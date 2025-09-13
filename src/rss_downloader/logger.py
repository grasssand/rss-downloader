import logging
from datetime import datetime
from typing import Any, Protocol

from loguru import logger


class LoggerProtocol(Protocol):
    """定义了一个 Logger 对象应该具备的方法，用于类型提示。"""

    def trace(self, message: str, *args: Any, **kwargs: Any) -> None: ...
    def debug(self, message: str, *args: Any, **kwargs: Any) -> None: ...
    def info(self, message: str, *args: Any, **kwargs: Any) -> None: ...
    def success(self, message: str, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, message: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, message: str, *args: Any, **kwargs: Any) -> None: ...
    def critical(self, message: str, *args: Any, **kwargs: Any) -> None: ...
    def exception(self, message: str, *args: Any, **kwargs: Any) -> None: ...


class DummyLogger:
    """一个什么都不做的备用 Logger，用于解决循环依赖和类型检查问题。"""

    def trace(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def success(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def critical(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


class InterceptHandler(logging.Handler):
    """拦截标准日志的处理器，将其重定向到loguru"""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # 从调用栈中找到正确的位置
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


async def setup_logger(config):
    """配置日志系统"""

    log_config = config.get().log
    log_level = log_config.level

    # 配置日志文件
    log_dir = config.config_path.parent / "logs"
    await log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"rss_downloader_{datetime.now().strftime('%Y%m%d')}.log"

    # 移除默认的处理器
    logger.remove()

    # 添加控制台输出
    logger.add(
        lambda msg: print(msg, end=""),
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
    )

    # 添加文件输出
    logger.add(
        log_file,
        rotation="10 MB",  # 当日志文件达到10MB时轮转
        retention=5,  # 保留最近5个日志文件
        compression="zip",  # 压缩旧的日志文件
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level=log_level,
        encoding="utf-8",
    )

    # 配置拦截器以统一日志格式
    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO, force=True)

    return logger
