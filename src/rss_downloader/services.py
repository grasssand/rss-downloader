import anyio
import httpx

from .config import DATABASE_FILE_NAME, ConfigManager
from .database import Database
from .downloaders import Aria2Client, QBittorrentClient, TransmissionClient
from .logger import LoggerProtocol, setup_logger
from .main import RSSDownloader
from .parser import RSSParser
from .webhook import WebhookService


class AppServices:
    """创建和持有核心服务实例的容器"""

    def __init__(
        self,
        config: ConfigManager,
        logger: LoggerProtocol,
        db: Database,
        rss_downloader: RSSDownloader,
        aria2: Aria2Client | None,
        qbittorrent: QBittorrentClient | None,
        transmission: TransmissionClient | None,
        webhook_service: WebhookService,
        http_client: httpx.AsyncClient,
    ):
        self.config = config
        self.logger = logger
        self.db = db
        self.rss_downloader = rss_downloader
        self.aria2 = aria2
        self.qbittorrent = qbittorrent
        self.transmission = transmission
        self.webhook_service = webhook_service
        self._http_client = http_client

        self.download_clients = [
            c for c in [aria2, qbittorrent, transmission] if c is not None
        ]

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

        # 初始化 RSS 解析器
        http_client = httpx.AsyncClient()
        parser = RSSParser(config=config, logger=logger, http_client=http_client)

        # 初始化 Webhook 服务
        webhook_service = WebhookService(
            config=config, logger=logger, http_client=http_client
        )

        # 创建 AppServices 实例
        instance = cls(
            config=config,
            logger=logger,
            db=db,
            rss_downloader=RSSDownloader(
                config, db, logger, parser, None, None, None, webhook_service
            ),
            aria2=None,
            qbittorrent=None,
            transmission=None,
            webhook_service=webhook_service,
            http_client=http_client,
        )

        await instance.reconfigure_downloaders()
        config.set_reconfig_callback(instance.reconfigure_downloaders)

        return instance

    async def reconfigure_downloaders(self):
        """配置下载器"""
        self.logger.info("开始重新配置下载器...")

        config = self.config

        # 关闭现有下载器
        async with anyio.create_task_group() as tg:
            for client in self.download_clients:
                tg.start_soon(client.aclose)

        # 初始化下载器
        aria2_client = None
        if config.aria2 and config.aria2.rpc:
            try:
                aria2_client = await Aria2Client.create(
                    logger=self.logger,
                    rpc_url=str(config.aria2.rpc),
                    secret=config.aria2.secret,
                    dir=config.aria2.dir,
                )
            except Exception as e:
                self.logger.error(f"初始化 Aria2 客户端失败，任务将无法下载。({e})")

        qb_client = None
        if config.qbittorrent and config.qbittorrent.host:
            try:
                qb_client = await QBittorrentClient.create(
                    logger=self.logger,
                    host=str(config.qbittorrent.host),
                    username=config.qbittorrent.username,
                    password=config.qbittorrent.password,
                )
            except Exception as e:
                self.logger.error(
                    f"初始化 qBittorrent 客户端失败，任务将无法下载。({e})"
                )

        transmission_client = None
        if config.transmission and config.transmission.host:
            try:
                transmission_client = await TransmissionClient.create(
                    logger=self.logger,
                    host=str(config.transmission.host),
                    username=config.transmission.username,
                    password=config.transmission.password,
                )
            except Exception as e:
                self.logger.error(
                    f"初始化 Transmission 客户端失败，任务将无法下载。({e})"
                )

        # 更新实例中的引用
        self.aria2 = aria2_client
        self.qbittorrent = qb_client
        self.transmission = transmission_client
        self.download_clients = [
            c for c in [aria2_client, qb_client, transmission_client] if c is not None
        ]

        if not self.download_clients:
            self.logger.warning("未配置任何下载器，无法下载内容")

        self.rss_downloader = RSSDownloader(
            config=config,
            database=self.db,
            logger=self.logger,
            parser=self.rss_downloader.parser,
            aria2=self.aria2,
            qbittorrent=self.qbittorrent,
            transmission=self.transmission,
            webhook_service=self.webhook_service,
        )

    async def close(self):
        """关闭所有需要关闭的服务"""
        async with anyio.create_task_group() as tg:
            tg.start_soon(self._http_client.aclose)
            for client in self.download_clients:
                tg.start_soon(client.aclose)

        self.logger.info("服务已关闭。")
