from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import HttpUrl

from rss_downloader.config import ConfigManager
from rss_downloader.database import Database
from rss_downloader.logger import LoggerProtocol
from rss_downloader.main import DownloaderError, ItemNotFoundError, RSSDownloader
from rss_downloader.models import DownloadRecord, ParsedItem
from rss_downloader.parser import RSSParser

pytestmark = pytest.mark.anyio


async def test_rss_downloader_run_flow(
    test_config: ConfigManager, test_db: Database, mock_logger: LoggerProtocol
):
    """测试一次完整的 run() 流程。"""
    feed_url = "http://test.com/rss.xml"
    await test_config.update(
        {
            "aria2": {"rpc": "http://fake-aria2"},
            "feeds": [{"name": "TestFeed", "url": feed_url, "downloader": "aria2"}],
        }
    )

    mock_parser = MagicMock(spec=RSSParser)
    item1 = ParsedItem(
        title="New Episode 1",
        url=HttpUrl("http://item/1"),
        download_url=HttpUrl("http://download/1"),
        published_time=datetime.now(),
    )
    item2 = ParsedItem(
        title="Already Downloaded",
        url=HttpUrl("http://item/2"),
        download_url=HttpUrl("http://download/2"),
        published_time=datetime.now(),
    )
    item3 = ParsedItem(
        title="Download Fails",
        url=HttpUrl("http://item/3"),
        download_url=HttpUrl("http://download/3"),
        published_time=datetime.now(),
    )
    mock_parser.parse_feed = AsyncMock(return_value=(3, [item1, item2, item3]))

    mock_aria2_client = AsyncMock()

    async def mock_add_link(url, **kwargs):
        if url == str(item3.download_url):
            return {"error": "Fake RPC error"}
        return {"result": "gid1"}

    mock_aria2_client.add_link = AsyncMock(side_effect=mock_add_link)

    # 在数据库中预先插入一条已下载记录
    pre_record = DownloadRecord(
        title="Pre-Downloaded",
        url=item2.url,
        download_url=item2.download_url,
        feed_name="TestFeed",
        feed_url=HttpUrl(feed_url),
        published_time=datetime.now(),
        download_time=datetime.now(),
        status=1,
        mode=0,
        downloader="aria2",
    )
    await test_db.insert(pre_record)

    downloader = RSSDownloader(
        config=test_config,
        database=test_db,
        logger=mock_logger,
        parser=mock_parser,
        aria2=mock_aria2_client,
        qbittorrent=None,
        transmission=None,
    )

    await downloader.run()

    # 验证数据库中的最终状态
    results, total = await test_db.search_downloads(limit=100)
    assert total == 3  # 1 (pre-record) + 1 (new success) + 1 (new fail)

    results_by_title = {r.title: r for r in results}
    assert (
        "New Episode 1" in results_by_title
        and results_by_title["New Episode 1"].status == 1
    )
    assert (
        "Download Fails" in results_by_title
        and results_by_title["Download Fails"].status == 0
    )


async def test_run_with_processing_error(
    test_config: ConfigManager, test_db: Database, mock_logger: LoggerProtocol
):
    """测试 run 方法在 process_feed 中发生未知异常时，是否能捕获并记录日志"""
    await test_config.update(
        {
            "aria2": {"rpc": "http://fake-aria2"},
            "feeds": [
                {"name": "TestFeed", "url": "http://test.com/rss.xml"},
            ],
        }
    )

    mock_parser = MagicMock(spec=RSSParser)
    mock_parser.parse_feed = AsyncMock(side_effect=Exception("Something went wrong!"))

    downloader = RSSDownloader(
        config=test_config,
        database=test_db,
        logger=mock_logger,
        parser=mock_parser,
        aria2=AsyncMock(),
        qbittorrent=None,
        transmission=None,
    )

    # run() 不应因内部异常而崩溃
    await downloader.run()

    downloader.logger.error.assert_called_once()
    args, _ = downloader.logger.error.call_args
    assert "运行时发生错误" in args[0]


async def test_send_to_downloader_not_configured(
    test_config: ConfigManager, test_db: Database, mock_logger: LoggerProtocol
):
    downloader = RSSDownloader(
        config=test_config,
        database=test_db,
        logger=mock_logger,
        parser=MagicMock(),
        aria2=None,
        qbittorrent=None,
        transmission=None,
    )
    item_data = {
        "title": "Test",
        "url": "http://a",
        "download_url": "http://a/dl",
        "feed_name": "a",
        "feed_url": "http://a/rss",
        "published_time": datetime.now(),
    }

    with pytest.raises(DownloaderError, match="下载器 aria2 未配置或不可用"):
        await downloader._send_to_downloader(item_data, downloader_name="aria2")

    with pytest.raises(DownloaderError, match="下载器 qbittorrent 未配置或不可用"):
        await downloader._send_to_downloader(item_data, downloader_name="qbittorrent")

    with pytest.raises(DownloaderError, match="下载器 transmission 未配置或不可用"):
        await downloader._send_to_downloader(item_data, downloader_name="transmission")


async def test_redownload_success(
    test_config: ConfigManager, test_db: Database, mock_logger: LoggerProtocol
):
    """测试 redownload 方法成功"""
    fake_record = DownloadRecord(
        id=123,
        title="Found Episode",
        url=HttpUrl("http://item/123"),
        download_url=HttpUrl("http://download/123"),
        feed_name="TestFeed",
        feed_url=HttpUrl("http://test.com/rss.xml"),
        published_time=datetime.now(),
        download_time=datetime.now(),
        status=0,
        mode=0,
        downloader="aria2",
    )

    test_db.search_download_by_id = AsyncMock(return_value=fake_record)

    downloader = RSSDownloader(
        config=test_config,
        database=test_db,
        logger=mock_logger,
        parser=MagicMock(),
        aria2=AsyncMock(),
        qbittorrent=None,
        transmission=None,
    )
    downloader._send_to_downloader = AsyncMock()

    await downloader.redownload(id=123, downloader="aria2")

    test_db.search_download_by_id.assert_awaited_once_with(123)
    downloader._send_to_downloader.assert_awaited_once_with(
        fake_record.model_dump(),
        "aria2",
        mode=1,
    )


async def test_redownload_item_failure(
    test_config: ConfigManager, test_db: Database, mock_logger: LoggerProtocol
):
    """测试 redownload 方法失败"""
    test_db.search_download_by_id = AsyncMock(return_value=None)
    downloader = RSSDownloader(
        config=test_config,
        database=test_db,
        logger=mock_logger,
        parser=MagicMock(),
        aria2=AsyncMock(),
        qbittorrent=None,
        transmission=None,
    )

    with pytest.raises(ItemNotFoundError):
        await downloader.redownload(id=999, downloader="aria2")
    test_db.search_download_by_id.assert_awaited_once_with(999)

    fake_record = AsyncMock(spec=DownloadRecord)
    fake_record.download_url = None
    test_db.search_download_by_id.return_value = fake_record

    with pytest.raises(ValueError, match="没有下载链接"):
        await downloader.redownload(id=1, downloader="aria2")
