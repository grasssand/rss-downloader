import abc
from typing import Any
from urllib.parse import urljoin

import httpx

from .logger import LoggerProtocol


class BaseClient(abc.ABC):
    """下载器客户端的抽象基类"""

    def __init__(self, logger: LoggerProtocol):
        self.logger = logger
        self.session = httpx.AsyncClient()

    async def aclose(self):
        """关闭 http 客户端会话"""
        await self.session.aclose()

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
        rpc_url: str,
        secret: str | None = None,
        dir: str | None = None,
    ):
        super().__init__(logger)
        self.rpc_url = rpc_url
        self.secret = secret
        self.dir = dir
        if not self.rpc_url:
            self.logger.warning("Aria2 未配置 RPC 地址，无法添加下载任务")

    def _prepare_request(
        self, method: str, params: list[Any] | None = None
    ) -> dict[str, Any]:
        """准备 RPC 请求数据"""
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

    @classmethod
    async def create(
        cls,
        logger: LoggerProtocol,
        rpc_url: str,
        secret: str | None = None,
        dir: str | None = None,
    ) -> "Aria2Client":
        instance = cls(logger, rpc_url, secret, dir)
        if instance.rpc_url:
            try:
                await instance.get_version()
                logger.success("Aria2 连接成功")
            except Exception as e:
                raise ConnectionError("无法连接到 Aria2，请检查配置或服务状态") from e
        return instance

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
        host: str,
        username: str | None = None,
        password: str | None = None,
    ):
        super().__init__(logger)
        self.base_url = host
        self.username = username
        self.password = password

    async def _login(self, username: str, password: str):
        """登录到qBittorrent WebUI"""
        login_url = urljoin(self.base_url, "/api/v2/auth/login")
        data = {"username": username, "password": password}
        response = await self.session.post(login_url, data=data, timeout=10)
        response.raise_for_status()
        if response.text.strip().lower() != "ok.":
            raise Exception(f"登录认证失败: {response.text}")

    @classmethod
    async def create(
        cls,
        logger: LoggerProtocol,
        host: str,
        username: str | None = None,
        password: str | None = None,
    ) -> "QBittorrentClient":
        instance = cls(logger, host, username, password)
        if username and password:
            try:
                await instance._login(username, password)
                logger.success("qBittorrent 连接成功")
            except Exception as e:
                raise ConnectionError(
                    "无法连接到 qBittorrent，请检查配置或服务状态"
                ) from e
        else:
            logger.warning(
                "qBittorrent 未配置用户名和密码，将以游客模式连接 (可能无法添加下载任务)"
            )
        return instance

    async def add_link(self, link: str) -> bool:
        """添加下载任务"""
        add_url = urljoin(self.base_url, "/api/v2/torrents/add")
        data = {"urls": link}
        response = await self.session.post(add_url, data=data, timeout=10)
        response.raise_for_status()
        if response.text.strip().lower() == "ok.":
            return True
        else:
            raise Exception(f"qBittorrent 添加任务失败: {response.text}")

    async def get_version(self) -> dict[str, str]:
        """获取 qBittorrent 版本信息以测试连接"""
        version_url = urljoin(self.base_url, "/api/v2/app/version")
        response = await self.session.get(version_url, timeout=5)
        response.raise_for_status()
        return {"version": response.text}


class TransmissionClient(BaseClient):
    def __init__(
        self,
        logger: LoggerProtocol,
        host: str,
        username: str | None = None,
        password: str | None = None,
    ):
        super().__init__(logger)
        self.rpc_url = urljoin(host, "/transmission/rpc")
        self._session_id: str | None = None

        if username and password:
            self.session.auth = httpx.BasicAuth(username, password)

    async def _get_session_id(self) -> str:
        """刷新 Transmission RPC session ID."""
        try:
            response = await self.session.post(self.rpc_url, json={}, timeout=5)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                session_id = e.response.headers.get("X-Transmission-Session-Id")
                if session_id:
                    self._session_id = session_id
                    self.logger.debug(f"获取到 Transmission Session ID: {session_id}")
                    return session_id

        raise ConnectionError("无法获取 Transmission Session ID")

    async def _make_rpc_request(
        self, method: str, arguments: dict | None = None
    ) -> dict[str, Any]:
        """生成并发送 RPC 请求"""
        if not self._session_id:
            await self._get_session_id()

        headers = {"X-Transmission-Session-Id": self._session_id}
        payload = {"method": method, "arguments": arguments or {}}

        try:
            response = await self.session.post(
                self.rpc_url,
                json=payload,
                headers=headers,  # type: ignore
                timeout=10,
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:  # Session ID 失效
                self.logger.warning("Transmission Session ID 失效. 重新获取...")
                self._session_id = None
                await self._get_session_id()
                headers["X-Transmission-Session-Id"] = self._session_id
                response = await self.session.post(
                    self.rpc_url,
                    json=payload,
                    headers=headers,  # type: ignore
                    timeout=10,
                )
                response.raise_for_status()
                return response.json()
            raise

    @classmethod
    async def create(
        cls,
        logger: LoggerProtocol,
        host: str,
        username: str | None = None,
        password: str | None = None,
    ) -> "TransmissionClient":
        instance = cls(logger, host, username, password)
        try:
            await instance.get_version()
            logger.success("Transmission 连接成功")
        except Exception as e:
            raise ConnectionError(
                "无法连接到 Transmission，请检查配置或服务状态"
            ) from e
        return instance

    async def add_link(self, link: str) -> dict[str, Any]:
        """添加下载任务"""
        arguments = {"filename": link}
        response_data = await self._make_rpc_request("torrent-add", arguments)
        if response_data.get("result") == "success":
            return response_data
        raise Exception(f"Transmission 添加任务失败: {response_data.get('result')}")

    async def get_version(self) -> dict[str, str]:
        """获取 Transmission 版本信息以测试连接"""
        response_data = await self._make_rpc_request("session-get")
        if response_data.get("result") == "success":
            version = response_data.get("arguments", {}).get("version", "Unknown")
            return {"version": version}
        raise Exception(f"Transmission 版本获取失败: {response_data.get('result')}")
