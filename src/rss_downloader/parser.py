import re
from functools import lru_cache

import feedparser
import httpx
from pydantic import HttpUrl, ValidationError

from .config import ConfigManager
from .logger import LoggerProtocol
from .models import ENTRY_PARSER_MAP, ParsedItem


@lru_cache(maxsize=32)
def _compile_patterns(
    feed_name: str,
    config_version: int,
    patterns: tuple[str, ...],
    logger: LoggerProtocol,
) -> list[re.Pattern]:
    """获取并编译指定RSS源的过滤规则"""
    logger.debug(f"编译过滤规则: {feed_name} (version {config_version})")
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


class RSSParser:
    def __init__(
        self,
        config: ConfigManager,
        logger: LoggerProtocol,
        http_client: httpx.AsyncClient,
    ):
        self.config = config
        self.logger = logger
        self.http_client = http_client

    def match_filters(self, title: str, feed_name: str) -> bool:
        """检查标题是否匹配指定源的过滤规则"""
        # 获取当前配置版本以确保缓存正确
        current_version = self.config.get_config_version()
        include_patterns, exclude_patterns = self.config.get_feed_patterns(feed_name)
        include_compiled = _compile_patterns(
            feed_name, current_version, tuple(include_patterns), self.logger
        )
        exclude_compiled = _compile_patterns(
            feed_name, current_version, tuple(exclude_patterns), self.logger
        )

        if not include_compiled:
            is_included = True
        else:
            is_included = any(pattern.search(title) for pattern in include_compiled)

        if any(pattern.search(title) for pattern in exclude_compiled):
            return False

        return is_included

    async def parse_feed(
        self, feed_name: str, feed_url: HttpUrl
    ) -> tuple[int, list[ParsedItem]]:
        """异步解析RSS源并返回总数和匹配的条目"""
        matched_items: list[ParsedItem] = []

        feed_config = self.config.get_feed_by_name(feed_name)
        extractor_type = feed_config.content_extractor if feed_config else "default"
        ParserModel = ENTRY_PARSER_MAP.get(extractor_type, ENTRY_PARSER_MAP["default"])

        self.logger.info(f"开始获取 RSS 源: {feed_name}")
        try:
            response = await self.http_client.get(
                str(feed_url),
                follow_redirects=True,
                timeout=30,
            )
            response.raise_for_status()
            feed_content = response.text
        except Exception as e:
            self.logger.error(f"获取 RSS 源时发生网络错误 ({feed_name}): {e}")
            return 0, []

        self.logger.info(f"开始解析 RSS 源: {feed_name}")
        feed = feedparser.parse(feed_content)

        if feed.bozo:
            self.logger.error(
                f"RSS 源解析错误，请检查 {feed_name}: {feed.bozo_exception}"
            )
            if hasattr(feed, "debug_message"):
                self.logger.error(f"Debug 信息: {feed.debug_message}")
            return 0, []

        # 检查是否成功获取到feed
        if not feed.entries and not getattr(feed, "feed", None):
            self.logger.error(f"Feed 为空或无法访问 ({feed_url})")
            return 0, []

        self.logger.info(f"{feed_name}: 获取到 {len(feed.entries)} 个条目")

        for entry in feed.entries:
            try:
                # 调用 MikanEntry 等模型解析和验证
                parsed_item = ParserModel.model_validate(entry)

                if self.match_filters(parsed_item.title, feed_name):
                    matched_items.append(parsed_item)
                    self.logger.info(f"匹配成功: {parsed_item.title}")
                else:
                    self.logger.warning(f"匹配失败: {parsed_item.title}")

            except ValidationError as entry_error:
                self.logger.error(f"处理条目时发生错误: {entry_error}")
                continue

        self.logger.info(f"{feed_name}: 匹配到 {len(matched_items)} 个条目")
        return len(feed.entries), matched_items
