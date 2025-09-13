from datetime import datetime
from typing import Any, Literal

import anyio
from pydantic import HttpUrl

from .config import ConfigManager
from .database import Database
from .downloaders import Aria2Client, QBittorrentClient
from .logger import LoggerProtocol
from .models import Downloader, DownloadRecord
from .parser import RSSParser


class DownloaderError(Exception):
    pass


class ItemNotFoundError(Exception):
    pass


class RSSDownloader:
    def __init__(
        self,
        config: ConfigManager,
        database: Database,
        logger: LoggerProtocol,
        parser: RSSParser,
        aria2: Aria2Client | None,
        qbittorrent: QBittorrentClient | None,
    ):
        self.config = config
        self.db = database
        self.logger = logger
        self.parser = parser
        self.aria2 = aria2
        self.qbittorrent = qbittorrent

        if not self.aria2 and not self.qbittorrent:
            self.logger.warning("未配置任何下载器，无法下载内容")

    async def _send_to_downloader(
        self,
        item: dict[str, Any],
        downloader_name: Downloader,
        mode: Literal[0, 1] = 0,
    ) -> None:
        """发送单个下载任务到指定下载器"""
        status = False
        error_message = ""
        downloader_client = None

        try:
            if downloader_name == "aria2":
                downloader_client = self.aria2
                if downloader_client:
                    result = await downloader_client.add_link(str(item["download_url"]))
                    if "error" in result:
                        error_message = result["error"]
                    else:
                        status = True
                else:
                    error_message = "下载器 aria2 未配置或不可用"
            elif downloader_name == "qbittorrent":
                downloader_client = self.qbittorrent
                if downloader_client:
                    if await downloader_client.add_link(str(item["download_url"])):
                        status = True
                else:
                    error_message = "下载器 qbittorrent 未配置或不可用"
            else:
                error_message = f"未知的下载器类型: {downloader_name}"

        except Exception as e:
            self.logger.exception(f"与下载器 {downloader_name} 通信时发生意外错误")
            error_message = str(e)
            status = False

        record = DownloadRecord(
            title=item["title"],
            url=item["url"],
            download_url=item["download_url"],
            feed_name=item["feed_name"],
            feed_url=item["feed_url"],
            published_time=item["published_time"],
            download_time=datetime.now(),
            downloader=downloader_name,
            status=1 if status else 0,
            mode=mode,
        )
        new_id = await self.db.insert(record)

        if status:
            self.logger.info(
                f"下载任务添加成功 ({downloader_name}): {new_id} - {item['title']}"
            )
        else:
            self.logger.debug(
                f"下载失败记录已创建 ({downloader_name}): {new_id} - {item['title']}"
            )
            raise DownloaderError(f"任务添加失败 ({downloader_name}): {error_message}")

    async def redownload(self, id: int, downloader: Downloader) -> None:
        """重新下载指定 ID 的任务"""
        record = await self.db.search_download_by_id(id)
        if not record:
            raise ItemNotFoundError(f"未找到 ID 为 {id} 的下载记录")

        if not record.download_url:
            raise ValueError(f"记录 ID 为 {id} 的下载记录没有下载链接")

        await self._send_to_downloader(record.model_dump(), downloader, mode=1)

    async def process_feed(
        self, feed_name: str, feed_url: HttpUrl
    ) -> tuple[int, int, int]:
        """处理单个RSS源，返回总数，匹配条目数和下载数"""
        success = 0
        total_items, matched_items = await self.parser.parse_feed(feed_name, feed_url)
        downloader_name = self.config.get_feed_downloader(feed_name)

        for item in matched_items:
            if await self.db.is_downloaded(str(item.download_url)):
                self.logger.info(f"跳过已下载项目: {item.title}")
                continue

            data = item.model_dump() | {"feed_name": feed_name, "feed_url": feed_url}

            try:
                await self._send_to_downloader(data, downloader_name)
                success += 1
            except DownloaderError as e:
                self.logger.error(f"处理失败 '{item.title}' : {e}")
            except Exception:
                self.logger.exception(f"下载时发生未知错误: {item.title}")

        return total_items, len(matched_items), success

    async def run(self):
        """并发方式运行RSS下载器"""
        total_count = total_mathed = totle_success = 0
        results = []

        async def process_and_collect(feed_name: str, feed_url: HttpUrl):
            self.logger.info(f"处理 RSS 源: {feed_name} ({feed_url})")
            result_tuple = await self.process_feed(feed_name, feed_url)
            results.append(result_tuple)

        try:
            async with anyio.create_task_group() as tg:
                for feed in self.config.feeds:
                    tg.start_soon(process_and_collect, feed.name, feed.url)

            total_count = sum(r[0] for r in results)
            total_mathed = sum(r[1] for r in results)
            totle_success = sum(r[2] for r in results)

        except Exception as e:
            self.logger.error(f"运行时发生错误: {e}")
            total_count = total_mathed = totle_success = 0
        finally:
            self.logger.info(
                f"共获取到 {total_count} 个条目，"
                f"匹配到 {total_mathed} 个条目，"
                f"成功添加 {totle_success} 个下载任务。"
            )
