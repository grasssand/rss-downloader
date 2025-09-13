import time
from datetime import datetime
from typing import Annotated, Any, Literal, TypeAlias

from feedparser.util import FeedParserDict
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


# ==================================
# 配置模型
# ==================================
class LogConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("level", mode="before")
    @classmethod
    def standardize_level_case(cls, v: Any) -> Any:
        """在验证前，将 level 转换为大写"""
        if isinstance(v, str):
            return v.upper()
        return v


class WebConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=0, le=65535)] = 8000
    interval_hours: Annotated[int, Field(gt=0)] = 6  # 检查 Feeds 更新间隔，单位小时


class Aria2Config(BaseModel):
    rpc: HttpUrl | None = HttpUrl("http://localhost:6800/jsonrpc")
    secret: str | None = None
    dir: str | None = None


class QBittorrentConfig(BaseModel):
    host: HttpUrl | None = HttpUrl("http://localhost:8080")
    username: str | None = None
    password: str | None = None


EXTRACTOR_DOMAIN_MAP = {
    "mikan": ("mikanime.tv", "mikanani.me"),
    "nyaa": ("nyaa.si",),
    "dmhy": ("dmhy.org",),
}

Downloader: TypeAlias = Literal["aria2", "qbittorrent"]


class FeedConfig(BaseModel):
    name: str
    url: HttpUrl
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    downloader: Downloader = "aria2"  # 默认下载器为 aria2
    content_extractor: str = "default"  # or "mikan", "nyaa"...

    @model_validator(mode="after")
    def set_content_extractor_from_url(self) -> "FeedConfig":
        """根据 url 自动设置 content_extractor"""
        if self.content_extractor == "default" and self.url and self.url.host:
            hostname = self.url.host.lower()
            for extractor_name, domains in EXTRACTOR_DOMAIN_MAP.items():
                if any(hostname.endswith(domain) for domain in domains):
                    self.content_extractor = extractor_name
                    break

        return self


class Config(BaseModel):
    log: LogConfig = Field(default_factory=LogConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    aria2: Aria2Config | None = None
    qbittorrent: QBittorrentConfig | None = None
    feeds: list[FeedConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_downloader_config_exists(self) -> "Config":
        used_downloaders = {feed.downloader for feed in self.feeds}

        if "aria2" in used_downloaders and self.aria2 is None:
            raise ValueError("Feed 中指定了 aria2 下载器, 但未提供 [aria2] 配置")

        if "qbittorrent" in used_downloaders and self.qbittorrent is None:
            raise ValueError(
                "Feed 中指定了 qbittorrent 下载器, 但未提供 [qbittorrent] 配置"
            )

        return self

    @field_validator("feeds")
    @classmethod
    def check_unique_feed_names(cls, v: list[FeedConfig]) -> list[FeedConfig]:
        """检查 Feed 名称唯一性"""
        seen = set()
        for feed in v:
            key = feed.name.strip().lower()
            if key in seen:
                raise ValueError(f"Feed 名称重复: {feed.name}")
            seen.add(key)
        return v


class ConfigUpdatePayload(BaseModel):
    """定义了允许通过 API 更新的配置字段"""

    log: LogConfig | None = None
    web: WebConfig | None = None
    aria2: Aria2Config | None = None
    qbittorrent: QBittorrentConfig | None = None
    feeds: list[FeedConfig] | None = None


# ==================================
# downloads 数据表模型
# ==================================
class DownloadRecord(BaseModel):
    id: int | None = None
    title: str = Field(..., min_length=1)
    url: HttpUrl
    download_url: str | HttpUrl
    feed_name: str
    feed_url: HttpUrl
    published_time: datetime
    download_time: datetime
    downloader: Downloader = "aria2"
    status: Literal[0, 1] = 0
    mode: Literal[0, 1] = 0


# ==================================
# Feed Entry 解析模型
# ==================================
class ParsedItem(BaseModel):
    title: str
    url: HttpUrl
    download_url: HttpUrl | str  # 允许字符串以兼容磁力链接
    published_time: datetime


class TorrentEntryMixin:
    @model_validator(mode="before")
    @classmethod
    def pre_process(cls, data: Any) -> Any:
        if not isinstance(data, FeedParserDict):
            return data

        if hasattr(data, "id") and data.id.startswith("http"):  # type: ignore
            url = data.id
        else:
            url = data.get("link")

        download_url = None
        if hasattr(data, "links"):
            # Mikan, dmhy 提取下载
            for link in data.links:
                if link.get("type") in ["application/x-bittorrent"]:
                    download_url = link.href if hasattr(link, "href") else None
                    break

            # Nyaa 等其他提取下载
            else:
                download_url = data.get("link")

        # 发布时间提取
        published_time = (
            datetime.fromtimestamp(time.mktime(data.published_parsed))  # type: ignore
            if hasattr(data, "published_parsed")
            else datetime.now()
        )

        return {
            "title": data.get("title", "No Title"),
            "url": url,
            "download_url": download_url,
            "published_time": published_time,
        }


class MikanEntry(TorrentEntryMixin, ParsedItem):
    """解析 Mikan RSS 源模型"""

    pass


class DmhyEntry(TorrentEntryMixin, ParsedItem):
    """解析动漫花园 RSS 源模型"""

    pass


class NyaaEntry(TorrentEntryMixin, ParsedItem):
    """解析动漫花园 RSS 源模型"""

    pass


class DefaultEntry(ParsedItem):
    """通用的回退解析模型"""

    @model_validator(mode="before")
    @classmethod
    def pre_process(cls, data: Any) -> Any:
        if not isinstance(data, FeedParserDict):
            return data

        if hasattr(data, "id") and data.id.startswith("http"):  # type: ignore
            url = data.id
        else:
            url = data.get("link")

        download_url = data.get("link")
        if hasattr(data, "links"):
            for link in data.links:
                if link.rel == "enclosure":
                    download_url = link.href if hasattr(link, "href") else None
                    break

        # 发布时间提取
        published_time = (
            datetime.fromtimestamp(time.mktime(data.published_parsed))  # type: ignore
            if hasattr(data, "published_parsed")
            else datetime.now()
        )

        return {
            "title": data.get("title", "No Title"),
            "url": url,
            "download_url": download_url,
            "published_time": published_time,
        }


ENTRY_PARSER_MAP = {
    "mikan": MikanEntry,
    "dmhy": DmhyEntry,
    "nyaa": NyaaEntry,
    "default": DefaultEntry,
}
