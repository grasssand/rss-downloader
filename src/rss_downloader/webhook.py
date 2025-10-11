import httpx

from .config import ConfigManager
from .logger import LoggerProtocol
from .models import DownloadRecord


class WebhookService:
    def __init__(
        self,
        config: ConfigManager,
        logger: LoggerProtocol,
        http_client: httpx.AsyncClient,
    ):
        self.config = config
        self.logger = logger
        self.http_client = http_client

    async def send(self, record: DownloadRecord):
        """发送下载结果通知到启用的 Webhook"""
        webhooks = self.config.webhooks
        if not webhooks:
            return

        status_text = "✅ 成功" if record.status == 1 else "❌ 失败"
        payload = {
            "content": "**RSS Downloader Notification**",
            "embeds": [
                {
                    "title": record.title,
                    "url": record.download_url,
                    "color": 5814783 if record.status == 1 else 15728640,
                    "fields": [
                        {
                            "name": "**RSS 源**",
                            "value": record.feed_name,
                            "inline": True,
                        },
                        {
                            "name": "**下载器**",
                            "value": record.downloader,
                            "inline": True,
                        },
                        {
                            "name": "**状态**",
                            "value": status_text,
                            "inline": True,
                        },
                        {
                            "name": "**发布日期**",
                            "value": record.published_time.strftime(
                                "%Y-%m-%d %H:%M:%S"
                            ),
                            "inline": True,
                        },
                    ],
                }
            ],
        }

        for hook in webhooks:
            if hook.enabled:
                try:
                    response = await self.http_client.post(
                        str(hook.url), json=payload, timeout=10
                    )
                    response.raise_for_status()
                    self.logger.info(f"Webhook 通知发送成功 - {hook.name}")
                except Exception as e:
                    self.logger.error(f"Webhook 通知发送失败 - {hook.name}: {e}")
