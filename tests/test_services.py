from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from rss_downloader.config import ConfigManager
from rss_downloader.downloaders import (
    Aria2Client,
    QBittorrentClient,
    TransmissionClient,
)
from rss_downloader.logger import LoggerProtocol
from rss_downloader.services import AppServices

pytestmark = pytest.mark.anyio


async def test_app_services_create_aria2_failure(
    test_config: ConfigManager, mock_logger: LoggerProtocol, monkeypatch
):
    """测试当下载器 aria2 初始化失败"""
    monkeypatch.setattr(
        "rss_downloader.services.setup_logger", AsyncMock(return_value=mock_logger)
    )

    # 模拟 Aria2Client.create 方法总是抛出连接错误
    with patch(
        "rss_downloader.services.Aria2Client.create",
        new=AsyncMock(side_effect=ConnectionError("Fake Connection Error")),
    ):
        await test_config.update({"aria2": {"rpc": "http://bad-host"}})

        services = await AppServices.create(config=test_config)

        assert services.aria2 is None
        mock_logger.error.assert_called_once()
        args, _ = mock_logger.error.call_args
        assert "初始化 Aria2 客户端失败" in args[0]
        assert "Fake Connection Error" in args[0]


async def test_app_services_create_qbittorent_failure(
    test_config: ConfigManager, mock_logger: LoggerProtocol, monkeypatch
):
    """测试当下载器 qBittorrent 初始化失败"""
    monkeypatch.setattr(
        "rss_downloader.services.setup_logger", AsyncMock(return_value=mock_logger)
    )
    # 模拟 QBittorrentClient.create 方法总是抛出连接错误
    with patch(
        "rss_downloader.services.QBittorrentClient.create",
        new=AsyncMock(side_effect=ConnectionError("Fake Connection Error")),
    ):
        await test_config.update({"qbittorrent": {"host": "http://bad-host"}})

        services = await AppServices.create(config=test_config)

        assert services.qbittorrent is None
        mock_logger.error.assert_called_once()
        args, _ = mock_logger.error.call_args
        assert "初始化 qBittorrent 客户端失败" in args[0]
        assert "Fake Connection Error" in args[0]


async def test_app_services_create_transmission_failure(
    test_config: ConfigManager, mock_logger: LoggerProtocol, monkeypatch
):
    """测试当下载器 transmission 初始化失败"""
    monkeypatch.setattr(
        "rss_downloader.services.setup_logger", AsyncMock(return_value=mock_logger)
    )
    # 模拟 TransmissionClient.create 方法总是抛出连接错误
    with patch(
        "rss_downloader.services.TransmissionClient.create",
        new=AsyncMock(side_effect=ConnectionError("Fake Connection Error")),
    ):
        await test_config.update({"transmission": {"host": "http://bad-host"}})

        services = await AppServices.create(config=test_config)

        assert services.transmission is None
        mock_logger.error.assert_called_once()
        args, _ = mock_logger.error.call_args
        assert "初始化 Transmission 客户端失败" in args[0]
        assert "Fake Connection Error" in args[0]


async def test_app_services_close(mock_logger: LoggerProtocol):
    """测试 AppServices.close 方法是否能正确关闭其管理的资源"""
    mock_http_client = AsyncMock(spec=AsyncClient)

    mock_aria2 = MagicMock(spec=Aria2Client)
    mock_aria2.aclose = AsyncMock()

    mock_qb = MagicMock(spec=QBittorrentClient)
    mock_qb.aclose = AsyncMock()

    mock_trans = MagicMock(spec=TransmissionClient)
    mock_trans.aclose = AsyncMock()

    # 3. 使用准备好的 Mocks 初始化 AppServices
    services_instance = AppServices(
        config=MagicMock(),
        logger=mock_logger,
        db=MagicMock(),
        rss_downloader=MagicMock(),
        aria2=mock_aria2,
        qbittorrent=mock_qb,
        transmission=mock_trans,
        http_client=mock_http_client,
    )

    await services_instance.close()

    mock_http_client.aclose.assert_awaited_once()
    mock_aria2.aclose.assert_awaited_once()
    mock_qb.aclose.assert_awaited_once()
    mock_trans.aclose.assert_awaited_once()
    mock_logger.info.assert_called_with("服务已关闭。")
