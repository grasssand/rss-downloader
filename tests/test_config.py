import anyio
import pytest
import yaml
from pydantic import ValidationError

from rss_downloader.config import CONFIG_FILE, ConfigManager
from rss_downloader.models import Config

pytestmark = pytest.mark.anyio


async def test_find_config_path_fallback(monkeypatch, tmp_path: anyio.Path):
    """测试当配置文件不存在时，是否回退到默认路径。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # 兼容 Windows
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.chdir(tmp_path)

    found_path = await ConfigManager._find_config_path()

    expected_path = anyio.Path(str(tmp_path), ".config", "rss-downloader", CONFIG_FILE)
    assert found_path == expected_path


async def test_config_creation(tmp_config_file: anyio.Path):
    """测试 _load_or_create 方法的文件创建行为"""
    assert not await tmp_config_file.exists()

    await tmp_config_file.write_text(yaml.safe_dump({}))
    await ConfigManager._load_or_create(tmp_config_file)

    assert await tmp_config_file.exists()

    async with await tmp_config_file.open("r", encoding="utf-8") as f:
        content = await f.read()
        data = yaml.safe_load(content)

    default_config = Config.model_validate({}).model_dump(mode="json")
    assert data == default_config


async def test_config_update_and_reload(test_config: ConfigManager):
    """测试配置更新和热重载"""
    initial_version = test_config.get_config_version()

    # 1. 测试成功更新
    update_data = {"log": {"level": "DEBUG"}}
    await test_config.update(update_data)
    assert test_config.get().log.level == "DEBUG"

    # 2. 测试热重载
    async with anyio.create_task_group() as tg:
        # 在后台启动文件监控任务
        test_config.initialize(tg, cli_force_web=True)
        await anyio.sleep(0.1)  # 短暂休眠，确保 watcher 启动

        # 异步地修改文件内容
        async with await test_config.config_path.open("w") as f:
            await f.write(yaml.safe_dump({"log": {"level": "CRITICAL"}}))

        # 等待足够的时间让 watcher (内部 sleep 5s) 检测到变化
        await anyio.sleep(5.5)

        # 验证配置是否已更新
        assert test_config.get().log.level == "CRITICAL"
        assert test_config.get_config_version() > initial_version

        # 清理后台任务，以便测试可以结束
        tg.cancel_scope.cancel()


async def test_update_failure_rollback(test_config: ConfigManager):
    """测试更新失败时的回滚"""
    await test_config.update({"log": {"level": "CRITICAL"}})

    invalid_data = {"web": {"port": "not-a-number"}}
    with pytest.raises((ValidationError, TypeError)):
        await test_config.update(invalid_data)

    assert test_config.get().log.level == "CRITICAL"


async def test_config_properties_access(test_config: ConfigManager):
    """测试 ConfigManager 的各个属性访问器"""
    await test_config.update(
        {
            "web": {"port": 8081, "enabled": False},
            "log": {"level": "WARNING"},
            "aria2": {"rpc": "http://localhost/aria2"},
            "qbittorrent": {"host": "http://localhost/qb"},
        }
    )

    assert test_config.web.port == 8081
    assert test_config.is_web_mode is False
    assert test_config.log_level == "WARNING"
    assert test_config.aria2 is not None


async def test_config_get_feed_methods_not_found(test_config: ConfigManager):
    """测试 get_feed_* 方法在找不到指定 feed 时的默认返回值"""
    await test_config.update({"feeds": []})

    assert test_config.get_feed_by_name("non_existent_feed") is None
    assert test_config.get_feed_patterns("non_existent_feed") == ([], [])
    assert test_config.get_feed_downloader("non_existent_feed") == "aria2"


async def test_config_get_feed_methods_found(test_config: ConfigManager):
    """测试 get_feed_* 方法在能找到 feed 时返回正确的值"""
    feed_data = {
        "name": "MyTestFeed",
        "url": "http://mytest.com/rss.xml",
        "include": ["1080p"],
        "exclude": ["720p"],
        "downloader": "qbittorrent",
    }
    await test_config.update(
        {
            "qbittorrent": {"host": "http://localhost/"},
            "feeds": [feed_data],
        }
    )

    found_feed = test_config.get_feed_by_name("MyTestFeed")
    assert found_feed.downloader == "qbittorrent"

    include, exclude = test_config.get_feed_patterns("MyTestFeed")
    assert include == ["1080p"]
    assert exclude == ["720p"]

    assert test_config.get_feed_downloader("MyTestFeed") == "qbittorrent"
