import functools
import os
from pathlib import Path as SyncPath
from typing import Any

import anyio
import yaml
from pydantic import ValidationError

from .logger import DummyLogger, LoggerProtocol
from .models import (
    Aria2Config,
    Config,
    Downloader,
    FeedConfig,
    QBittorrentConfig,
    WebConfig,
)

CONFIG_FILE = "config.yaml"
DATABASE_FILE_NAME = "downloads.db"


def _deep_merge(default: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    """合并配置：保留用户已有值，补齐缺失字段"""
    result = dict(default)
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


class ConfigManager:
    def __init__(self, config_path: anyio.Path, initial_config: Config):
        self.config_path = config_path
        self._lock = anyio.Lock()
        self._config_version = 0
        self._config = initial_config
        self._last_mtime = 0.0
        self._web_mode_enabled = False
        self.logger: LoggerProtocol = DummyLogger()

    @classmethod
    async def create(cls) -> "ConfigManager":
        config_path = await cls._find_config_path()
        initial_config = await cls._load_or_create(config_path)
        instance = cls(config_path, initial_config)

        try:
            stat_result = await instance.config_path.stat()
            instance._last_mtime = stat_result.st_mtime
        except FileNotFoundError:
            pass

        return instance

    @staticmethod
    async def _find_config_path() -> anyio.Path:
        search_paths = [
            anyio.Path(os.environ.get("XDG_CONFIG_HOME", SyncPath.home() / ".config"))
            / "rss-downloader"
            / CONFIG_FILE,  # $XDG_CONFIG_HOME 或 ~/.config
            anyio.Path(SyncPath.cwd()) / CONFIG_FILE,  # 当前工作目录
            anyio.Path(SyncPath(__file__).resolve().parent)
            / CONFIG_FILE,  # 脚本所在目录
        ]

        for path in search_paths:
            if await path.exists():
                return path
        return search_paths[0]  # 如果都不存在，使用第一个路径

    @staticmethod
    async def _load_or_create(config_path: anyio.Path) -> Config:
        """加载或创建配置文件"""
        user_data = {}
        if await config_path.exists():
            async with await config_path.open("r", encoding="utf-8") as f:
                content = await f.read()
                user_data = (
                    await anyio.to_thread.run_sync(yaml.safe_load, content) or {}
                )

        default_dump = Config.model_validate({}).model_dump(mode="json")
        merged_config = _deep_merge(default_dump, user_data)
        config_obj = Config.model_validate(merged_config)

        # 仅当文件不存在或内容不完整时才回写
        if not await config_path.exists() or user_data != merged_config:
            await config_path.parent.mkdir(parents=True, exist_ok=True)
            dumped_yaml = await anyio.to_thread.run_sync(
                functools.partial(
                    yaml.safe_dump,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                ),
                config_obj.model_dump(mode="json"),
            )
            async with await config_path.open("w", encoding="utf-8") as f:
                await f.write(dumped_yaml)

        return config_obj

    async def _read_only_load(self) -> Config:
        """一个只读的加载方法供热重载使用，避免回写"""
        user_data = {}
        if await self.config_path.exists():
            async with await self.config_path.open("r", encoding="utf-8") as f:
                content = await f.read()
                user_data = (
                    await anyio.to_thread.run_sync(yaml.safe_load, content) or {}
                )

        default_dump = Config.model_validate({}).model_dump(mode="json")
        merged_config = _deep_merge(default_dump, user_data)
        return Config.model_validate(merged_config)

    def set_logger(self, logger: LoggerProtocol) -> None:
        self.logger = logger

    def get(self) -> Config:
        return self._config

    async def update(self, new_data: dict[str, Any]) -> None:
        """更新配置并写回文件"""

        self.logger.debug(f"尝试更新配置: {new_data}")

        async with self._lock:
            backup_config_dump = self._config.model_dump(mode="json")
            merged_for_validation = _deep_merge(backup_config_dump, new_data)
            try:
                self._config = Config.model_validate(merged_for_validation)
                dumped_yaml = await anyio.to_thread.run_sync(
                    functools.partial(
                        yaml.safe_dump,
                        allow_unicode=True,
                        sort_keys=False,
                    ),
                    self._config.model_dump(mode="json"),
                )
                async with await self.config_path.open("w", encoding="utf-8") as f:
                    await f.write(dumped_yaml)

            except (ValidationError, OSError) as e:
                self.logger.error(f"配置更新失败，正在回滚... 错误: {e}")
                self._config = Config.model_validate(backup_config_dump)
                raise e

    def initialize(self, tg: anyio.abc.TaskGroup, cli_force_web: bool = False):
        """根据命令行参数或配置文件启用配置热重载"""
        self._web_mode_enabled = cli_force_web or self._config.web.enabled
        if self._web_mode_enabled:
            tg.start_soon(self._watch_for_changes)

    @property
    def is_web_mode(self) -> bool:
        return self._web_mode_enabled

    @property
    def web(self) -> WebConfig:
        return self.get().web

    @property
    def log_level(self) -> str:
        return self.get().log.level

    @property
    def aria2(self) -> Aria2Config | None:
        return self.get().aria2

    @property
    def qbittorrent(self) -> QBittorrentConfig | None:
        return self.get().qbittorrent

    @property
    def feeds(self) -> list[FeedConfig]:
        return self.get().feeds

    def get_feed_by_name(self, feed_name: str) -> FeedConfig | None:
        for feed in self.feeds:
            if feed.name == feed_name:
                return feed
        return None

    def get_feed_patterns(self, feed_name: str) -> tuple[list[str], list[str]]:
        """获取指定RSS源的过滤规则"""
        for feed in self.feeds:
            if feed.name == feed_name:
                include_patterns = feed.include
                exclude_patterns = feed.exclude
                return include_patterns, exclude_patterns
        return [], []  # 如果找不到对应的源，返回空规则

    def get_feed_downloader(self, feed_name: str) -> Downloader:
        """获取指定RSS源的下载器类型"""
        for feed in self.feeds:
            if feed.name == feed_name:
                return feed.downloader
        return "aria2"

    def get_config_version(self) -> int:
        """获取当前配置的版本号"""
        return self._config_version

    async def _watch_for_changes(self):
        """启动后台线程监控文件变化"""
        self.logger.info(f"正在监控配置文件: {self.config_path}")

        while True:
            await anyio.sleep(5)
            try:
                if await self.config_path.exists():
                    mtime = (await self.config_path.stat()).st_mtime
                    if mtime > self._last_mtime:
                        async with self._lock:
                            self._config = await self._read_only_load()
                            self._last_mtime = mtime
                            self._config_version += 1
                        self.logger.info(f"配置文件已重新加载: {self.config_path}")
            except Exception:
                self.logger.exception("配置文件监控任务出错")
