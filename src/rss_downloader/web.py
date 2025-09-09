from datetime import datetime, time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError, model_validator

from .config import ConfigManager
from .database import Database
from .downloaders import Aria2Client, QBittorrentClient
from .logger import LoggerProtocol
from .main import (
    DownloaderError,
    ItemNotFoundError,
    RSSDownloader,
)
from .models import Aria2Config, Config, ConfigUpdatePayload, QBittorrentConfig

router = APIRouter()

# 设置静态文件和模板
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def format_datetime(dt: datetime | None, fmt: str | None = None) -> str:
    """格式化日期时间为字符串"""
    if dt is None:
        return ""

    if fmt is None:
        fmt = "%Y-%m-%d %H:%M:%S"

    return dt.strftime(fmt)


templates.env.filters["strftime"] = format_datetime


def get_config_manager(request: Request) -> ConfigManager:
    return request.app.state.config


def get_logger(request: Request) -> LoggerProtocol:
    return request.app.state.logger


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_downloader(request: Request) -> RSSDownloader:
    return request.app.state.downloader


class SearchFilters(BaseModel):
    """封装下载记录搜索查询的参数模型"""

    page: Annotated[int, Query(description="页码", ge=1)] = 1
    limit: Annotated[int, Query(description="每页数量", ge=1, le=100)] = 20
    title: Annotated[str | None, Query(description="标题关键词")] = None
    feed_name: Annotated[str | None, Query(description="RSS源名称")] = None
    downloader: Annotated[
        str | None, Query(description="下载器", pattern="^(aria2|qbittorrent)?$")
    ] = None
    status: Annotated[
        int | None, Query(description="下载状态 (1成功, 0失败)", ge=0, le=1)
    ] = None
    mode: Annotated[
        int | None, Query(description="下载模式 (1手动, 0自动)", ge=0, le=1)
    ] = None
    published_start_time: Annotated[
        datetime | None, Query(description="发布起始时间")
    ] = None
    published_end_time: Annotated[
        datetime | None, Query(description="发布结束时间")
    ] = None
    download_start_time: Annotated[
        datetime | None, Query(description="下载起始时间")
    ] = None
    download_end_time: Annotated[datetime | None, Query(description="下载结束时间")] = (
        None
    )

    @model_validator(mode="after")
    def fix_date_ranges(self) -> "SearchFilters":
        """动修正不合理的日期范围，并将结束日期调整为当天末尾"""
        if (
            self.published_start_time
            and self.published_end_time
            and self.published_start_time > self.published_end_time
        ):
            self.published_start_time, self.published_end_time = (
                self.published_end_time,
                self.published_start_time,
            )

        if (
            self.download_start_time
            and self.download_end_time
            and self.download_start_time > self.download_end_time
        ):
            self.download_start_time, self.download_end_time = (
                self.download_end_time,
                self.download_start_time,
            )

        if self.published_end_time:
            self.published_end_time = datetime.combine(
                self.published_end_time.date(), time.max
            )

        if self.download_end_time:
            self.download_end_time = datetime.combine(
                self.download_end_time.date(), time.max
            )

        return self


class RedownloadRequest(BaseModel):
    """重新下载任务的请求体模型"""

    id: int
    downloader: Literal["aria2", "qbittorrent"]


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    filters: Annotated[SearchFilters, Depends()],
    db: Annotated[Database, Depends(get_db)],
):
    """主页，展示下载记录"""
    offset = (filters.page - 1) * filters.limit

    # 获取数据
    downloads, total = db.search_downloads(
        **filters.model_dump(exclude={"page", "limit"}),
        limit=filters.limit,
        offset=offset,
    )

    # 计算分页
    total_pages = (total + filters.limit - 1) // filters.limit

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "downloads": downloads,
            "page": filters.page,
            "offset": offset,
            "total": total,
            "total_pages": total_pages,
            "query": filters,
        },
    )


@router.post("/redownload")
async def redownload_item(
    payload: RedownloadRequest,
    downloader: Annotated[RSSDownloader, Depends(get_downloader)],
    logger: Annotated[LoggerProtocol, Depends(get_logger)],
):
    """API: 重新下载一个任务"""
    try:
        downloader.redownload(id=payload.id, downloader=payload.downloader)
        return {"status": "success", "message": "任务已成功发送到下载器"}

    except ItemNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except DownloaderError as e:
        raise HTTPException(status_code=500, detail=f"任务发送失败: {e}") from e
    except Exception as e:
        logger.exception(f"重新下载时发生未知错误 (ID: {payload.id})")
        raise HTTPException(
            status_code=500, detail="发生未知服务器错误，请查看日志"
        ) from e


@router.get("/config", response_model=Config)
def get_config(config: Annotated[ConfigManager, Depends(get_config_manager)]):
    """API：获取配置"""
    return config.get()


@router.put("/config")
def update_config(
    payload: ConfigUpdatePayload,
    config: Annotated[ConfigManager, Depends(get_config_manager)],
    logger: Annotated[LoggerProtocol, Depends(get_logger)],
):
    """API：更新配置"""
    try:
        update_data = payload.model_dump(exclude_unset=True)
        config.update(update_data)
        return {"status": "ok", "message": "配置已成功保存！"}
    except ValidationError as e:
        logger.error(f"配置验证失败: {e.errors()}")
        raise HTTPException(status_code=422, detail=e.errors()) from e
    except Exception as e:
        logger.error(f"配置更新失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {e}") from e


@router.get("/config-page", response_class=HTMLResponse)
def config_page(request: Request):
    """配置页面"""
    return templates.TemplateResponse("config.html", {"request": request})


@router.post("/test-downloader/aria2")
async def test_aria2_connection(
    data: Aria2Config,
    logger: Annotated[LoggerProtocol, Depends(get_logger)],
):
    """测试 Aria2 连接"""
    try:
        if not data.rpc:
            raise ValueError("RPC 地址不能为空")

        client = Aria2Client(logger=logger, rpc_url=str(data.rpc), secret=data.secret)
        result = client.get_version()
        if "error" in result:
            raise ValueError(result["error"]["message"])

        return {
            "status": "success",
            "version": result.get("result", {}).get("version", "未知"),
        }

    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("测试 Aria2 连接失败")
        raise HTTPException(status_code=500, detail=f"连接失败: {e}") from e


@router.post("/test-downloader/qbittorrent")
async def test_qbittorrent_connection(
    data: QBittorrentConfig,
    logger: Annotated[LoggerProtocol, Depends(get_logger)],
):
    """测试 qBittorrent 连接"""
    try:
        if not data.host:
            raise ValueError("Host 不能为空")

        client = QBittorrentClient(
            logger=logger,
            host=str(data.host),
            username=data.username,
            password=data.password,
        )
        result = client.get_version()
        if "error" in result:
            raise ValueError(result["error"])

        return {"status": "success", "version": result["version"]}

    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("测试 qBittorrent 连接失败")
        raise HTTPException(status_code=500, detail=f"连接失败: {e}") from e
