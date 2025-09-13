from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from httpx import Response
from pydantic import HttpUrl

from rss_downloader.config import ConfigManager
from rss_downloader.logger import LoggerProtocol
from rss_downloader.parser import RSSParser

pytestmark = pytest.mark.anyio

# 用于模拟 HTTP 响应的 RSS XML 示例
SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Test Feed</title>
  <item>
    <title>[Test] Success Case 1</title>
    <link>https://example.com/download/1.torrent</link>
    <guid>https://example.com/page/1</guid>
    <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
  </item>
  <item>
    <title>[Test] Filtered Case 2</title>
    <link>https://example.com/download/2.torrent</link>
    <guid>https://example.com/page/2</guid>
    <pubDate>Wed, 01 Jan 2025 00:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Invalid Case 3</title>
  </item>
</channel>
</rss>"""


async def test_match_filters_case_insensitive(
    test_config: ConfigManager, mock_logger: LoggerProtocol
):
    """测试过滤规则是否不区分大小写"""
    await test_config.update(
        {
            "aria2": {"rpc": "http://localhost:6800"},
            "feeds": [
                {
                    "name": "TestFeed",
                    "url": "http://test.com/rss.xml",
                    "include": ["1080P", "CHS"],
                    "exclude": ["720p", "Eng"],
                }
            ],
        }
    )
    parser = RSSParser(
        config=test_config,
        logger=mock_logger,
        http_client=AsyncMock(spec=httpx.AsyncClient),
    )

    assert parser.match_filters("[Anime] Episode 01 [1080p][chs]", "TestFeed")
    assert not parser.match_filters("[Anime] Episode 03 [1080p][eng]", "TestFeed")


async def test_match_filters_no_include_rules(
    test_config: ConfigManager, mock_logger: LoggerProtocol
):
    """测试在没有包含规则时，默认匹配所有"""
    await test_config.update(
        {
            "aria2": {"rpc": "http://localhost:6800"},
            "feeds": [
                {
                    "name": "AnotherFeed",
                    "url": "http://another.com/rss.xml",
                    "include": [],
                    "exclude": ["Test"],
                }
            ],
        }
    )
    parser = RSSParser(
        config=test_config,
        logger=mock_logger,
        http_client=AsyncMock(spec=httpx.AsyncClient),
    )
    assert parser.match_filters("This is a normal title", "AnotherFeed")
    assert not parser.match_filters("This is a Test title", "AnotherFeed")


@respx.mock
async def test_parse_feed_scenarios(
    test_config: ConfigManager, mock_logger: LoggerProtocol
):
    """全面测试 parse_feed 方法的各种场景"""
    feed_url = HttpUrl("http://test.com/rss")
    await test_config.update(
        {
            "aria2": {"rpc": "http://aria2"},
            "feeds": [
                {
                    "name": "TestFeed",
                    "url": str(feed_url),
                    "include": ["Success"],
                }
            ],
        }
    )

    async with httpx.AsyncClient() as http_client:
        parser = RSSParser(
            config=test_config, logger=mock_logger, http_client=http_client
        )

        # 场景1: 成功解析并匹配
        respx.get(str(feed_url)).mock(return_value=Response(200, text=SAMPLE_RSS_XML))
        total, matched = await parser.parse_feed("TestFeed", feed_url)
        assert total == 3
        assert len(matched) == 1
        assert matched[0].title == "[Test] Success Case 1"
        mock_logger.error.assert_called_once()  # 断言 Invalid Case 3 导致了一次解析错误
        mock_logger.reset_mock()

        # 场景2: Feed 解析出错 (bozo=1), 模拟一个无效的 XML
        respx.get(str(feed_url)).mock(return_value=Response(200, text="<rss><channel>"))
        total, matched = await parser.parse_feed("TestFeed", feed_url)
        assert total == 0
        assert len(matched) == 0
        mock_logger.error.assert_called_once()
        args, _ = mock_logger.error.call_args
        assert args[0].startswith("RSS 源解析错误，请检查 TestFeed:")
        mock_logger.reset_mock()

        # 场景3: Feed 内容为空
        respx.get(str(feed_url)).mock(return_value=Response(200, text=""))
        total, matched = await parser.parse_feed("TestFeed", feed_url)
        assert total == 0
        assert len(matched) == 0
        mock_logger.error.assert_called_with(f"Feed 为空或无法访问 ({feed_url})")
