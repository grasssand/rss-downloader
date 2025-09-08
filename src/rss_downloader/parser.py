import re
from functools import lru_cache

import feedparser
from pydantic import HttpUrl, ValidationError

from .config import config
from .logger import logger
from .models import ENTRY_PARSER_MAP, ParsedItem


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

    def parse_feed(
        self, feed_name: str, feed_url: HttpUrl
    ) -> tuple[int, list[ParsedItem]]:
        """解析RSS源并返回总数和匹配的条目"""
        matched_items: list[ParsedItem] = []

        feed_config = config.get_feed_by_name(feed_name)
        extractor_type = feed_config.content_extractor if feed_config else "default"
        ParserModel: ParsedItem = ENTRY_PARSER_MAP.get(
            extractor_type, ENTRY_PARSER_MAP["default"]
        )

        logger.info(f"开始解析 RSS 源: {feed_name} ({feed_url})")
        feed = feedparser.parse(str(feed_url))

        if feed.bozo:
            logger.error(f"RSS 源解析错误，请检查 {feed_name}: {feed.bozo_exception}")
            if hasattr(feed, "debug_message"):
                logger.error(f"Debug 信息: {feed.debug_message}")
            return 0, []

        # 检查是否成功获取到feed
        if not feed.entries and not getattr(feed, "feed", None):
            logger.error(f"Feed 为空或无法访问 ({feed_url})")
            return 0, []

        logger.info(f"成功获取到 {len(feed.entries)} 个条目")

        for entry in feed.entries:
            try:
                # 调用 MikanEntry 等模型解析和验证
                parsed_item = ParserModel.parse_obj(entry)

                if self.match_filters(parsed_item.title, feed_name):
                    matched_items.append(parsed_item)

            except ValidationError as entry_error:
                logger.error(f"处理条目时发生错误: {entry_error}")
                continue

        logger.info(f"匹配到 {len(matched_items)} 个条目")

        return len(feed.entries), matched_items
