from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routers import health

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="Gridiron HQ", version="0.1.0")

    app.include_router(health.router)

    if FRONTEND_DIST.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=FRONTEND_DIST, html=True),
            name="frontend",
        )

    return app


app = create_app()
