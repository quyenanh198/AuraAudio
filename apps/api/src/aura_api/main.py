from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="AuraAudio API")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    from aura_api.routers import exports, jobs, projects, scores, uploads

    app.include_router(uploads.router, prefix="/v1")
    app.include_router(projects.router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
    app.include_router(scores.router, prefix="/v1")
    app.include_router(exports.router, prefix="/v1")

    return app


app = create_app()
