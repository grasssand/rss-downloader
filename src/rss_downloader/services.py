from pathlib import Path

from .config import ConfigManager
from .database import Database
from .logger import LoggerProtocol, setup_logger
from .main import RSSDownloader


class AppServices:
    """创建和持有核心服务实例的容器"""

    def __init__(self, config: ConfigManager, db_path: Path):
        self.config = config

        # 创建实例
        self.logger: LoggerProtocol = setup_logger(config=self.config)  # type: ignore
        self.config.set_logger(self.logger)

        self.db: Database = Database(db_path=db_path, logger=self.logger)
        self.downloader: RSSDownloader = RSSDownloader(
            config=self.config, database=self.db, logger=self.logger
        )
