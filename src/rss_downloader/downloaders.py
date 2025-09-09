from typing import Any
from urllib.parse import urljoin

import requests


class Aria2Client:
    def __init__(
        self,
        rpc_url: str,
        secret: str | None = None,
        dir: str | None = None,
        logger=None,
    ):
        self.rpc_url = rpc_url
        self.secret = secret
        self.dir = dir
        self.logger = logger

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

    def add_link(self, link: str) -> dict[str, Any]:
        """添加下载任务"""
        try:
            options = {}
            if self.dir:
                options["dir"] = self.dir

            params: list[list[str] | dict[str, Any]] = [[link]]
            if options:
                params.append(options)

            data = self._prepare_request("aria2.addUri", params)
            response = requests.post(self.rpc_url, json=data, timeout=10)
            response.raise_for_status()

            return response.json()

        except requests.exceptions.RequestException as e:
            if self.logger:
                self.logger.error(f"Aria2 请求失败: {e}")
            return {"error": str(e)}
        except Exception as e:
            if self.logger:
                self.logger.exception(f"Aria2 发生未知错误: {e}")
            return {"error": str(e)}

    def get_version(self) -> dict[str, Any]:
        """获取 Aria2 版本信息以测试连接"""
        try:
            data = self._prepare_request("aria2.getVersion")
            response = requests.post(self.rpc_url, json=data, timeout=5)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取 Aria2 版本失败: {e}")
            return {"error": {"message": str(e)}}


class QBittorrentClient:
    def __init__(
        self,
        host: str,
        username: str | None = None,
        password: str | None = None,
        logger=None,
    ):
        self.base_url = host
        self.session = requests.Session()
        self.logger = logger

        if username and password:
            try:
                self._login(username, password)
                if self.logger:
                    self.logger.info("qBittorrent 登录成功")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"qBittorrent 登录失败: {e}")
                raise ConnectionError(
                    "无法登录到 qBittorrent，请检查配置或服务状态"
                ) from e
        else:
            if self.logger:
                self.logger.info(
                    "qBittorrent 未配置用户名和密码，将以游客模式连接 (可能无法添加任务)"
                )

    def _login(self, username: str, password: str):
        """登录到qBittorrent WebUI"""
        try:
            login_url = urljoin(self.base_url, "/api/v2/auth/login")
            data = {"username": username, "password": password}

            response = self.session.post(login_url, data=data, timeout=10)
            response.raise_for_status()
            if response.text.strip().lower() != "ok.":
                raise Exception(f"登录认证失败，响应: {response.text}")
            if self.logger:
                self.logger.info("qBittorrent 登录成功")

        except requests.exceptions.RequestException as e:
            if self.logger:
                self.logger.error(f"qBittorrent 登录请求失败: {e}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"qBittorrent 登录失败: {e}")

    def add_link(self, link: str) -> bool:
        """添加下载任务"""
        try:
            add_url = urljoin(self.base_url, "/api/v2/torrents/add")
            data = {"urls": link}
            response = self.session.post(add_url, data=data, timeout=10)
            response.raise_for_status()
            if response.text.strip().lower() == "ok.":
                return True
            else:
                if self.logger:
                    self.logger.error(
                        f"qBittorrent 添加任务失败，响应: {response.text}"
                    )
                return False

        except requests.exceptions.RequestException as e:
            if self.logger:
                self.logger.error(f"qBittorrent 添加任务请求失败: {e}")
            return False
        except Exception as e:
            if self.logger:
                self.logger.exception(f"qBittorrent 添加任务时发生未知错误: {e}")
            return False

    def get_version(self) -> dict[str, str]:
        """获取 qBittorrent 版本信息以测试连接"""
        try:
            version_url = urljoin(self.base_url, "/api/v2/app/version")
            response = self.session.get(version_url, timeout=5)
            response.raise_for_status()
            return {"version": response.text}
        except Exception as e:
            if self.logger:
                self.logger.error(f"获取 qBittorrent 版本失败: {e}")
            return {"error": str(e)}
