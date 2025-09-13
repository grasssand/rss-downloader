import sqlite3
from datetime import datetime

import aiosqlite
from anyio import Path

from .logger import LoggerProtocol
from .models import DownloadRecord


def adapt_datetime(dt_obj: datetime) -> str:
    """将 datetime 对象转换为 ISO 8601 格式的字符串以便存储。"""
    return dt_obj.isoformat()


def convert_datetime(iso_str: bytes) -> datetime:
    """将从数据库读取的 ISO 8601 格式字符串转换回 datetime 对象。"""
    return datetime.fromisoformat(iso_str.decode())


# https://docs.python.org/3/library/sqlite3.html#sqlite3-adapter-converter-recipes
aiosqlite.register_adapter(datetime, adapt_datetime)
aiosqlite.register_converter("TIMESTAMP", convert_datetime)


class Database:
    def __init__(self, db_path: Path, logger: LoggerProtocol):
        self.db_path = db_path
        self.logger = logger

    @classmethod
    async def create(cls, db_path: Path, logger: LoggerProtocol) -> "Database":
        """创建并初始化一个 Database 实例"""
        instance = cls(db_path, logger)
        await instance._init_db()
        return instance

    async def _init_db(self):
        """初始化数据库表"""
        async with aiosqlite.connect(
            self.db_path,  # type: ignore
            detect_types=sqlite3.PARSE_DECLTYPES,
        ) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,                -- 标题
                    url TEXT NOT NULL,                  -- 链接
                    download_url TEXT NOT NULL,         -- 下载链接
                    feed_name TEXT NOT NULL,            -- RSS源名称
                    feed_url TEXT NOT NULL,             -- RSS源地址
                    published_time TIMESTAMP NOT NULL,  -- 发布时间
                    download_time TIMESTAMP NOT NULL,   -- 下载时间
                    downloader TEXT NOT NULL,           -- 下载器名称, aira2/qbittorrent
                    status INTEGER NOT NULL,            -- 0失败，1成功
                    mode INTEGER DEFAULT 0              -- 0自动下载，1手动下载
                )
            """)
            await conn.commit()

    async def reset(self):
        """重置数据库"""
        async with aiosqlite.connect(
            self.db_path,  # type: ignore
            detect_types=sqlite3.PARSE_DECLTYPES,
        ) as conn:
            await conn.execute("DROP TABLE IF EXISTS downloads")
            await conn.commit()
        await self._init_db()

    async def insert(self, record: DownloadRecord) -> int:
        """添加下载记录"""
        try:
            async with aiosqlite.connect(
                self.db_path,  # type: ignore
                detect_types=sqlite3.PARSE_DECLTYPES,
            ) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO downloads (
                            title, url, download_url, feed_name, feed_url,
                            published_time, download_time, downloader, status, mode
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.title,
                            str(record.url),
                            str(record.download_url),
                            record.feed_name,
                            str(record.feed_url),
                            record.published_time,
                            record.download_time,
                            record.downloader,
                            record.status,
                            record.mode,
                        ),
                    )
                    await conn.commit()
                    return cursor.lastrowid  # type: ignore

        except Exception as e:
            self.logger.error(f"添加下载记录失败: {e}")
            return 0

    async def is_downloaded(self, url: str) -> bool:
        """检查URL是否已经下载过"""
        async with aiosqlite.connect(
            self.db_path,  # type: ignore
            detect_types=sqlite3.PARSE_DECLTYPES,
        ) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) FROM downloads WHERE status = 1 and download_url = ?",
                    (url,),
                )
                result = await cursor.fetchone()
                return result[0] > 0 if result else False

    async def search_download_by_id(self, id: int) -> DownloadRecord | None:
        """通过ID获取下载记录"""
        async with aiosqlite.connect(
            self.db_path,  # type: ignore
            detect_types=sqlite3.PARSE_DECLTYPES,
        ) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.cursor() as cursor:
                await cursor.execute("SELECT * FROM downloads WHERE id = ?", (id,))
                row = await cursor.fetchone()
                return DownloadRecord.model_validate(dict(row)) if row else None

    async def search_downloads(
        self,
        title: str | None = None,
        feed_name: str | None = None,
        downloader: str | None = None,
        status: int | None = None,
        mode: int | None = None,
        published_start_time: datetime | None = None,
        published_end_time: datetime | None = None,
        download_start_time: datetime | None = None,
        download_end_time: datetime | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[DownloadRecord], int]:
        """搜索下载记录"""
        query_parts = ["SELECT * FROM downloads WHERE 1=1"]
        params = []

        if title:
            query_parts.append("AND title LIKE ?")
            params.append(f"%{title}%")
        if feed_name:
            query_parts.append("AND feed_name LIKE ?")
            params.append(f"%{feed_name}%")
        if downloader:
            query_parts.append("AND downloader = ?")
            params.append(downloader)
        if status is not None:
            query_parts.append("AND status = ?")
            params.append(status)
        if mode is not None:
            query_parts.append("AND mode = ?")
            params.append(mode)
        if published_start_time:
            query_parts.append("AND published_time >= ?")
            params.append(published_start_time)
        if published_end_time:
            query_parts.append("AND published_time <= ?")
            params.append(published_end_time)
        if download_start_time:
            query_parts.append("AND download_time >= ?")
            params.append(download_start_time)
        if download_end_time:
            query_parts.append("AND download_time <= ?")
            params.append(download_end_time)

        count_params = list(params)

        async with aiosqlite.connect(
            self.db_path,  # type: ignore
            detect_types=sqlite3.PARSE_DECLTYPES,
        ) as conn:
            conn.row_factory = aiosqlite.Row

            # 获取总数
            async with conn.cursor() as cursor:
                count_query = (
                    "SELECT COUNT(*) FROM ("
                    + " ".join(query_parts).replace("SELECT *", "SELECT id")
                    + ")"
                )
                await cursor.execute(count_query, count_params)
                total_count_result = await cursor.fetchone()
                total_count = total_count_result[0] if total_count_result else 0

            self.logger.debug(
                f"查询下载记录，SQL: {' '.join(query_parts)}, 参数: {params}"
            )

            # 获取数据
            async with conn.cursor() as cursor:
                query_parts.append("ORDER BY download_time DESC LIMIT ? OFFSET ?")
                params.extend([limit, offset])
                await cursor.execute(" ".join(query_parts), params)
                rows = await cursor.fetchall()
                results = [DownloadRecord.model_validate(dict(row)) for row in rows]

            return results, total_count
