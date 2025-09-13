import httpx
import pytest
import respx
from httpx import RequestError, Response

from rss_downloader.downloaders import Aria2Client, QBittorrentClient
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
    async with httpx.AsyncClient() as http_client:
        client = await Aria2Client.create(
            logger=mock_logger, http_client=http_client, rpc_url="http://fake-aria2/rpc"
        )
    assert client is not None


async def test_aria2_create_failure(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 Aria2Client 初始化时连接失败并抛出 ConnectionError"""
    mock_downloader_api.post("http://fake-aria2/rpc").mock(
        side_effect=RequestError("Connection failed")
    )
    with pytest.raises(ConnectionError, match="无法连接到 Aria2"):
        async with httpx.AsyncClient() as http_client:
            await Aria2Client.create(
                logger=mock_logger,
                http_client=http_client,
                rpc_url="http://fake-aria2/rpc",
            )


async def test_aria2_create_no_rpc_url(mock_logger: LoggerProtocol):
    """测试当 rpc_url 为空时，create 方法不会抛出异常并记录警告"""
    async with httpx.AsyncClient() as http_client:
        client = await Aria2Client.create(
            logger=mock_logger, http_client=http_client, rpc_url=""
        )
    assert client is not None
    mock_logger.warning.assert_called_with("Aria2 未配置 RPC 地址，无法添加下载任务")


async def test_aria2_add_link(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 Aria2Client.add_link 的成功和失败路径"""
    mock_downloader_api.post("http://a2/rpc", json__method="aria2.getVersion").mock(
        return_value=Response(200, json={"result": "ok"})
    )

    async with httpx.AsyncClient() as http_client:
        client = await Aria2Client.create(
            logger=mock_logger,
            http_client=http_client,
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


# --- QBittorrentClient Tests ---
async def test_qb_create_success(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 QBittorrentClient 初始化时登录成功"""
    mock_downloader_api.post("http://fake-qb/api/v2/auth/login").mock(
        return_value=Response(200, text="Ok.")
    )
    async with httpx.AsyncClient() as http_client:
        client = await QBittorrentClient.create(
            logger=mock_logger,
            http_client=http_client,
            host="http://fake-qb",
            username="admin",
            password="password",
        )
    assert client is not None
    mock_logger.info.assert_called_with("qBittorrent 登录成功")


async def test_qb_create_login_fail_auth(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 QBittorrentClient 初始化时因认证失败而抛出 ConnectionError"""
    mock_downloader_api.post("http://fake-qb/api/v2/auth/login").mock(
        return_value=Response(200, text="Fails.")
    )
    with pytest.raises(ConnectionError, match="无法登录到 qBittorrent"):
        async with httpx.AsyncClient() as http_client:
            await QBittorrentClient.create(
                logger=mock_logger,
                http_client=http_client,
                host="http://fake-qb",
                username="admin",
                password="password",
            )


async def test_qb_create_login_fail_network(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 QBittorrentClient 初始化时因网络失败而抛出 ConnectionError"""
    mock_downloader_api.post("http://fake-qb/api/v2/auth/login").mock(
        side_effect=RequestError("Network Error")
    )
    with pytest.raises(ConnectionError, match="无法登录到 qBittorrent"):
        async with httpx.AsyncClient() as http_client:
            await QBittorrentClient.create(
                logger=mock_logger,
                http_client=http_client,
                host="http://fake-qb",
                username="admin",
                password="password",
            )


async def test_qb_create_no_auth_warning(mock_logger: LoggerProtocol):
    """测试 QBittorrentClient 初始化时不提供用户名密码会打印警告"""
    async with httpx.AsyncClient() as http_client:
        client = await QBittorrentClient.create(
            logger=mock_logger, http_client=http_client, host="http://fake-qb"
        )
    assert client is not None
    mock_logger.warning.assert_called_once()


async def test_qb_get_version(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 get_version 方法的成功与失败"""
    async with httpx.AsyncClient() as http_client:
        client = await QBittorrentClient.create(
            logger=mock_logger, http_client=http_client, host="http://qb-ver"
        )

        # 场景1: 成功获取
        mock_downloader_api.get("http://qb-ver/api/v2/app/version").mock(
            return_value=Response(200, text="v4.3.9")
        )
        result = await client.get_version()
        assert result == {"version": "v4.3.9"}
        mock_downloader_api.reset()

        # 场景2: 失败时抛出异常
        mock_downloader_api.get("http://qb-ver/api/v2/app/version").mock(
            return_value=Response(404)
        )
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_version()


async def test_qb_add_link(
    mock_downloader_api: respx.Router, mock_logger: LoggerProtocol
):
    """测试 QBittorrentClient.add_link 的成功和失败路径"""
    mock_downloader_api.post("http://qb-add/api/v2/auth/login").mock(
        return_value=Response(200, text="Ok.")
    )

    async with httpx.AsyncClient() as http_client:
        client = await QBittorrentClient.create(
            logger=mock_logger,
            http_client=http_client,
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
