"""计划域路由（§2.3 /projects 子资源）：FR-02 重要性水平 R8、风险评级确认、R5 程序包。

路由层薄：只做鉴权与出入参，规则在 services/planning；错误码 PL-1xxx。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.planning import (
    MaterialityIn,
    MaterialityOut,
    PlanExportOut,
    ProgramIn,
    ProgramOut,
    RiskAiOut,
    RiskConfirmIn,
    RiskIn,
    RiskOut,
)
from ...services import planning

router = APIRouter(prefix="/projects", tags=["projects"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → PL-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, planning.NotFound):
        return HTTPException(404, f"PL-1002 {exc}")
    if isinstance(exc, planning.ProjectFrozen):
        return HTTPException(409, f"PL-1005 {exc} 项目暂停期间禁止写操作")
    if isinstance(exc, planning.Locked):
        return HTTPException(409, f"PL-1006 {exc}")
    if isinstance(exc, ValueError):  # engine 参数拒绝（R8/R5，RiskProgramError 同路径）
        return HTTPException(422, str(exc))
    return HTTPException(500, "PL-1999 内部错误")


@router.get("/{project_id}/materiality", response_model=MaterialityOut,
             summary="查询重要性水平（R8：plan.confirm ≥ VIEW；未设置 → 404）")
def get_materiality(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialityOut:
    try:
        row = planning.get_materiality(db, user, project_id)
    except (planning.NotFound) as exc:
        raise _http_for(exc) from exc
    return MaterialityOut.model_validate(row)


@router.post("/{project_id}/materiality", response_model=MaterialityOut,
             summary="录入/修改重要性水平（FR-02-01：R8 计算经 engine，修改全量留痕并重置报批）")
def save_materiality(
    project_id: str,
    body: MaterialityIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialityOut:
    try:
        row = planning.save_materiality(
            db, user, project_id,
            benchmark_type=body.benchmark_type, basis_amount=body.basis_amount,
            exec_ratio_pct=body.exec_ratio_pct, trivial_ratio_pct=body.trivial_ratio_pct,
        )
    except (planning.NotFound, planning.ProjectFrozen, planning.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return MaterialityOut.model_validate(row)


@router.post("/{project_id}/materiality/approve", response_model=MaterialityOut,
             summary="重要性水平报批确认（R8：随计划报合伙人审批，联动计划门条件）")
def approve_materiality(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MaterialityOut:
    try:
        row = planning.approve_materiality(db, user, project_id)
    except (planning.NotFound, planning.ProjectFrozen, planning.Locked) as exc:
        raise _http_for(exc) from exc
    return MaterialityOut.model_validate(row)


@router.post("/{project_id}/risk-ai", response_model=RiskAiOut, status_code=201,
             summary="风险评估 AI（FR-02-02：读入明细账纯规则识别，输出带准则锚点的风险清单草稿）")
def generate_risk_list(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RiskAiOut:
    try:
        risks, orm, skipped, line_count = planning.generate_risk_list(db, user, project_id)
    except (planning.NotFound, planning.Locked, planning.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return RiskAiOut(
        run_no=orm.run_no, status=orm.status, line_count=line_count,
        risks=[RiskOut.model_validate(r) for r in risks], skipped_no_anchor=skipped,
    )


@router.post("/{project_id}/risks", response_model=RiskOut, status_code=201,
             summary="登记财报层风险（FR-02-02：AI 线索默认「待确认」，硬约束 3）")
def add_risk(
    project_id: str,
    body: RiskIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RiskOut:
    try:
        row = planning.add_risk(
            db, user, project_id,
            source=body.source, description=body.description,
            risk_level=body.risk_level, assertion_mapping=body.assertion_mapping,
        )
    except (planning.NotFound, planning.Locked, planning.ProjectFrozen) as exc:
        raise _http_for(exc) from exc
    return RiskOut.model_validate(row)


@router.post("/{project_id}/risks/{risk_id}/confirm", response_model=RiskOut,
             summary="风险评级逐项确认（FR-02-03：可改评级，确认后锁定）")
def confirm_risk(
    project_id: str,
    risk_id: str,
    body: RiskConfirmIn | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RiskOut:
    try:
        row = planning.confirm_risk(
            db, user, risk_id, risk_level=body.risk_level if body else None
        )
    except (planning.NotFound, planning.Locked, planning.ProjectFrozen) as exc:
        raise _http_for(exc) from exc
    return RiskOut.model_validate(row)


@router.post("/{project_id}/risks/{risk_id}/programs", response_model=list[ProgramOut],
             summary="按确认评级生成程序包（R5 经 engine.select_package 裁决）")
def generate_programs(
    project_id: str,
    risk_id: str,
    items: list[ProgramIn],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProgramOut]:
    try:
        rows = planning.generate_programs(
            db, user, risk_id, items=[i.model_dump() for i in items]
        )
    except (planning.NotFound, planning.Locked, planning.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return [ProgramOut.model_validate(r) for r in rows]


@router.post("/{project_id}/plan-export", response_model=PlanExportOut, status_code=201,
             summary="审计计划出稿（FR-02-01：2600 模板保真写回；2610 重要性表头跨表公式联动；PL-1010 指纹拒绝→422）")
def export_plan(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PlanExportOut:
    try:
        template_key, file_key = planning.export_plan(db, user, project_id)
    except (planning.NotFound, planning.Locked, planning.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return PlanExportOut(project_id=project_id, template_key=template_key, file_key=file_key)
