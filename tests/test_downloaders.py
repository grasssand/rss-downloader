import json

import pytest
import respx
from httpx import Request, RequestError, Response

from rss_downloader.downloaders import (
    Aria2Client,
    QBittorrentClient,
    TransmissionClient,
)
from rss_downloader.logger import LoggerProtocol

pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_downloader_api():
    """使用 respx 模拟所有 HTTP 请求"""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        yield mock


# --- Aria2Client Tests ---
async def test_aria2_create_success(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 Aria2Client 初始化时连接成功"""
    mock_downloader_api.post("http://fake-aria2/rpc").mock(
        return_value=Response(200, json={"result": "ok"})
    )
    client = await Aria2Client.create(
        logger=mock_logger, rpc_url="http://fake-aria2/rpc"
    )
    assert client is not None
    await client.aclose()


async def test_aria2_create_failure(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 Aria2Client 初始化时连接失败并抛出 ConnectionError"""
    mock_downloader_api.post("http://fake-aria2/rpc").mock(
        side_effect=RequestError("Connection failed")
    )
    with pytest.raises(ConnectionError, match="无法连接到 Aria2"):
        await Aria2Client.create(
            logger=mock_logger,
            rpc_url="http://fake-aria2/rpc",
        )


async def test_aria2_add_link(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 Aria2Client.add_link 的成功和失败路径"""
    mock_downloader_api.post("http://a2/rpc", json__method="aria2.getVersion").mock(
        return_value=Response(200, json={"result": "ok"})
    )
    client = await Aria2Client.create(
        logger=mock_logger,
        rpc_url="http://a2/rpc",
        secret="s3cr3t",
        dir="test",
    )

    add_route = mock_downloader_api.post(
        "http://a2/rpc", json__method="aria2.addUri"
    ).mock(return_value=Response(200, json={"result": "gid1"}))
    result = await client.add_link("magnet:?xt=1")
    assert result == {"result": "gid1"}
    assert add_route.called

    add_route.mock(side_effect=RequestError("Network down"))
    with pytest.raises(RequestError):
        await client.add_link("magnet:?xt=2")

    await client.aclose()


# --- QBittorrentClient Tests ---
async def test_qb_create_success(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 QBittorrentClient 初始化时登录成功"""
    mock_downloader_api.post("http://fake-qb/api/v2/auth/login").mock(
        return_value=Response(200, text="Ok.")
    )
    client = await QBittorrentClient.create(
        logger=mock_logger,
        host="http://fake-qb",
        username="admin",
        password="password",
    )
    assert client is not None
    mock_logger.success.assert_called_with("qBittorrent 连接成功")
    await client.aclose()


async def test_qb_create_login_fail_auth(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 QBittorrentClient 初始化时因认证失败而抛出 ConnectionError"""
    mock_downloader_api.post("http://fake-qb/api/v2/auth/login").mock(
        return_value=Response(200, text="Fails.")
    )
    with pytest.raises(ConnectionError, match="无法连接到 qBittorrent"):
        await QBittorrentClient.create(
            logger=mock_logger,
            host="http://fake-qb",
            username="admin",
            password="password",
        )


async def test_qb_add_link(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 QBittorrentClient.add_link 的成功和失败路径"""
    mock_downloader_api.post("http://qb-add/api/v2/auth/login").mock(
        return_value=Response(200, text="Ok.")
    )
    client = await QBittorrentClient.create(
        logger=mock_logger,
        host="http://qb-add",
        username="admin",
        password="password",
    )

    add_route = mock_downloader_api.post("http://qb-add/api/v2/torrents/add").mock(
        return_value=Response(200, text="Ok.")
    )
    result = await client.add_link("magnet:?xt=1")
    assert result is True
    assert add_route.called

    add_route.mock(return_value=Response(200, text="failed."))
    with pytest.raises(Exception, match="qBittorrent 添加任务失败"):
        await client.add_link("magnet:?xt=2")

    await client.aclose()


# --- TransmissionClient Tests ---
@pytest.fixture
def transmission_rpc_url() -> str:
    return "http://fake-trans/transmission/rpc"


class MockTransmissionServer:
    """一个可配置的、用于模拟 Transmission RPC 服务器的类"""

    def __init__(self):
        self.current_session_id = "session-123"
        self.next_session_id = "session-456"  # 用于模拟 session 过期
        self.fail_add_link = False

    def handler(self, request: Request):
        """智能 RPC 处理器，根据请求头和内容返回不同响应"""
        session_id = request.headers.get("X-Transmission-Session-Id")

        # 场景 1: 客户端没有 session id 或 session id 错误/过期
        if session_id != self.current_session_id:
            # 在 session 过期测试中，我们会将 current_session_id 切换到 next_session_id
            return Response(
                409, headers={"X-Transmission-Session-Id": self.current_session_id}
            )

        # 场景 2: 客户端有正确的 session id，解析其请求
        data = json.loads(request.read())
        method = data.get("method")

        if method == "session-get":
            return Response(
                200, json={"result": "success", "arguments": {"version": "4.0.0"}}
            )
        if method == "torrent-add":
            if self.fail_add_link:
                return Response(200, json={"result": "duplicate torrent"})
            return Response(200, json={"result": "success"})

        return Response(500, text="Mock server error: Unknown method")


@pytest.fixture
def mock_transmission_server(
    mock_downloader_api: respx.Router, transmission_rpc_url: str
) -> MockTransmissionServer:
    """设置并返回一个可配置的 Transmission 模拟服务器实例"""
    server = MockTransmissionServer()
    mock_downloader_api.post(transmission_rpc_url).mock(side_effect=server.handler)
    return server


async def test_transmission_create_success(
    mock_transmission_server: MockTransmissionServer,
    mock_logger: LoggerProtocol,
):
    """测试 TransmissionClient 初始化成功"""
    client = await TransmissionClient.create(
        logger=mock_logger, host="http://fake-trans"
    )
    assert client is not None
    mock_logger.success.assert_called_with("Transmission 连接成功")
    await client.aclose()


async def test_transmission_create_failure(
    mock_downloader_api: respx.Router,
    mock_logger: LoggerProtocol,
    transmission_rpc_url: str,
):
    """测试 TransmissionClient 初始化时因网络错误失败"""
    mock_downloader_api.post(transmission_rpc_url).mock(
        side_effect=RequestError("Network Error")
    )
    with pytest.raises(ConnectionError, match="无法连接到 Transmission"):
        await TransmissionClient.create(logger=mock_logger, host="http://fake-trans")


async def test_transmission_add_link_success(
    mock_transmission_server: MockTransmissionServer,
    mock_logger: LoggerProtocol,
):
    """测试 TransmissionClient.add_link 成功"""
    client = await TransmissionClient.create(
        logger=mock_logger, host="http://fake-trans"
    )
    result = await client.add_link("magnet:?xt=1")
    assert result == {"result": "success"}
    await client.aclose()


async def test_transmission_session_id_expired_and_retry(
    mock_transmission_server: MockTransmissionServer,
    mock_logger: LoggerProtocol,
):
    """测试 session ID 过期后，客户端能自动重试并成功"""
    client = await TransmissionClient.create(
        logger=mock_logger, host="http://fake-trans"
    )

    # 模拟服务器 session 过期
    mock_transmission_server.current_session_id = (
        mock_transmission_server.next_session_id
    )

    result = await client.add_link("magnet:?xt=1")
    assert result == {"result": "success"}
    await client.aclose()


async def test_transmission_add_link_failure(
    mock_transmission_server: MockTransmissionServer,
    mock_logger: LoggerProtocol,
):
    """测试当 RPC 调用返回失败时，add_link 抛出异常"""
    # 设置服务器在收到 torrent-add 请求时返回失败
    mock_transmission_server.fail_add_link = True

    client = await TransmissionClient.create(
        logger=mock_logger, host="http://fake-trans"
    )
    with pytest.raises(Exception, match="Transmission 添加任务失败: duplicate torrent"):
        await client.add_link("magnet:?xt=1")

    await client.aclose()
