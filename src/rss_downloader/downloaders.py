import abc
from typing import Any
from urllib.parse import urljoin

import httpx

from .logger import LoggerProtocol


class BaseClient(abc.ABC):
    """下载器客户端的抽象基类"""

    def __init__(self, logger: LoggerProtocol, http_client: httpx.AsyncClient):
        self.logger = logger
        self.session = http_client

    @abc.abstractmethod
    async def add_link(self, link: str) -> Any:
        """添加下载链接的抽象方法"""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_version(self) -> Any:
        """获取版本号以测试连接的抽象方法"""
        raise NotImplementedError


class Aria2Client(BaseClient):
    def __init__(
        self,
        logger: LoggerProtocol,
        http_client: httpx.AsyncClient,
        rpc_url: str,
        secret: str | None = None,
        dir: str | None = None,
    ):
        super().__init__(logger, http_client)
        self.rpc_url = rpc_url
        self.secret = secret
        self.dir = dir
        if not self.rpc_url:
            self.logger.warning("Aria2 未配置 RPC 地址，无法添加下载任务")

    @classmethod
    async def create(
        cls,
        logger: LoggerProtocol,
        http_client: httpx.AsyncClient,
        rpc_url: str,
        secret: str | None = None,
        dir: str | None = None,
    ) -> "Aria2Client":
        instance = cls(logger, http_client, rpc_url, secret, dir)
        if instance.rpc_url:
            try:
                await instance.get_version()
            except Exception as e:
                raise ConnectionError("无法连接到 Aria2，请检查配置或服务状态") from e
        return instance

    def _prepare_request(
        self, method: str, params: list[Any] | None = None
    ) -> dict[str, Any]:
        """准备RPC请求数据"""
        if params is None:
            params = []
        if self.secret:
            params.insert(0, f"token:{self.secret}")
        return {
            "jsonrpc": "2.0",
            "id": "rss-downloader",
            "method": method,
            "params": params,
        }

    async def add_link(self, link: str) -> dict[str, Any]:
        """添加下载任务"""
        options = {}
        if self.dir:
            options["dir"] = self.dir
        params: list[list[str] | dict[str, Any]] = [[link]]
        if options:
            params.append(options)
        data = self._prepare_request("aria2.addUri", params)
        response = await self.session.post(self.rpc_url, json=data, timeout=10)
        response.raise_for_status()
        return response.json()

    async def get_version(self) -> dict[str, Any]:
        """获取 Aria2 版本信息以测试连接"""
        data = self._prepare_request("aria2.getVersion")
        response = await self.session.post(self.rpc_url, json=data, timeout=5)
        response.raise_for_status()
        return response.json()


class QBittorrentClient(BaseClient):
    def __init__(
        self,
        logger: LoggerProtocol,
        http_client: httpx.AsyncClient,
        host: str,
        username: str | None = None,
        password: str | None = None,
    ):
        super().__init__(logger, http_client)
        self.base_url = host
        self.username = username
        self.password = password

    @classmethod
    async def create(
        cls,
        logger: LoggerProtocol,
        http_client: httpx.AsyncClient,
        host: str,
        username: str | None = None,
        password: str | None = None,
    ) -> "QBittorrentClient":
        instance = cls(logger, http_client, host, username, password)
        if username and password:
            try:
                await instance._login(username, password)
                instance.logger.info("qBittorrent 登录成功")
            except Exception as e:
                raise ConnectionError(
                    "无法登录到 qBittorrent，请检查配置或服务状态"
                ) from e
        else:
            instance.logger.warning(
                "qBittorrent 未配置用户名和密码，将以游客模式连接 (可能无法添加下载任务)"
            )
        return instance

    async def _login(self, username: str, password: str):
        """登录到qBittorrent WebUI"""
        login_url = urljoin(self.base_url, "/api/v2/auth/login")
        data = {"username": username, "password": password}
        response = await self.session.post(login_url, data=data, timeout=10)
        response.raise_for_status()
        if response.text.strip().lower() != "ok.":
            raise Exception(f"登录认证失败，响应: {response.text}")

    async def add_link(self, link: str) -> bool:
        """添加下载任务"""
        add_url = urljoin(self.base_url, "/api/v2/torrents/add")
        data = {"urls": link}
        response = await self.session.post(add_url, data=data, timeout=10)
        response.raise_for_status()
        if response.text.strip().lower() == "ok.":
            return True
        else:
            raise Exception(f"qBittorrent 添加任务失败，响应: {response.text}")

    async def get_version(self) -> dict[str, str]:
        """获取 qBittorrent 版本信息以测试连接"""
        version_url = urljoin(self.base_url, "/api/v2/app/version")
        response = await self.session.get(version_url, timeout=5)
        response.raise_for_status()
        return {"version": response.text}
