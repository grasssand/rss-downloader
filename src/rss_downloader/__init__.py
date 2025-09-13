"""RSS下载器 - 自动从RSS源获取并下载内容"""

from importlib.metadata import version

import anyio

__version__ = version("rss_downloader")


async def async_main() -> None:
    import argparse

    from .config import ConfigManager
    from .services import AppServices

    parser = argparse.ArgumentParser(description="RSS下载器 - 从RSS源自动下载内容")
    parser.add_argument("-w", "--web", action="store_true", help="启动 Web 界面")
    parser.add_argument("--reset-db", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    config = await ConfigManager.create()
    services = await AppServices.create(config=config)
    try:
        if args.reset_db:
            await services.db.reset()
            services.logger.warning("数据库已重置")

        # 检查是否需要启动Web界面
        if args.web or services.config.web.enabled:
            import uvicorn

            from .app import create_app

            async def run_downloader_periodically():
                """后台定时执行下载任务"""
                while True:
                    try:
                        await services.downloader.run()
                    except Exception:
                        services.logger.exception("下载器后台任务运行时发生错误")
                    interval = services.config.web.interval_hours
                    await anyio.sleep(interval * 3600)

            web_app = create_app(services=services)
            uv_config = uvicorn.Config(
                web_app,
                host=services.config.web.host,
                port=services.config.web.port,
                log_config=None,
            )
            server = uvicorn.Server(uv_config)

            services.logger.info(
                f"启动 Web 界面: http://{uv_config.host}:{uv_config.port}"
            )

            async with anyio.create_task_group() as tg:
                services.config.initialize(tg, cli_force_web=args.web)
                tg.start_soon(run_downloader_periodically)
                tg.start_soon(server.serve)

        else:
            await services.downloader.run()

    except (KeyboardInterrupt, anyio.get_cancelled_exc_class()):
        services.logger.info("程序被用户中断")
    except Exception:
        services.logger.exception("程序运行时发生错误")
    finally:
        await services.close()


def main() -> None:
    try:
        anyio.run(async_main)
    except KeyboardInterrupt:
        print("\n程序已退出。")
