from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import HttpUrl

from rss_downloader.database import Database
from rss_downloader.models import DownloadRecord

pytestmark = pytest.mark.anyio


async def test_db_insert_and_is_downloaded(test_db: Database):
    """测试插入记录和检查是否已下载的功能"""
    assert not await test_db.is_downloaded("http://example.com/item1.torrent")

    record = DownloadRecord(
        title="Test Item 1",
        url=HttpUrl("http://example.com/page1"),
        download_url="http://example.com/item1.torrent",
        feed_name="TestFeed",
        feed_url=HttpUrl("http://example.com/feed.xml"),
        published_time=datetime.now(),
        download_time=datetime.now(),
        downloader="aria2",
        status=1,  # 成功
        mode=0,
    )
    await test_db.insert(record)

    assert await test_db.is_downloaded("http://example.com/item1.torrent")

    # 插入一条失败的记录
    record_failed = record.model_copy(
        update={"download_url": "http://example.com/item2.torrent", "status": 0}
    )
    await test_db.insert(record_failed)
    assert not await test_db.is_downloaded("http://example.com/item2.torrent")


async def test_db_insert_failure(test_db: Database, monkeypatch):
    """测试当数据库写入失败时，insert 方法是否能正确捕获异常"""
    record = DownloadRecord(
        title="Test Item Fail",
        url=HttpUrl("http://example.com/page_fail"),
        download_url="http://example.com/item_fail.torrent",
        feed_name="TestFeed",
        feed_url=HttpUrl("http://example.com/feed.xml"),
        published_time=datetime.now(),
        download_time=datetime.now(),
        downloader="aria2",
        status=1,
        mode=0,
    )

    monkeypatch.setattr(
        "aiosqlite.Cursor.execute",
        AsyncMock(side_effect=RuntimeError("模拟数据库错误")),
    )

    return_value = await test_db.insert(record)

    assert return_value == 0
    test_db.logger.error.assert_called_once()
    call_args, _ = test_db.logger.error.call_args
    assert "添加下载记录失败" in call_args[0]


async def test_db_search_by_id(test_db: Database):
    """测试数据库的按 id 查找"""
    record = DownloadRecord(
        title="Test Item 123",
        url=HttpUrl("http://example.com/123"),
        download_url="http://example.com/123.torrent",
        feed_name="TestFeed",
        feed_url=HttpUrl("http://example.com/feed.xml"),
        published_time=datetime.now(),
        download_time=datetime.now(),
        downloader="aria2",
        status=1,
        mode=0,
    )
    record_id = await test_db.insert(record)
    result = await test_db.search_download_by_id(record_id)

    assert result.id == record_id
    assert result.title == "Test Item 123"


async def test_db_search(test_db: Database):
    """测试数据库的搜索功能"""
    now = datetime.now()
    records = [
        DownloadRecord(
            title="Aria2 Success Auto",
            downloader="aria2",
            status=1,
            mode=0,
            download_time=now,
            published_time=now,
            url=HttpUrl("http://a"),
            download_url="http://a/dl",
            feed_name="testA",
            feed_url=HttpUrl("http://a/f"),
        ),
        DownloadRecord(
            title="Aria2 Fail Manual",
            downloader="aria2",
            status=0,
            mode=1,
            download_time=datetime(2024, 1, 1),
            published_time=datetime(2024, 1, 1),
            url=HttpUrl("http://b"),
            download_url="http://b/dl",
            feed_name="testA",
            feed_url=HttpUrl("http://a/f"),
        ),
        DownloadRecord(
            title="qB Success Auto",
            downloader="qbittorrent",
            status=1,
            mode=0,
            download_time=now,
            published_time=now,
            url=HttpUrl("http://c"),
            download_url="http://c/dl",
            feed_name="testB",
            feed_url=HttpUrl("http://b/f"),
        ),
    ]
    for r in records:
        await test_db.insert(r)

    # 测试无条件搜索
    results, total = await test_db.search_downloads()
    assert total == 3
    assert len(results) == 3

    # 测试按标题搜索
    results, total = await test_db.search_downloads(title="Aria2")
    assert total == 2

    # 测试按RSS源名称搜索
    results, total = await test_db.search_downloads(feed_name="testA")
    assert total == 2

    # 测试按下载器搜索
    results, total = await test_db.search_downloads(downloader="qbittorrent")
    assert total == 1
    assert results[0].title == "qB Success Auto"

    # 测试按状态和模式组合搜索
    results, total = await test_db.search_downloads(status=1, mode=0)
    assert total == 2

    # 测试按时间搜索
    results, total = await test_db.search_downloads(
        published_start_time=datetime(2024, 1, 1),
        published_end_time=datetime(2024, 1, 2),
        download_start_time=datetime(2024, 1, 1),
        download_end_time=datetime(2024, 1, 2),
    )
    assert total == 1
    assert results[0].title == "Aria2 Fail Manual"
