from datetime import datetime, time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from rss_downloader.main import DownloaderError, ItemNotFoundError
from rss_downloader.models import Config
from rss_downloader.services import AppServices
from rss_downloader.web import SearchFilters, format_datetime

pytestmark = pytest.mark.anyio


def test_format_datetime_filter():
    """测试 Jinja2 的 format_datetime 过滤器"""

    dt = datetime(2025, 9, 11, 8, 30, 0)
    assert format_datetime(dt) == "2025-09-11 08:30:00"
    assert format_datetime(dt, "%Y/%m/%d") == "2025/09/11"
    assert format_datetime(None) == ""


def test_search_filters_date_range_fix():
    """测试 SearchFilters 模型校验器是否能自动修正日期范围"""

    # 开始日期晚于结束日期，应被交换
    filters = SearchFilters(
        published_start_time=datetime(2025, 9, 11),
        published_end_time=datetime(2025, 9, 10),
        download_start_time=datetime(2025, 9, 11),
        download_end_time=datetime(2025, 9, 10),
    )
    assert filters.published_start_time == datetime(2025, 9, 10)
    assert filters.published_end_time == datetime.combine(
        datetime(2025, 9, 11).date(), time.max
    )
    assert filters.download_start_time == datetime(2025, 9, 10)
    assert filters.download_end_time == datetime.combine(
        datetime(2025, 9, 11).date(), time.max
    )


async def test_get_index_page(client: AsyncClient, mock_services: AppServices):
    """测试 / 页面路由"""
    # 模拟数据库返回的数据
    mock_services.db.search_downloads.return_value = ([], 0)

    response = await client.get("/")
    assert response.status_code == 200
    assert "下载记录" in response.text

    # 验证 db.search_downloads 方法被正确调用了一次
    mock_services.db.search_downloads.assert_awaited_once()


async def test_config_page_route(client: AsyncClient):
    """测试 /config-page 页面路由"""
    response = await client.get("/config-page")
    assert response.status_code == 200
    assert "配置管理" in response.text


async def test_get_config_api(client: AsyncClient, mock_services: AppServices):
    """测试 GET /config API 是否能返回配置"""
    fake_config_data = {
        "log": {"level": "INFO"},
        "web": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8000,
            "interval_hours": 6,
        },
        "aria2": None,
        "qbittorrent": None,
        "feeds": [],
    }
    fake_config_obj = Config.model_validate(fake_config_data)
    mock_services.config.get.return_value = fake_config_obj

    response = await client.get("/config")
    assert response.status_code == 200

    # 验证返回的 JSON 数据
    data = response.json()
    assert data["log"]["level"] == "INFO"
    assert data["web"]["port"] == 8000


async def test_update_config_api(client: AsyncClient, mock_services: AppServices):
    """测试 PUT /config API 是否能成功更新配置"""
    payload = {"log": {"level": "SUCCESS"}, "web": {"enabled": False}}

    response = await client.put("/config", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # 验证 mock_services.config.update 方法被以正确的参数调用
    mock_services.config.update.assert_awaited_once_with(payload)


async def test_update_config_api_validation_error(
    client: AsyncClient, mock_services: AppServices
):
    """专门测试 update_config API 的错误处理分支"""
    # 场景1: 模拟 ValidationError
    mock_services.config.update.side_effect = ValidationError.from_exception_data(
        title="Config",
        line_errors=[
            {
                "type": "value_error",
                "loc": ("feeds",),
                "input": "Duplicate",
                "ctx": {"error": "Feed 名称重复"},
            }
        ],
    )
    response = await client.put(
        "/config",
        json={
            "feeds": [
                {"name": "Duplicate", "url": "http://a"},
                {"name": "Duplicate", "url": "http://b"},
            ]
        },
    )
    assert response.status_code == 422
    json_response = response.json()
    assert "Feed 名称重复" in str(json_response)

    # 场景2: 模拟通用 Exception
    mock_services.config.update.side_effect = Exception("Disk is full")
    response = await client.put("/config", json={"web": {"port": 8080}})
    assert response.status_code == 500
    json_response = response.json()
    assert "内部服务器错误" in json_response["detail"]
    assert "Disk is full" in json_response["detail"]


async def test_redownload_api_success(client: AsyncClient, mock_services: AppServices):
    """测试 POST /redownload API 成功时的情况"""
    mock_services.rss_downloader.redownload.return_value = None

    payload = {"id": 1, "downloader": "aria2"}
    response = await client.post("/redownload", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # 验证 downloader.redownload 方法被以正确的参数调用
    mock_services.rss_downloader.redownload.assert_awaited_once_with(
        id=1, downloader="aria2"
    )


async def test_redownload_api_error(client: AsyncClient, mock_services: AppServices):
    """测试 redownload API 的错误"""
    # 场景1: 抛出 ItemNotFoundError
    mock_services.rss_downloader.redownload.side_effect = ItemNotFoundError(
        "未找到记录"
    )
    payload = {"id": 999, "downloader": "aria2"}
    response = await client.post("/redownload", json=payload)
    assert response.status_code == 404
    assert "未找到记录" in response.json()["detail"]

    # 场景2: 抛出 ValueError
    mock_services.rss_downloader.redownload.side_effect = ValueError("No download URL")
    response = await client.post("/redownload", json={"id": 1, "downloader": "aria2"})
    assert response.status_code == 400
    assert "No download URL" in response.json()["detail"]

    # 场景3: 抛出 DownloaderError
    mock_services.rss_downloader.redownload.side_effect = DownloaderError(
        "Aria2 failed"
    )
    response = await client.post("/redownload", json={"id": 1, "downloader": "aria2"})
    assert response.status_code == 500
    assert "Aria2 failed" in response.json()["detail"]

    # 场景4: 抛出通用 Exception
    mock_services.rss_downloader.redownload.side_effect = Exception("Generic error")
    response = await client.post("/redownload", json={"id": 1, "downloader": "aria2"})
    assert response.status_code == 500
    assert "未知服务器错误" in response.json()["detail"]


async def test_test_downloader_connection_routes(
    client: AsyncClient, mock_services: AppServices
):
    """测试 /test-downloader/* 路由的各种情况"""
    # --- 测试 Aria2 ---
    # 场景1: 连接成功
    mock_aria2_instance = AsyncMock()
    mock_aria2_instance.get_version = AsyncMock(
        return_value={"result": {"version": "1.35"}}
    )
    with patch(
        "rss_downloader.web.Aria2Client.create",
        new=AsyncMock(return_value=mock_aria2_instance),
    ) as mock_aria2_create:
        response = await client.post(
            "/test-downloader/aria2", json={"rpc": "http://fake-a2"}
        )
        assert response.status_code == 200
        assert response.json()["version"] == "1.35"

    # 场景2: 连接失败 (抛出 ConnectionError)
    with patch(
        "rss_downloader.web.Aria2Client.create",
        new=AsyncMock(side_effect=ConnectionError("Failed")),
    ):
        response = await client.post(
            "/test-downloader/aria2", json={"rpc": "http://fake-a2"}
        )
        assert response.status_code == 500
        assert "连接失败" in response.json()["detail"]

    # 场景3: API 返回错误信息
    mock_aria2_instance_error = AsyncMock()
    mock_aria2_instance_error.get_version = AsyncMock(
        return_value={"error": {"message": "Invalid method"}}
    )
    with patch(
        "rss_downloader.web.Aria2Client.create",
        new=AsyncMock(return_value=mock_aria2_instance_error),
    ):
        response = await client.post(
            "/test-downloader/aria2", json={"rpc": "http://fake-a2", "secret": "test"}
        )
        assert response.status_code == 400
        assert "Invalid method" in response.json()["detail"]

    # --- 测试 qBittorrent ---
    # 场景1: 连接成功
    mock_qb_instance = AsyncMock()
    mock_qb_instance.get_version = AsyncMock(return_value={"version": "v4.4.0"})
    with patch(
        "rss_downloader.web.QBittorrentClient.create",
        new=AsyncMock(return_value=mock_qb_instance),
    ):
        response = await client.post(
            "/test-downloader/qbittorrent", json={"host": "http://fake-qb"}
        )
        assert response.status_code == 200
        assert response.json()["version"] == "v4.4.0"

    # 场景2: 连接失败
    with patch(
        "rss_downloader.web.QBittorrentClient.create",
        new=AsyncMock(side_effect=ConnectionError("Failed")),
    ):
        response = await client.post(
            "/test-downloader/qbittorrent", json={"host": "http://fake-qb"}
        )
        assert response.status_code == 500
        assert "连接失败" in response.json()["detail"]

    # 场景3: API 返回错误信息
    with patch(
        "rss_downloader.web.QBittorrentClient.create",
        new=AsyncMock(side_effect=ConnectionError("Auth failed")),
    ):
        response = await client.post(
            "/test-downloader/qbittorrent",
            json={"host": "http://fake-qb", "username": "test", "password": ""},
        )
        assert response.status_code == 500
        assert "Auth failed" in response.json()["detail"]

    # --- 测试 Transmission ---
    # 场景1: 连接成功
    mock_tr_instance = AsyncMock()
    mock_tr_instance.get_version = AsyncMock(return_value={"version": "4.0.0"})
    with patch(
        "rss_downloader.web.TransmissionClient.create",
        new=AsyncMock(return_value=mock_tr_instance),
    ):
        response = await client.post(
            "/test-downloader/transmission", json={"host": "http://fake-tr"}
        )
        assert response.status_code == 200
        assert response.json()["version"] == "4.0.0"

    # 场景2: 连接失败
    with patch(
        "rss_downloader.web.TransmissionClient.create",
        new=AsyncMock(side_effect=ConnectionError("Failed")),
    ):
        response = await client.post(
            "/test-downloader/transmission", json={"host": "http://fake-tr"}
        )
        assert response.status_code == 500
        assert "连接失败" in response.json()["detail"]

    # 场景3: API 返回错误信息
    with patch(
        "rss_downloader.web.TransmissionClient.create",
        new=AsyncMock(side_effect=ConnectionError("Auth failed")),
    ):
        response = await client.post(
            "/test-downloader/transmission",
            json={"host": "http://fake-tr", "username": "test", "password": ""},
        )
        assert response.status_code == 500
        assert "Auth failed" in response.json()["detail"]
