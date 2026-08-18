from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from aura_api.auth import local_auth_middleware
from aura_api.migrations import run_migrations
from aura_api.startup import recover_interrupted_jobs


@asynccontextmanager
async def lifespan(app: FastAPI):
    # aura_api.db builds its engine at import time, so run_migrations opens
    # its own connection rather than reusing that engine.
    run_migrations()
    recover_interrupted_jobs()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AuraAudio API", lifespan=lifespan)
    app.middleware("http")(local_auth_middleware)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    from aura_api.routers import exports, jobs, projects, uploads

    app.include_router(uploads.router, prefix="/v1")
    app.include_router(projects.router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
    app.include_router(exports.router, prefix="/v1")

    return app


app = create_app()
