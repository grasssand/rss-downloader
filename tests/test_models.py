import pytest
from feedparser.util import FeedParserDict
from pydantic import HttpUrl, ValidationError

from rss_downloader.models import (
    Config,
    DefaultEntry,
    FeedConfig,
    LogConfig,
    MikanEntry,
    TorrentEntryMixin,
)


def test_log_config_validator_with_non_string():
    """测试当 LogConfig 的 level 字段传入非字符串时"""
    with pytest.raises(ValidationError):
        LogConfig.model_validate({"level": 123})

    # 测试传入一个合法的、已经是大写的字符串
    assert LogConfig.model_validate({"level": "DEBUG"}).level == "DEBUG"

    # 测试传入一个需要被转换为大写的字符串
    assert LogConfig.model_validate({"level": "warning"}).level == "WARNING"


def test_feed_config_unique_names_validator():
    """测试 Config 模型中 feeds 列表的名称唯一性校验器。"""
    # 正常情况
    feeds = [
        FeedConfig(name="Feed A", url=HttpUrl("http://a.com")),
        FeedConfig(name="Feed B", url=HttpUrl("http://b.com")),
    ]
    config_data = {"feeds": feeds, "aria2": {"rpc": "http://localhost"}}
    Config.model_validate(config_data)  # 不应抛出异常

    # 包含重复名称的情况
    feeds_with_duplicates = [
        FeedConfig(name="Feed A", url=HttpUrl("http://a.com")),
        FeedConfig(name="Feed A", url=HttpUrl("http://b.com")),  # 名称重复
    ]
    config_data_duplicates = {"feeds": feeds_with_duplicates}

    with pytest.raises(ValidationError, match="Feed 名称重复: Feed A"):
        Config.model_validate(config_data_duplicates)


def test_downloader_config_exists_validator():
    """测试当 feed 使用某个下载器时，该下载器的配置必须存在。"""
    # 场景1: 使用了 aria2，但没有提供 aria2 配置
    feeds = [FeedConfig(name="Feed A", url=HttpUrl("http://a.com"), downloader="aria2")]
    config_data = {"feeds": feeds, "aria2": None}  # aria2 配置为 None

    with pytest.raises(
        ValidationError, match="Feed 中指定了 aria2 下载器, 但未提供 \\[aria2\\] 配置"
    ):
        Config.model_validate(config_data)

    # 场景2: 使用了 qbittorrent，但没有提供 qbittorrent 配置
    feeds_qb = [
        FeedConfig(name="Feed B", url=HttpUrl("http://b.com"), downloader="qbittorrent")
    ]
    config_data_qb = {"feeds": feeds_qb, "qbittorrent": None}
    with pytest.raises(
        ValidationError,
        match="Feed 中指定了 qbittorrent 下载器, 但未提供 \\[qbittorrent\\] 配置",
    ):
        Config.model_validate(config_data_qb)

    # 场景3: 使用了 transmission，但没有提供 transmission 配置
    feeds_tr = [
        FeedConfig(
            name="Feed C", url=HttpUrl("http://c.com"), downloader="transmission"
        )
    ]
    config_data_tr = {"feeds": feeds_tr, "transmission": None}

    with pytest.raises(
        ValidationError,
        match="Feed 中指定了 transmission 下载器, 但未提供 \\[transmission\\] 配置",
    ):
        Config.model_validate(config_data_tr)

    # 正常情况: 配置存在
    config_data_ok = {
        "feeds": feeds_qb,
        "qbittorrent": {"host": "http://localhost:8080"},
    }
    Config.model_validate(config_data_ok)  # 不应抛出异常


def test_feed_config_auto_set_extractor():
    """测试 FeedConfig 是否能根据 URL 自动设置 content_extractor。"""
    # Mikan URL
    feed_mikan = FeedConfig(name="Mikan", url=HttpUrl("https://mikanime.tv/rss.xml"))
    assert feed_mikan.content_extractor == "mikan"

    # Nyaa URL
    feed_nyaa = FeedConfig(name="Nyaa", url=HttpUrl("https://nyaa.si/?page=rss"))
    assert feed_nyaa.content_extractor == "nyaa"

    # 未知 URL
    feed_default = FeedConfig(name="Default", url=HttpUrl("http://example.com/rss"))
    assert feed_default.content_extractor == "default"

    # 用户手动指定时，不应被覆盖
    feed_manual = FeedConfig(
        name="Manual",
        url=HttpUrl("https://mikanime.tv/rss.xml"),
        content_extractor="nyaa",
    )
    assert feed_manual.content_extractor == "nyaa"


def test_torrent_entry_mixin_pre_process():
    """测试 TorrentEntryMixin 的预处理逻辑。"""
    # 场景1：模拟一个典型的 Mikan/dmhy entry
    mikan_entry_data = FeedParserDict(
        {
            "title": "Mikan Anime [01]",
            "link": "https://mikanime.tv/episode/1",
            "links": [
                FeedParserDict(
                    {
                        "type": "application/x-bittorrent",
                        "href": "https://mikanime.tv/download/1.torrent",
                    }
                ),
                FeedParserDict(
                    {"type": "text/html", "href": "https://mikanime.tv/episode/1"}
                ),
            ],
            "published_parsed": (2025, 9, 10, 12, 0, 0, 0, 0, 0),  # time.struct_time
        }
    )

    processed = TorrentEntryMixin.pre_process(mikan_entry_data)
    validated = MikanEntry.model_validate(processed)

    assert validated.title == "Mikan Anime [01]"
    assert str(validated.url) == "https://mikanime.tv/episode/1"
    assert str(validated.download_url) == "https://mikanime.tv/download/1.torrent"
    assert validated.published_time.year == 2025

    # 场景2：模拟一个 Nyaa entry (download_url 和 link 相同)
    nyaa_entry_data = FeedParserDict(
        {
            "title": "Nyaa Anime [02]",
            "id": "https://mikanime.tv/episode/2",
            "link": "https://nyaa.si/download/2.torrent",
            "links": [],  # Nyaa 的 enclosure 可能不在 links 数组中
            "published_parsed": (2025, 9, 11, 12, 0, 0, 0, 0, 0),
        }
    )

    processed_nyaa = TorrentEntryMixin.pre_process(nyaa_entry_data)
    validated_nyaa = MikanEntry.model_validate(
        processed_nyaa
    )  # 可以用 MikanEntry 验证，因为它共享 Mixin

    assert str(validated_nyaa.url) == "https://mikanime.tv/episode/2"
    assert str(validated_nyaa.download_url) == "https://nyaa.si/download/2.torrent"


def test_default_entry_pre_process():
    """测试 DefaultEntry 的预处理逻辑。"""
    # 场景1：包含 enclosure link
    entry_with_enclosure = FeedParserDict(
        {
            "title": "Default Anime [03]",
            "id": "id-3",
            "link": "http://example.com/page/link/3",
            "links": [
                FeedParserDict(
                    {"rel": "alternate", "href": "http://example.com/page/alternate/3"}
                ),
                FeedParserDict(
                    {
                        "rel": "enclosure",
                        "href": "http://example.com/download/3.torrent",
                    }
                ),
            ],
            "published_parsed": (2025, 9, 12, 12, 0, 0, 0, 0, 0),
        }
    )

    validated = DefaultEntry.model_validate(entry_with_enclosure)
    assert str(validated.url) == "http://example.com/page/link/3"
    assert str(validated.download_url) == "http://example.com/download/3.torrent"

    # 场景2：不包含 enclosure link, download_url 应回退到 link
    entry_without_enclosure = FeedParserDict(
        {
            "title": "Default Anime [04]",
            "id": "http://example.com/page/4",
            "link": "http://example.com/download/4.torrent",  # 假设 link 就是下载链接
            "links": [],
            "published_parsed": (2025, 9, 13, 12, 0, 0, 0, 0, 0),
        }
    )

    validated2 = DefaultEntry.model_validate(entry_without_enclosure)
    assert str(validated2.download_url) == "http://example.com/download/4.torrent"


def test_entry_pre_process_with_non_dict_input():
    """测试当输入不是 FeedParserDict 时，预处理应直接返回原数据。"""
    # 这个测试用来覆盖 if not isinstance(...) 的分支

    # TorrentEntryMixin
    raw_string = "just a string"
    processed = TorrentEntryMixin.pre_process(raw_string)
    assert processed is raw_string

    # DefaultEntry
    processed_default = DefaultEntry.pre_process(raw_string)
