"""API v1 路由聚合（TECH_DESIGN §2.3 接口域清单）。

骨架期 11 域集中定义；域扩展时按域拆分独立文件（auth.py / projects.py / ...）。
接口契约：出入参一律 Pydantic Schema；状态变更类接口幂等（请求 ID + Redis 锁）；
错误码按域分段（WS=底稿 CF=函证 FD=问题 PR=项目 RP=报告 QU=质控 KN=知识 AI=AI AD=管理）。
"""
from __future__ import annotations

from fastapi import APIRouter

from ... import __version__
from .anomaly import router as anomaly_router
from .auth import router as auth_router
from .confirmations import router as confirmations_router
from .findings import router as findings_router
from .knowledge import router as knowledge_router
from .materials import router as materials_router
from .planning import router as planning_router
from .projects import router as projects_router
from .quality import router as quality_router
from .reports import router as reports_router
from .reviews import router as reviews_router
from .worksheets import router as worksheets_router

api_v1 = APIRouter(prefix="/api/v1")


def _domain_router(domain: str, code: str) -> APIRouter:
    """生成域路由占位：返回域说明与错误码段，业务端点按 FR 域逐步落位。"""
    r = APIRouter(prefix=f"/{domain}", tags=[domain])

    @r.get("/_meta", summary=f"{domain} 域元信息（骨架占位）")
    def meta() -> dict:
        return {"domain": domain, "error_code_prefix": code, "version": __version__, "status": "skeleton"}

    return r


# auth / projects / planning（挂 /projects 子资源）/ worksheets / reviews / anomaly / confirmations /
# findings / quality / reports / materials / knowledge 已落位
api_v1.include_router(auth_router)
api_v1.include_router(projects_router)
api_v1.include_router(planning_router)
api_v1.include_router(worksheets_router)
api_v1.include_router(reviews_router)
api_v1.include_router(anomaly_router)
api_v1.include_router(confirmations_router)
api_v1.include_router(findings_router)
api_v1.include_router(quality_router)
api_v1.include_router(reports_router)
api_v1.include_router(materials_router)
api_v1.include_router(knowledge_router)

for _domain, _code in [
    ("ai", "AI"),
    ("admin", "AD"),
]:
    api_v1.include_router(_domain_router(_domain, _code))
