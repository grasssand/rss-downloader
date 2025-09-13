from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .services import AppServices
from .web import router as web_router


def create_app(services: AppServices) -> FastAPI:
    app = FastAPI()

    # 从传入的容器中获取服务实例，并附加到 app.state
    app.state.services = services

    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )
    app.include_router(web_router)

    return app
