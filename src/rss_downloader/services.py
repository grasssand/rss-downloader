import httpx

from .config import DATABASE_FILE_NAME, ConfigManager
from .database import Database
from .downloaders import Aria2Client, QBittorrentClient
from .logger import LoggerProtocol, setup_logger
from .main import RSSDownloader
from .parser import RSSParser


class AppServices:
    """创建和持有核心服务实例的容器"""

    def __init__(
        self,
        config: ConfigManager,
        logger: LoggerProtocol,
        db: Database,
        downloader: RSSDownloader,
        aria2: Aria2Client | None,
        qbittorrent: QBittorrentClient | None,
        http_client: httpx.AsyncClient,
    ):
        self.config = config
        self.logger = logger
        self.db = db
        self.downloader = downloader
        self.aria2 = aria2
        self.qbittorrent = qbittorrent
        self.http_client = http_client

    @classmethod
    async def create(cls, config: ConfigManager) -> "AppServices":
        """异步创建并初始化所有应用服务。"""
        # 数据库路径
        db_path = config.config_path.parent / DATABASE_FILE_NAME

        # 初始化日志
        logger: LoggerProtocol = await setup_logger(config=config)  # type: ignore
        config.set_logger(logger)

        # 初始化数据库
        db = await Database.create(db_path=db_path, logger=logger)

        # 创建共享的 HTTP 客户端
        http_client = httpx.AsyncClient()

        # 初始化下载器客户端
        aria2_client = None
        aria2_config = config.aria2
        if aria2_config and aria2_config.rpc:
            try:
                aria2_client = await Aria2Client.create(
                    logger=logger,
                    http_client=http_client,
                    rpc_url=str(aria2_config.rpc),
                    secret=aria2_config.secret,
                    dir=aria2_config.dir,
                )
            except Exception as e:
                logger.error(f"初始化 Aria2 客户端失败，任务将无法下载。({e})")

        qb_client = None
        qb_config = config.qbittorrent
        if qb_config and qb_config.host:
            try:
                qb_client = await QBittorrentClient.create(
                    logger=logger,
                    http_client=http_client,
                    host=str(qb_config.host),
                    username=qb_config.username,
                    password=qb_config.password,
                )
            except Exception as e:
                logger.error(f"初始化 qBittorrent 客户端失败，任务将无法下载。({e})")

        # 初始化 RSS 解析器
        parser = RSSParser(config=config, logger=logger, http_client=http_client)

        # 初始化核心下载调度器
        rss_downloader = RSSDownloader(
            config=config,
            database=db,
            logger=logger,
            parser=parser,
            aria2=aria2_client,
            qbittorrent=qb_client,
        )

        return cls(
            config=config,
            logger=logger,
            db=db,
            downloader=rss_downloader,
            aria2=aria2_client,
            qbittorrent=qb_client,
            http_client=http_client,
        )

    async def close(self):
        """关闭所有需要关闭的服务"""
        self.logger.info("正在关闭共享的 HTTP 客户端...")
        await self.http_client.aclose()
        self.logger.info("服务已关闭。")
