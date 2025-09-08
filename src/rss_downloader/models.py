import time
from datetime import datetime
from typing import Annotated, Any, Literal

from feedparser.util import FeedParserDict
from pydantic import BaseModel, Field, HttpUrl, root_validator, validator


# ==================================
# 配置模型
# ==================================
class LogConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @validator("level", pre=True)
    @classmethod
    def standardize_level_case(cls, v: Any) -> Any:
        """在验证前，将 level 转换为大写"""
        if isinstance(v, str):
            return v.upper()
        return v


class WebConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: Annotated[int, Field(ge=0, le=65535)] = 8000
    interval_hours: Annotated[int, Field(gt=0)] = 6  # 检查 Feeds 更新间隔，单位小时


class Aria2Config(BaseModel):
    rpc: HttpUrl | None = HttpUrl("http://127.0.0.1:6800/jsonrpc", scheme="http")  # type: ignore
    secret: str | None = None
    dir: str | None = None


class QBittorrentConfig(BaseModel):
    host: HttpUrl | None = HttpUrl("http://127.0.0.1:8080", scheme="http")  # type: ignore
    username: str | None = None
    password: str | None = None


EXTRACTOR_DOMAIN_MAP = {
    "mikan": ("mikanime.tv", "mikanani.me"),
    "nyaa": ("nyaa.si",),
    "dmhy": ("dmhy.org",),
}


class FeedConfig(BaseModel):
    name: str
    url: HttpUrl
    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    downloader: Literal["aria2", "qbittorrent"] = "aria2"  # 默认下载器为 aria2
    content_extractor: str = "default"  # or "mikan", "nyaa"...

    @root_validator()
    def set_content_extractor_from_url(cls, values: dict) -> dict:
        """根据 url 自动设置 content_extractor"""
        extractor = values.get("content_extractor")
        url = values.get("url")

        if extractor == "default" and url and url.host:
            hostname = url.host.lower()
            for extractor_name, domains in EXTRACTOR_DOMAIN_MAP.items():
                if any(hostname.endswith(domain) for domain in domains):
                    values["content_extractor"] = extractor_name
                    break

        return values


class Config(BaseModel):
    log: LogConfig = Field(default_factory=LogConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    aria2: Aria2Config | None = None
    qbittorrent: QBittorrentConfig | None = None
    feeds: list[FeedConfig] = Field(default_factory=list)

    @root_validator()
    def check_downloader_config_exists(cls, values: dict) -> dict:
        """检查 Feed 使用的下载器是否已配置"""
        feeds = values.get("feeds", [])
        aria2_config = values.get("aria2")
        qb_config = values.get("qbittorrent")

        used_downloaders = {feed.downloader for feed in feeds}

        if "aria2" in used_downloaders and not aria2_config:
            raise ValueError("Feed 中指定了 aria2 下载器, 但未提供 [aria2] 配置")

        if "qbittorrent" in used_downloaders and not qb_config:
            raise ValueError(
                "Feed 中指定了 qbittorrent 下载器, 但未提供 [qbittorrent] 配置"
            )

        return values

    @validator("feeds")
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

    @classmethod
    def default(cls) -> "Config":
        return cls()


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
    downloader: Literal["aria2", "qbittorrent"] = "aria2"
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


class MikanEntry(ParsedItem):
    """解析 Mikan RSS 源模型"""

    @root_validator(pre=True)
    @classmethod
    def pre_process(cls, values: Any) -> Any:
        item_id = values.get("id")
        if item_id and item_id.startswith("http"):
            url = item_id
        else:
            url = values.get("link")

        download_url = None
        # Mikan, dmhy 提取下载
        for link in values.get("links", []):  # type: ignore
            if link.get("type") in ["application/x-bittorrent"]:
                download_url = link.get("href")
                break

        # Nyaa 等其他提取下载
        else:
            download_url = values.get("link")

        # 发布时间提取
        published_time = (
            datetime.fromtimestamp(time.mktime(values["published_parsed"]))
            if "published_parsed" in values
            else datetime.now()
        )

        return {
            "title": values.get("title", "No Title"),
            "url": url,
            "download_url": download_url,
            "published_time": published_time,
        }


class DmhyEntry(ParsedItem):
    """解析动漫花园 RSS 源模型"""

    @root_validator(pre=True)
    def pre_process(cls, values: Any) -> dict:
        return MikanEntry.pre_process(values)


class NyaaEntry(ParsedItem):
    """解析动漫花园 RSS 源模型"""

    @root_validator(pre=True)
    def pre_process(cls, values: Any) -> dict:
        return MikanEntry.pre_process(values)


class DefaultEntry(ParsedItem):
    """通用的回退解析模型"""

    @root_validator(pre=True)
    @classmethod
    def pre_process(cls, data: Any) -> Any:
        if not isinstance(data, FeedParserDict):
            return data

        download_url = data.get("link")
        if hasattr(data, "links"):
            for link in data.links:
                if link.rel == "enclosure":
                    download_url = link.href
                    break

        # 发布时间提取
        published_time = (
            datetime.fromtimestamp(time.mktime(data.published_parsed))  # type: ignore
            if hasattr(data, "published_parsed")
            else datetime.now()
        )

        return {
            "title": data.title if hasattr(data, "title") else "No Title",
            "url": data.id if hasattr(data, "id") else download_url,
            "download_url": download_url,
            "published_time": published_time,
        }


ENTRY_PARSER_MAP = {
    "mikan": MikanEntry,
    "dmhy": DmhyEntry,
    "nyaa": NyaaEntry,
    "default": DefaultEntry,
}
