from datetime import datetime, time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError, model_validator

from .downloaders import Aria2Client, QBittorrentClient
from .main import DownloaderError, ItemNotFoundError
from .models import (
    Aria2Config,
    Config,
    ConfigUpdatePayload,
    Downloader,
    QBittorrentConfig,
)
from .services import AppServices

router = APIRouter()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def format_datetime(dt: datetime | None, fmt: str | None = None) -> str:
    """格式化日期时间为字符串"""
    if dt is None:
        return ""
    if fmt is None:
        fmt = "%Y-%m-%d %H:%M:%S"
    return dt.strftime(fmt)


templates.env.filters["strftime"] = format_datetime


def get_services(request: Request) -> AppServices:
    """从 app.state 中获取 AppServices 容器"""
    return request.app.state.services


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
    downloader: Downloader


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    filters: Annotated[SearchFilters, Depends()],
    services: Annotated[AppServices, Depends(get_services)],
):
    """主页，展示下载记录"""
    offset = (filters.page - 1) * filters.limit

    downloads, total = await services.db.search_downloads(
        **filters.model_dump(exclude={"page", "limit"}),
        limit=filters.limit,
        offset=offset,
    )

    total_pages = (total + filters.limit - 1) // filters.limit

    return templates.TemplateResponse(
        request,
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
    services: Annotated[AppServices, Depends(get_services)],
):
    """API: 重新下载一个任务"""
    try:
        await services.downloader.redownload(
            id=payload.id, downloader=payload.downloader
        )
        return {"status": "success", "message": "任务已成功发送到下载器"}
    except ItemNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except DownloaderError as e:
        raise HTTPException(status_code=500, detail=f"任务发送失败: {e}") from e
    except Exception as e:
        services.logger.error(f"重新下载时发生未知错误 (ID: {payload.id})")
        raise HTTPException(
            status_code=500, detail="发生未知服务器错误，请查看日志"
        ) from e


@router.get("/config", response_model=Config)
async def get_config(services: Annotated[AppServices, Depends(get_services)]):
    """API：获取配置"""
    return services.config.get()


@router.put("/config")
async def update_config(
    payload: ConfigUpdatePayload,
    services: Annotated[AppServices, Depends(get_services)],
):
    """API：更新配置"""
    try:
        update_data = payload.model_dump(exclude_unset=True)
        await services.config.update(update_data)
        return {"status": "ok", "message": "配置已成功保存！"}
    except ValidationError as e:
        services.logger.error(f"配置验证失败: {e.errors()}")
        raise HTTPException(status_code=422, detail=e.errors()) from e
    except Exception as e:
        services.logger.error(f"配置更新失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {e}") from e


@router.get("/config-page", response_class=HTMLResponse)
async def config_page(request: Request):
    """配置页面"""
    return templates.TemplateResponse(request, "config.html", {"request": request})


@router.post("/test-downloader/aria2")
async def test_aria2_connection(
    data: Aria2Config,
    services: Annotated[AppServices, Depends(get_services)],
):
    """测试 Aria2 连接"""
    try:
        client = await Aria2Client.create(
            logger=services.logger,
            http_client=services.http_client,
            rpc_url=str(data.rpc),
            secret=data.secret,
        )
        result = await client.get_version()
        if "error" in result:
            raise ValueError(result["error"]["message"])
        return {
            "status": "success",
            "version": result.get("result", {}).get("version", "未知"),
        }
    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        services.logger.error("测试 Aria2 连接失败")
        raise HTTPException(status_code=500, detail=f"连接失败: {e}") from e


@router.post("/test-downloader/qbittorrent")
async def test_qbittorrent_connection(
    data: QBittorrentConfig,
    services: Annotated[AppServices, Depends(get_services)],
):
    """测试 qBittorrent 连接"""
    try:
        client = await QBittorrentClient.create(
            logger=services.logger,
            http_client=services.http_client,
            host=str(data.host),
            username=data.username,
            password=data.password,
        )
        result = await client.get_version()
        return {"status": "success", "version": result["version"]}
    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        services.logger.error("测试 qBittorrent 连接失败")
        raise HTTPException(status_code=500, detail=f"连接失败: {e}") from e
