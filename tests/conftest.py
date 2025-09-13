from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from anyio import Path as AnyioPath
from httpx import ASGITransport, AsyncClient

from rss_downloader.app import create_app
from rss_downloader.config import ConfigManager
from rss_downloader.database import Database
from rss_downloader.logger import LoggerProtocol
from rss_downloader.main import RSSDownloader
from rss_downloader.models import Config
from rss_downloader.services import AppServices

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_logger() -> LoggerProtocol:
    """创建一个功能完整的模拟 logger 对象。"""
    logger = MagicMock(spec=LoggerProtocol)
    # 确保所有可能被调用的方法都存在，并且可以被断言
    for level in [
        "trace",
        "debug",
        "info",
        "success",
        "warning",
        "error",
        "critical",
        "exception",
    ]:
        setattr(logger, level, MagicMock())
    return logger


@pytest.fixture
def tmp_config_file(tmp_path: Path) -> AnyioPath:
    """创建一个临时的、空的配置文件路径。"""
    config_dir = tmp_path / "rss-downloader"
    config_dir.mkdir()
    return AnyioPath(config_dir / "config.yaml")


@pytest.fixture
async def test_config(
    tmp_config_file: AnyioPath, monkeypatch, mock_logger: LoggerProtocol
) -> ConfigManager:
    """创建一个使用临时配置文件和模拟 logger 的异步 ConfigManager 实例。"""

    async def mock_find_path() -> AnyioPath:
        return tmp_config_file

    monkeypatch.setattr(ConfigManager, "_find_config_path", mock_find_path)

    cfg_manager = await ConfigManager.create()
    cfg_manager.set_logger(mock_logger)
    return cfg_manager


@pytest.fixture
async def test_db(
    tmp_path: Path, mock_logger: LoggerProtocol
) -> AsyncIterator[Database]:
    """创建一个使用临时的数据库实例。"""
    db_path = AnyioPath(tmp_path / "test.db")
    db = await Database.create(db_path=db_path, logger=mock_logger)
    yield db
    await db.reset()


@pytest.fixture
def mock_services(mock_logger: LoggerProtocol) -> AppServices:
    """创建一个 AppServices 的完全模拟 (mock) 对象，包含异步 mock 方法。"""
    services = MagicMock(spec=AppServices)

    # 为 ConfigManager 创建一个更精确的 mock
    mock_config = MagicMock(spec=ConfigManager)
    mock_config.get = MagicMock(return_value=Config.model_validate({}))
    mock_config.update = AsyncMock()
    services.config = mock_config

    # 为其他异步方法使用 AsyncMock
    services.db = MagicMock(
        spec=Database, search_downloads=AsyncMock(return_value=([], 0))
    )
    services.downloader = MagicMock(spec=RSSDownloader, redownload=AsyncMock())
    services.logger = mock_logger
    services.http_client = MagicMock(spec=AsyncClient, aclose=AsyncMock())

    return services


@pytest.fixture
async def client(mock_services: AppServices) -> AsyncIterator[AsyncClient]:
    """创建一个 FastAPI 异步测试客户端。"""
    app = create_app(services=mock_services)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
