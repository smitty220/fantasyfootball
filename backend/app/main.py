from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import app.models  # noqa: F401  (register models with Base before migrating)
from app.migrations import run_migrations
from app.routers import auth, data, evaluate, health, leagues, manual, players

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def create_app() -> FastAPI:
    run_migrations()

    app = FastAPI(title="Gridiron HQ", version="0.1.0")

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(leagues.router)
    app.include_router(manual.router)
    app.include_router(players.router)
    app.include_router(data.router)
    app.include_router(evaluate.router)

    if FRONTEND_DIST.is_dir():
        app.mount(
            "/",
            StaticFiles(directory=FRONTEND_DIST, html=True),
            name="frontend",
        )

    return app


app = create_app()
