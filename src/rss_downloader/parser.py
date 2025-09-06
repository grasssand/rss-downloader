import re
import time
from datetime import datetime
from functools import lru_cache
from typing import Any

import feedparser
from pydantic import HttpUrl

from .config import config
from .logger import logger


class RSSParser:
    @lru_cache(maxsize=32)
    def _get_patterns_for_feed(
        self, feed_name: str, config_version: int
    ) -> tuple[list[re.Pattern], list[re.Pattern]]:
        """获取并编译指定RSS源的过滤规则"""
        logger.debug(f"编译过滤规则: {feed_name} (version {config_version})")

        include_patterns, exclude_patterns = config.get_feed_patterns(feed_name)
        include_compiled = [re.compile(pattern) for pattern in include_patterns]
        exclude_compiled = [re.compile(pattern) for pattern in exclude_patterns]

        return include_compiled, exclude_compiled

    def _extract_download_url(
        self, entry: Any, extractor_type: str
    ) -> tuple[str | None, str | None]:
        """根据提取器类型查找下载链接"""
        url = download_url = None

        # Mikan 处理逻辑
        if extractor_type in ["mikan", "dmhy"] and hasattr(entry, "links"):
            for link in entry.links:
                if link.get("type") in ["application/x-bittorrent"]:
                    url = entry.link if hasattr(entry, "link") else None
                    download_url = link.get("href")
                    break

        # Nyaa 处理逻辑
        elif extractor_type in ["nyaa"] and hasattr(entry, "link"):
            url = entry.id if hasattr(entry, "id") else None
            download_url = entry.link if hasattr(entry, "link") else None

        # 默认处理逻辑
        else:
            url = entry.id if hasattr(entry, "id") else None
            download_url = entry.link if hasattr(entry, "link") else None

        return url, download_url

    def match_filters(self, title: str, feed_name: str) -> bool:
        """检查标题是否匹配指定源的过滤规则"""
        # 获取当前配置版本以确保缓存正确
        current_version = config.get_config_version()
        include_patterns, exclude_patterns = self._get_patterns_for_feed(
            feed_name, current_version
        )

        # 如果没有包含规则，则默认匹配
        if not include_patterns:
            is_included = True
        else:
            is_included = any(pattern.search(title) for pattern in include_patterns)

        # 如果匹配任何排除规则，则返回False
        if any(pattern.search(title) for pattern in exclude_patterns):
            return False

        return is_included

    def parse_feed(self, feed_name: str, feed_url: HttpUrl) -> list[dict[str, Any]]:
        """解析RSS源并返回匹配的条目"""
        try:
            logger.info(f"开始解析 RSS 源: {feed_name} ({feed_url})")
            feed = feedparser.parse(str(feed_url))

            if feed.bozo:
                logger.error(
                    f"{feed_name} 源解析错误，请检查 RSS 源: {feed.bozo_exception}"
                )
                if hasattr(feed, "debug_message"):
                    logger.error(f"Debug 信息: {feed.debug_message}")
                return []

            # 检查是否成功获取到feed
            if not feed.entries and not getattr(feed, "feed", None):
                logger.error(f"Feed 为空或无法访问 ({feed_url})")
                return []

            logger.info(f"成功获取到 {len(feed.entries)} 个条目")
            matched_items = []
            for entry in feed.entries:
                try:
                    title = str(entry.title) if hasattr(entry, "title") else None
                    url = download_url = None
                    # 下载链接适用于Mikan、Nyaa...
                    if hasattr(entry, "links"):
                        for link in entry.links:
                            if link.get("type") in ["application/x-bittorrent"]:
                                url = entry.link if hasattr(entry, "link") else None
                                download_url = link.get("href")
                                break
                        else:
                            url = entry.id if hasattr(entry, "id") else None
                            download_url = (
                                entry.link if hasattr(entry, "link") else None
                            )

                    if title and self.match_filters(title, feed_name):
                        item = {
                            "title": title,
                            "url": url,
                            "download_url": download_url,
                            "feed_name": feed_name,
                            "feed_url": str(feed_url),
                            "published_time": datetime.fromtimestamp(
                                time.mktime(
                                    entry.published_parsed,  # type: ignore
                                )
                            )
                            if hasattr(entry, "published_parsed")
                            else datetime.now(),
                        }
                        matched_items.append(item)
                except Exception as entry_error:
                    logger.error(f"处理条目时发生错误: {entry_error}")
                    continue

            logger.info(f"匹配到 {len(matched_items)} 个条目")
            return matched_items

        except Exception as e:
            logger.error(f"解析 {feed_name} ({feed_url}) 时发生错误: {e}")
            return []
