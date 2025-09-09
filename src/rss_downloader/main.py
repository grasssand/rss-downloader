from datetime import datetime
from typing import Any, Literal

from pydantic import HttpUrl

from .config import ConfigManager
from .database import Database, DownloadRecord
from .downloaders import Aria2Client, QBittorrentClient
from .parser import RSSParser


class DownloaderError(Exception):
    pass


class ItemNotFoundError(Exception):
    pass


class RSSDownloader:
    def __init__(self, config: ConfigManager, database: Database, logger):
        self.config = config
        self.parser = RSSParser(config=config, logger=logger)
        self.db = database
        self.logger = logger
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
                logger=self.logger,
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
                    logger=self.logger,
                )
            except Exception as e:
                self.logger.error("初始化 qBittorrent 客户端失败，任务将无法下载。")
                raise ConnectionError(f"无法连接到 qBittorrent: {e}") from e
        else:
            self.qbittorrent = None

        if not self.aria2 and not self.qbittorrent:
            self.logger.warning("未配置任何下载器，无法下载内容")

    def _send_to_downloader(
        self,
        item: dict[str, Any],
        downloader: Literal["aria2", "qbittorrent"],
        mode: Literal[0, 1] = 0,
    ) -> None:
        """发送单个下载任务到指定下载器"""
        status = False
        error_message = ""

        try:
            if downloader == "aria2" and self.aria2:
                result = self.aria2.add_link(str(item["download_url"]))
                if "error" in result:
                    error_message = result["error"]
                else:
                    status = True
            elif downloader == "qbittorrent" and self.qbittorrent:
                success = self.qbittorrent.add_link(str(item["download_url"]))
                if success:
                    status = True
                else:
                    error_message = "qBittorrent API 返回失败"
            else:
                error_message = f"下载器 {downloader} 未配置或不可用"

        except Exception as e:
            self.logger.exception(f"与下载器 {downloader} 通信时发生意外错误")
            error_message = str(e)
            status = False

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
        self.logger.debug(f"新下载记录: {new_id} - {item['title']}")

        if status:
            self.logger.info(
                f"下载任务添加成功 ({downloader}): {new_id} - {item['title']}"
            )
        else:
            raise DownloaderError(f"任务添加失败 ({downloader}): {error_message}")

    def redownload(self, id: int, downloader: Literal["aria2", "qbittorrent"]) -> None:
        """重新下载指定 ID 的任务"""
        record = self.db.search_download_by_id(id)
        if not record:
            raise ItemNotFoundError(f"未找到 ID 为 {id} 的下载记录")

        if not record.download_url:
            raise ValueError(f"记录 ID 为 {id} 的下载记录没有下载链接")

        self._send_to_downloader(record.model_dump(), downloader, mode=1)

    def process_feed(self, feed_name: str, feed_url: HttpUrl) -> tuple[int, int, int]:
        """处理单个RSS源，返回总数，匹配条目数和下载数"""
        success = 0
        total_items, matched_items = self.parser.parse_feed(feed_name, feed_url)
        downloader = self.config.get_feed_downloader(feed_name)

        for item in matched_items:
            # 检查是否已经下载过
            if self.db.is_downloaded(str(item.download_url)):
                self.logger.info(f"跳过已下载项目: {item.title}")
                continue

            data = item.model_dump() | {"feed_name": feed_name, "feed_url": feed_url}

            try:
                # 添加下载任务
                self._send_to_downloader(data, downloader)
                success += 1
            except DownloaderError as e:
                self.logger.error(f"处理 '{item.title}' 失败: {e}")
            except Exception as e:
                self.logger.exception(f"下载时发生未知错误 (ID: {id}): {e}")

        return total_items, len(matched_items), success

    def run(self):
        """运行RSS下载器"""
        total_count = total_mathed = totle_success = 0
        try:
            for feed in self.config.feeds:
                self.logger.info(f"处理 RSS 源: {feed.name} ({feed.url})")
                count, matched, success = self.process_feed(feed.name, feed.url)
                total_count += count
                total_mathed += matched
                totle_success += success

        except Exception as e:
            self.logger.error(f"运行时发生错误: {e}")
        finally:
            self.logger.info(
                f"共获取到 {total_count} 个条目，"
                f"匹配到 {total_mathed} 个条目。"
                f"成功添加 {totle_success} 个下载任务。"
            )
