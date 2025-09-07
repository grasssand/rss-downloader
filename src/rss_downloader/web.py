from datetime import datetime, time
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from .config import Config, config
from .database import db
from .downloaders import (
    Aria2Client,
    Aria2TestPayload,
    QBittorrentClient,
    QBittorrentTestPayload,
)
from .logger import logger
from .main import rss_downloader

app = FastAPI(title="RSS下载器管理界面")

# 设置静态文件和模板
templates_dir = Path(__file__).parent / "templates"
static_dir = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(templates_dir))
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


def format_datetime(value) -> str:
    """格式化日期时间为字符串"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return value

    return str(value)


templates.env.filters["strftime"] = format_datetime


@app.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    title: str | None = None,
    feed_name: str | None = None,
    downloader: Annotated[str | None, Query(pattern="^(aria2|qbittorrent)?$")] = None,
    status: Annotated[int | None, Query(ge=0, le=1)] = None,
    mode: Annotated[int | None, Query(ge=0, le=1)] = None,
    published_start_time: datetime | None = None,
    published_end_time: datetime | None = None,
    download_start_time: datetime | None = None,
    download_end_time: datetime | None = None,
):
    """主页，展示下载记录"""
    offset = (page - 1) * limit

    # 自动交换不合理的日期范围
    if (
        published_start_time
        and published_end_time
        and (published_start_time > published_end_time)
    ):
        published_start_time, published_end_time = (
            published_end_time,
            published_start_time,
        )
    if (
        download_start_time
        and download_end_time
        and (download_start_time > download_end_time)
    ):
        download_start_time, download_end_time = (
            download_end_time,
            download_start_time,
        )
    if published_end_time:
        published_end_time = datetime.combine(published_end_time.date(), time.max)
    if download_end_time:
        download_end_time = datetime.combine(download_end_time.date(), time.max)

    try:
        # 获取数据
        downloads, total = db.search_downloads(
            title=title,
            feed_name=feed_name,
            downloader=downloader,
            status=status,
            mode=mode,
            published_start_time=published_start_time,
            published_end_time=published_end_time,
            download_start_time=download_start_time,
            download_end_time=download_end_time,
            limit=limit,
            offset=offset,
        )

        # 计算分页
        total_pages = (total + limit - 1) // limit

        query_params = {
            "title": title,
            "feed_name": feed_name,
            "downloader": downloader,
            "status": status,
            "mode": mode,
            "published_start_time": published_start_time,
            "published_end_time": published_end_time,
            "download_start_time": download_start_time,
            "download_end_time": download_end_time,
            "page": page,
            "limit": limit,
        }

        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "downloads": downloads,
                "page": page,
                "offset": offset,
                "total": total,
                "total_pages": total_pages,
                "query": query_params,
            },
        )
    except Exception as e:
        logger.exception("获取下载记录失败")
        return templates.TemplateResponse(
            "error.html", {"request": request, "message": str(e)}
        )


@app.post("/redownload")
async def redownload_item(request: Request):
    """API: 重新下载一个任务"""
    try:
        data = await request.json()
        record_id = data.get("id")
        downloader = data.get("downloader")

        if not all([record_id, downloader]):
            raise HTTPException(status_code=400, detail="缺少参数 id 或 downloader")

        success = rss_downloader.redownload(id=int(record_id), downloader=downloader)

        if success:
            return {"status": "success", "message": "任务已成功发送到下载器"}
        else:
            logger.error(
                f"发送任务到 {downloader} 失败 (ID: {record_id})，请检查下载器配置和日志。"
            )
            raise HTTPException(
                status_code=500, detail="任务发送失败，请检查下载器配置和日志"
            )
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.exception("重新下载时发生意外错误")
        raise HTTPException(status_code=500, detail=f"服务器发生意外错误: {e}")


@app.get("/config", response_model=Config)
def get_config():
    """API：获取配置"""
    return config.get()


@app.put("/config")
def update_config(new_cfg: Config):
    """API：更新配置"""
    try:
        config.update(new_cfg.model_dump())
        return {"status": "ok"}
    except ValidationError as e:
        logger.error(f"配置验证失败: {e.errors()}")
        raise HTTPException(status_code=422, detail=e.errors())
    except Exception as e:
        logger.error(f"配置更新失败: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/config-page", response_class=HTMLResponse)
def config_page(request: Request):
    """配置页面"""
    return templates.TemplateResponse("config.html", {"request": request})


@app.post("/test-downloader/{downloader_name}")
async def test_downloader_connection(downloader_name: str, request: Request):
    """测试下载器连接"""
    payload = await request.json()

    try:
        if downloader_name == "aria2":
            data = Aria2TestPayload.model_validate(payload)
            if not data.rpc:
                raise ValueError("RPC 地址不能为空")

            client = Aria2Client(rpc_url=str(data.rpc), secret=data.secret)
            result = client.get_version()
            if "error" in result:
                raise ValueError(result["error"]["message"])
            return {
                "status": "success",
                "version": result.get("result", {}).get("version", "未知"),
            }

        elif downloader_name == "qbittorrent":
            data = QBittorrentTestPayload.model_validate(payload)
            if not data.host:
                raise ValueError("Host 不能为空")

            client = QBittorrentClient(
                host=str(data.host), username=data.username, password=data.password
            )
            result = client.get_version()
            if "error" in result:
                raise ValueError(result["error"])
            return {"status": "success", "version": result["version"]}

        else:
            raise HTTPException(status_code=404, detail="未知的下载器类型")

    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"测试 {downloader_name} 连接失败")
        raise HTTPException(status_code=500, detail=f"连接失败: {e}")


def run_web_server(host: str = "127.0.0.1", port: int = 8000):
    """运行Web服务器"""
    import uvicorn

    logger.info(f"启动Web服务器在 http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_config=None)
