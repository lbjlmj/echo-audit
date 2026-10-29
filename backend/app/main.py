"""Echo 审计作业平台 FastAPI 入口。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__
from .api.v1.router import api_v1
from .core.config import get_settings
from .core.permissions import PermissionDenied


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="中小事务所智能审计作业系统（架构硬约束见 AGENTS.md / TECH_DESIGN v1.1）",
    )
    app.include_router(api_v1)

    @app.exception_handler(PermissionDenied)
    def permission_denied(_request, exc: PermissionDenied) -> JSONResponse:
        """权限后端统一裁决（硬约束 5）：RBAC 拒绝一律 403。"""
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.get("/health", tags=["ops"])
    def health() -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "llm_enabled": settings.llm_enabled,  # 大模型通道默认关闭（§15-15）
        }

    return app


app = create_app()
