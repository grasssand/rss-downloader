from datetime import datetime
from typing import Any, Literal

from pydantic import HttpUrl

from .config import config
from .database import DownloadRecord, db
from .downloaders import Aria2Client, QBittorrentClient
from .logger import logger
from .parser import RSSParser


class RSSDownloader:
    def __init__(self):
        self.config = config
        self.parser = RSSParser()
        self.db = db
        self._setup_downloaders()

    def _setup_downloaders(self):
        """初始化下载器"""
        # 初始化 Aria2 客户端
        aria2_config = self.config.aria2
        if aria2_config and aria2_config.rpc:
            self.aria2 = Aria2Client(
                rpc_url=str(aria2_config.rpc),
                secret=aria2_config.secret,
                dir=aria2_config.dir,
            )
        else:
            self.aria2 = None

        # 初始化 qBittorrent 客户端
        qb_config = self.config.qbittorrent
        if qb_config and qb_config.host:
            try:
                self.qbittorrent = QBittorrentClient(
                    host=str(qb_config.host),
                    username=qb_config.username,
                    password=qb_config.password,
                )
            except Exception as e:
                logger.warning(f"初始化 qBittorrent 客户端失败，错误: {e}")
                self.qbittorrent = None
        else:
            self.qbittorrent = None

        if not self.aria2 and not self.qbittorrent:
            logger.warning("未配置任何下载器，无法下载内容")

    def _send_to_downloader(
        self,
        item: dict[str, Any],
        downloader: Literal["aria2", "qbittorrent"],
        mode: Literal[0, 1] = 0,
    ) -> bool:
        """发送单个下载任务到指定下载器"""
        status = False

        if downloader == "aria2" and self.aria2:
            result = self.aria2.add_link(item["download_url"])
            if "error" not in result:
                status = True

        elif downloader == "qbittorrent" and self.qbittorrent:
            success = self.qbittorrent.add_link(item["download_url"])
            if success:
                status = True

        else:
            logger.error(f"未配置 {downloader}，无法添加任务")

        if status:
            logger.info(f"任务添加成功 ({downloader}): {item['title']}")
        else:
            logger.error(f"任务添加失败 ({downloader}): {item['title']}")

        # 记录到数据库
        record = DownloadRecord(
            title=item["title"],
            url=item["url"],
            download_url=item["download_url"],
            feed_name=item["feed_name"],
            feed_url=item["feed_url"],
            published_time=item["published_time"],
            download_time=datetime.now(),
            downloader=downloader,
            status=1 if status else 0,
            mode=mode,
        )
        new_id = self.db.insert(record)
        logger.debug(f"下载新记录: {new_id} - {item['title']}")

        return status

    def redownload(self, id: int, downloader: Literal["aria2", "qbittorrent"]) -> bool:
        """重新下载指定 ID 的任务"""
        record = self.db.search_download_by_id(id)
        if not record:
            logger.error(f"未找到 ID 为 {id} 的下载记录")
            return False

        if not record.download_url:
            logger.error(f"记录 ID 为 {id} 的下载记录没有下载链接")
            return False

        status = self._send_to_downloader(record.model_dump(), downloader, mode=1)
        return status

    def process_feed(self, feed_name: str, feed_url: HttpUrl) -> int:
        """处理单个RSS源"""
        items = self.parser.parse_feed(feed_name, feed_url)
        count = 0

        # 获取当前源的信息
        downloader = self.config.get_feed_downloader(feed_name)

        for item in items:
            if not item.get("download_url"):
                continue

            # 检查是否已经下载过
            if self.db.is_downloaded(item["download_url"]):
                logger.info(f"跳过已下载项目: {item['title']}")
                continue

            # 添加下载任务
            status = self._send_to_downloader(item, downloader)

            if status:
                count += 1

        return count

    def run(self):
        """运行RSS下载器"""
        total_items = 0
        try:
            for feed in self.config.feeds:
                logger.info(f"处理 RSS 源: {feed.name} ({feed.url})")
                count = self.process_feed(feed.name, feed.url)
                total_items += count

        except Exception as e:
            logger.error(f"运行时发生错误: {e}")
        finally:
            logger.info(f"处理完成，共添加 {total_items} 个下载任务")


rss_downloader = RSSDownloader()
