import os
import threading
import time
from pathlib import Path
from typing import Any, Literal

import yaml

from .models import Aria2Config, Config, FeedConfig, QBittorrentConfig, WebConfig

CONFIG_FILE = "config.yaml"


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
    def __init__(self):
        self.config_path = self._find_config_path()
        self._lock = threading.Lock()
        self._config_version = 0
        self._config = self._load_or_create()
        self._last_mtime = self.config_path.stat().st_mtime
        self._web_mode_enabled = False

    def _find_config_path(self) -> Path:
        search_paths = [
            Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "rss-downloader"
            / CONFIG_FILE,  # $XDG_CONFIG_HOME 或 ~/.config
            Path.cwd() / CONFIG_FILE,  # 当前工作目录
            Path(__file__).resolve().parent / CONFIG_FILE,  # 脚本所在目录
        ]
        config_path = next(
            (path for path in search_paths if path.exists()),
            search_paths[0],  # 如果都不存在，使用第一个路径
        )

        return config_path

    def _load_or_create(self) -> Config:
        """加载或创建配置文件"""
        default_cfg = Config.default()
        if self.config_path.exists():
            with self.config_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            merged = _deep_merge(default_cfg.dict(), data)
            if merged != data:
                with self.config_path.open("w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        merged,
                        f,
                        allow_unicode=True,
                        sort_keys=False,
                        default_flow_style=False,
                    )
            return Config.parse_obj(merged)

        else:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with self.config_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(
                    default_cfg.dict(),
                    f,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
            return default_cfg

    def get(self) -> Config:
        with self._lock:
            return self._config

    def update(self, new_data: dict[str, Any]):
        """更新配置并写回文件"""
        from .logger import logger

        logger.debug(f"尝试更新配置: {new_data}")

        with self._lock:
            current_dump = self._config.dict()
            try:
                merged = _deep_merge(current_dump, new_data)
                self._config = Config.parse_obj(merged)
                with self.config_path.open("w", encoding="utf-8") as f:
                    yaml.safe_dump(
                        self._config.dict(),
                        f,
                        allow_unicode=True,
                        sort_keys=False,
                        default_flow_style=False,
                    )
            except Exception as e:
                logger.exception(f"更新配置文件时发生错误: {e}")
                self._config = Config.parse_obj(current_dump)

    def initialize(self, cli_force_web: bool = False):
        """根据命令行参数或配置文件启用配置热重载"""
        self._web_mode_enabled = cli_force_web or self._config.web.enabled
        if self._web_mode_enabled:
            self._start_watcher()

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

    def get_feed_downloader(self, feed_name: str) -> Literal["aria2", "qbittorrent"]:
        """获取指定RSS源的下载器类型"""
        for feed in self.feeds:
            if feed.name == feed_name:
                return feed.downloader

        return "aria2"

    def get_config_version(self) -> int:
        """获取当前配置的版本号"""
        with self._lock:
            return self._config_version

    def _start_watcher(self):
        """启动后台线程监控文件变化"""
        from .logger import logger

        def watch():
            while True:
                try:
                    mtime = self.config_path.stat().st_mtime
                    if mtime != self._last_mtime:
                        with self._lock:
                            self._config = self._load_or_create()
                            self._last_mtime = mtime
                            self._config_version += 1  # 配置重载时，版本号+1
                        logger.info(f"配置文件已重新加载: {self.config_path}")
                except FileNotFoundError:
                    pass
                time.sleep(5)  # 检查间隔

        threading.Thread(target=watch, daemon=True).start()


config = ConfigManager()
