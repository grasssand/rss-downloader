"""RSS下载器 - 自动从RSS源获取并下载内容"""

from importlib.metadata import version

__version__ = version("rss_downloader")


def main() -> None:
    import argparse

    from .config import config
    from .logger import logger
    from .main import rss_downloader

    parser = argparse.ArgumentParser(description="RSS下载器 - 从RSS源自动下载内容")
    parser.add_argument("-w", "--web", action="store_true", help="启动 Web 界面")
    parser.add_argument("--reset-db", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args()

    try:
        config.initialize(cli_force_web=args.web)

        # 重置数据库
        if args.reset_db:
            from .database import db

            db.reset()
            logger.warning("数据库已重置")

        # 如果配置了启用Web界面，则同时启动Web服务器
        if config.is_web_mode:
            import threading
            import time

            try:
                import uvicorn

                from .web import app
            except ImportError:
                logger.error("Web UI 依赖未安装！请安装 'rss-downloader[web]'")
                return

            logger.info(f"启动 Web 界面: http://{config.web.host}:{config.web.port}")

            # 后台定时执行下载任务
            def run_downloader_periodically():
                while True:
                    try:
                        rss_downloader.run()
                    except Exception:
                        logger.exception("下载器后台任务运行时发生错误")

                    interval = config.web.interval_hours
                    time.sleep(interval * 3600)

            threading.Thread(
                target=run_downloader_periodically,
                daemon=True,
            ).start()

            # 启动Web服务器（主线程）
            uvicorn.run(
                app, host=config.web.host, port=config.web.port, log_config=None
            )

        else:
            # 仅启动下载器
            rss_downloader.run()

    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception:
        logger.exception("程序运行时发生错误")
