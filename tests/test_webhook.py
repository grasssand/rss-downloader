import json
from datetime import datetime

import httpx
import pytest
import respx
from httpx import Response
from pydantic import HttpUrl

from rss_downloader.config import ConfigManager
from rss_downloader.logger import LoggerProtocol
from rss_downloader.models import DownloadRecord, WebhookConfig
from rss_downloader.webhook import WebhookService

pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_http_client() -> httpx.AsyncClient:
    """真实的 HTTP 客户端，用于集成测试"""
    return httpx.AsyncClient()


@pytest.fixture
def successful_record() -> DownloadRecord:
    """测试用的成功下载记录"""
    return DownloadRecord(
        id=1,
        title="Test Anime Episode 1",
        url=HttpUrl("http://example.com/page/1"),
        download_url="magnet:?xt=urn:btih:123",
        feed_name="TestFeed",
        feed_url=HttpUrl("http://example.com/feed.xml"),
        published_time=datetime.now(),
        download_time=datetime.now(),
        downloader="aria2",
        status=1,
        mode=0,
    )


async def test_send_single_webhook_success(
    mock_logger: LoggerProtocol,
    test_config: ConfigManager,
    successful_record: DownloadRecord,
    mock_http_client: httpx.AsyncClient,
):
    """测试成功发送 webhook 通知"""
    webhook_url = "https://discord.com/api/webhooks/123/abc"
    await test_config.update(
        {
            "webhooks": [
                WebhookConfig(name="Discord", url=HttpUrl(webhook_url), enabled=True)
            ]
        }
    )

    with respx.mock as mock_router:
        webhook_route = mock_router.post(webhook_url).mock(return_value=Response(204))

        service = WebhookService(
            config=test_config, logger=mock_logger, http_client=mock_http_client
        )
        await service.send(successful_record)

        assert webhook_route.called
        mock_logger.error.assert_not_called()
        mock_logger.info.assert_called_with("Webhook 通知发送成功 - Discord")

        # 验证发送的请求内容
        sent_request = webhook_route.calls[0].request
        payload = json.loads(sent_request.content)
        assert "Test Anime Episode 1" in payload["embeds"][0]["title"]
        assert payload["embeds"][0]["fields"][0]["value"] == "TestFeed"


async def test_sends_to_enabled_webhooks_only(
    mock_logger: LoggerProtocol,
    test_config: ConfigManager,
    successful_record: DownloadRecord,
    mock_http_client: httpx.AsyncClient,
):
    """测试仅向启用的 webhook 发送通知"""
    enabled_url = "https://discord.com/api/webhooks/123/enabled"
    disabled_url = "https://discord.com/api/webhooks/456/disabled"

    await test_config.update(
        {
            "webhooks": [
                WebhookConfig(
                    name="Enabled Hook", url=HttpUrl(enabled_url), enabled=True
                ),
                WebhookConfig(
                    name="Disabled Hook", url=HttpUrl(disabled_url), enabled=False
                ),
            ]
        }
    )

    with respx.mock as mock_router:
        enabled_route = mock_router.post(enabled_url).mock(return_value=Response(204))
        disabled_route = mock_router.post(disabled_url).mock(return_value=Response(204))

        service = WebhookService(
            config=test_config, logger=mock_logger, http_client=mock_http_client
        )
        await service.send(successful_record)

        assert enabled_route.called
        assert not disabled_route.called
        mock_logger.info.assert_called_with("Webhook 通知发送成功 - Enabled Hook")


async def test_handles_network_error_gracefully(
    mock_logger: LoggerProtocol,
    test_config: ConfigManager,
    successful_record: DownloadRecord,
    mock_http_client: httpx.AsyncClient,
):
    """测试 webhook 网络错误"""
    webhook_url = "https://discord.com/api/webhooks/789/fail"
    await test_config.update(
        {
            "webhooks": [
                WebhookConfig(
                    name="Failing Hook", url=HttpUrl(webhook_url), enabled=True
                )
            ]
        }
    )

    with respx.mock as mock_router:
        mock_router.post(webhook_url).mock(
            side_effect=httpx.ConnectError("Connection failed")
        )

        service = WebhookService(
            config=test_config, logger=mock_logger, http_client=mock_http_client
        )
        await service.send(successful_record)

        mock_logger.error.assert_called_once()
        args, _ = mock_logger.error.call_args
        assert "Webhook 通知发送失败 - Failing Hook" in args[0]
        assert "Connection failed" in str(args[0])


async def test_handles_http_error_response_gracefully(
    mock_logger: LoggerProtocol,
    test_config: ConfigManager,
    successful_record: DownloadRecord,
    mock_http_client: httpx.AsyncClient,
):
    """测试 webhook 返回 HTTP 错误状态码"""
    webhook_url = "https://discord.com/api/webhooks/404/notfound"
    await test_config.update(
        {
            "webhooks": [
                WebhookConfig(name="404 Hook", url=HttpUrl(webhook_url), enabled=True)
            ]
        }
    )

    with respx.mock as mock_router:
        mock_router.post(webhook_url).mock(return_value=Response(404, text="Not Found"))

        service = WebhookService(
            config=test_config, logger=mock_logger, http_client=mock_http_client
        )
        await service.send(successful_record)

        mock_logger.error.assert_called_once()
        args, _ = mock_logger.error.call_args
        assert "Webhook 通知发送失败 - 404 Hook" in args[0]
        assert "404 Not Found" in str(args[0])


async def test_does_nothing_if_no_webhooks_configured(
    mock_logger: LoggerProtocol,
    test_config: ConfigManager,
    successful_record: DownloadRecord,
    mock_http_client: httpx.AsyncClient,
):
    """测试当没有配置任何 webhook 时不执行任何操作"""
    await test_config.update({"webhooks": []})

    with respx.mock as mock_router:
        service = WebhookService(
            config=test_config, logger=mock_logger, http_client=mock_http_client
        )
        await service.send(successful_record)

        mock_logger.error.assert_not_called()
