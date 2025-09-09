import sqlite3
from datetime import datetime
from pathlib import Path

from .logger import logger
from .models import DownloadRecord


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
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
                    mode INTEGER DEFAULT 0               -- 0自动下载，1手动下载
                )
            """)
            conn.commit()

    def reset(self):
        """重置数据库"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DROP TABLE IF EXISTS downloads")
            conn.commit()
        self._init_db()

    def insert(self, record: DownloadRecord) -> int:
        """添加下载记录

        Args:
            title: 标题
            url: 链接
            download_url: 下载链接
            feed_name: RSS源名称
            feed_url: RSS源地址
            published_time: 发布时间
            downloader: 下载器名称
            status: 下载状态，0表示失败，1表示成功
            mode: 下载模式，0表示自动下载，1表示手动下载
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
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
                conn.commit()
                return cursor.lastrowid  # type: ignore

        except Exception as e:
            logger.error(f"添加下载记录失败: {e}")
            return 0

    def is_downloaded(self, url: str) -> bool:
        """检查URL是否已经下载过"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM downloads WHERE status = 1 and download_url = ?",
                (url,),
            )
            count = cursor.fetchone()[0]
            return count > 0

    def search_download_by_id(self, id: int) -> DownloadRecord | None:
        """通过ID获取下载记录"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM downloads WHERE id = ?", (id,))
            row = cursor.fetchone()
            return DownloadRecord.model_validate(dict(row)) if row else None

    def search_downloads(
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

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 获取总数
            count_query = (
                "SELECT COUNT(*) FROM ("
                + " ".join(query_parts).replace("SELECT *", "SELECT id")
                + ")"
            )
            cursor.execute(count_query, count_params)
            total_count = cursor.fetchone()[0]

            logger.debug(f"查询下载记录，SQL: {' '.join(query_parts)}, 参数: {params}")

            # 获取数据
            query_parts.append("ORDER BY download_time DESC LIMIT ? OFFSET ?")
            params.extend([limit, offset])
            cursor.execute(" ".join(query_parts), params)
            results = [
                DownloadRecord.model_validate(dict(row)) for row in cursor.fetchall()
            ]

            return results, total_count
