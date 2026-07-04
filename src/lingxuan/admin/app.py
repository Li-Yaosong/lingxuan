"""Admin FastAPI sub-app: independent port, SPA mount, route aggregation.

Creates a ``FastAPI`` instance wired to the DI Container via ``deps.py``.
REST routes live under ``/admin/api``, WebSocket routes under ``/admin/ws``,
and the SPA static build (if present) is served at ``/admin``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from lingxuan.admin.deps import set_container

if TYPE_CHECKING:
    from lingxuan.container import Container


def create_admin_app(container: Container) -> FastAPI:
    """Build and return the admin FastAPI application.

    The caller (bootstrap) is responsible for starting it on an
    independent port via ``uvicorn.Server``.
    """
    # Wire DI container into FastAPI dependencies
    set_container(container)

    app = FastAPI(
        title="灵轩管理端",
        docs_url="/admin/api/docs",
        openapi_url="/admin/api/openapi.json",
        redoc_url=None,
    )

    # ── CORS ─────────────────────────────────────────────────────────────
    # Default: same-origin / localhost only.  Configurable via ADMIN_CORS_ORIGINS
    # env var if the SPA dev server runs on a different origin.
    cors_origins = _cors_origins(container)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health check (no auth required) ──────────────────────────────────
    @app.get("/admin/api/health", tags=["health"])
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # ── SECRET_KEY startup guard ─────────────────────────────────────────
    # If SECRET_KEY is empty, auth endpoints will fail at request time.
    # We log a warning at startup so the operator knows immediately.
    secret_key = container.config.get_str("SECRET_KEY")
    if not secret_key:
        import logging

        logging.getLogger("lingxuan.admin").warning(
            "SECRET_KEY is not configured — admin auth endpoints will reject "
            "all requests. Set SECRET_KEY in .env or environment."
        )

    # ── Route aggregation ────────────────────────────────────────────────
    from lingxuan.admin.routes import auth as auth_routes
    from lingxuan.admin.routes import config as config_routes

    app.include_router(auth_routes.router, prefix="/admin/api")
    app.include_router(config_routes.router, prefix="/admin/api")

    # ── SPA static files ─────────────────────────────────────────────────
    _mount_spa(app, container)

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cors_origins(container: Container) -> list[str]:
    """Build the allowed CORS origins list.

    By default only same-origin and localhost are allowed.  If the env
    var ``ADMIN_CORS_ORIGINS`` is set (comma-separated), those origins
    are added — useful when the SPA dev server runs on e.g.
    ``http://localhost:5173``.
    """
    import os

    origins: list[str] = [
        "http://127.0.0.1:8081",
        "http://localhost:8081",
    ]
    extra = os.environ.get("ADMIN_CORS_ORIGINS", "")
    if extra:
        for origin in extra.split(","):
            origin = origin.strip()
            if origin and origin not in origins:
                origins.append(origin)
    return origins


def _mount_spa(app: FastAPI, container: Container) -> None:
    """Mount the SPA static build directory at ``/admin`` if it exists.

    Looks for ``web/dist/`` relative to the configured ``DATA_ROOT``.
    When present, ``StaticFiles`` serves the build and a catch-all
    fallback serves ``index.html`` for client-side routing.
    """
    data_root = container.config.get_str("DATA_ROOT")
    spa_dir = Path(data_root).parent / "web" / "dist"
    if not spa_dir.is_dir():
        return

    # Mount static assets; the SPA itself is served from /admin/
    app.mount(
        "/admin",
        StaticFiles(directory=str(spa_dir), html=True),
        name="admin-spa",
    )
