from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import app.models  # noqa: F401  (register models with Base before migrating)
from app.middleware import SessionAuthMiddleware
from app.migrations import backup_sqlite, run_migrations
from app.routers import (
    accuracy,
    app_auth,
    auth,
    dashboard,
    data,
    evaluate,
    health,
    leagues,
    manual,
    players,
)

FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """Serve the built SPA with a client-side-routing fallback.

    A browser refresh on a route like ``/leagues/manual.1`` reaches the server
    with a path only the frontend router knows; answer such misses with
    ``index.html`` so the SPA can boot and route itself. Real ``/api`` paths
    never get here (the routers claim them first), so a 404 from this mount is
    always a frontend route.
    """

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Unknown /api paths must stay real 404s for API consumers.
            if exc.status_code != 404 or scope.get("path", "").startswith("/api"):
                raise
            return await super().get_response("index.html", scope)
        if response.status_code == 404 and not scope.get("path", "").startswith("/api"):
            response = await super().get_response("index.html", scope)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Imported lazily (not at module top) so app.services.scheduler - and the
    # sync-service modules it imports, e.g. app.services.fantasypros - are
    # first imported *after* run_migrations() has run. Alembic's env.py calls
    # logging.config.fileConfig(...) (disable_existing_loggers=True by
    # default), which silently disables any logger already created at that
    # point; importing eagerly at module top previously caused
    # "app.services.scheduler" (and fantasypros) log records to vanish.
    from app.services.scheduler import shutdown_scheduler, start_scheduler

    # start_scheduler() is idempotent and a no-op when SCHEDULER_ENABLED is
    # false, so this is safe under uvicorn --reload (which re-enters lifespan
    # in the reloader's child process) and under a test suite that builds the
    # app repeatedly (tests force SCHEDULER_ENABLED=False, see conftest.py).
    start_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


def create_app() -> FastAPI:
    backup_sqlite()
    run_migrations()

    app = FastAPI(title="Gridiron HQ", version="0.1.0", lifespan=lifespan)

    # A no-op while OWNER_PASSWORD is unset (the default).
    app.add_middleware(SessionAuthMiddleware)

    app.include_router(health.router)
    app.include_router(app_auth.router)
    app.include_router(auth.router)
    app.include_router(leagues.router)
    app.include_router(manual.router)
    app.include_router(players.router)
    app.include_router(data.router)
    app.include_router(evaluate.router)
    app.include_router(evaluate.projections_router)
    app.include_router(accuracy.router)
    app.include_router(dashboard.router)

    if FRONTEND_DIST.is_dir():
        app.mount(
            "/",
            SPAStaticFiles(directory=FRONTEND_DIST, html=True),
            name="frontend",
        )

    return app


app = create_app()
