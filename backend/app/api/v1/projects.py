"""项目域路由（§2.3 /projects）：FR-01 立项行为链。路由层薄，规则在 services/intake。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...core.permissions import Level, assert_allowed
from ...db import get_db
from ...models.project import Client
from ...models.user import User
from ...schemas.project import (
    ClientOut,
    IndustryTemplateScope,
    IntakeSubmitRequest,
    ProjectOut,
    SelectTemplateIn,
    TeamHoursIn,
    TeamOverviewOut,
)
from ...services import intake

router = APIRouter(prefix="/projects", tags=["projects"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → PR-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, intake.IntakeRejected):
        return HTTPException(422, f"PR-1001 {exc}")
    if isinstance(exc, intake.NotFound):
        return HTTPException(404, f"PR-1002 {exc}")
    if isinstance(exc, intake.MemberConflict):
        msg = str(exc.args[0]) if exc.args else str(exc)
        status = 409 if "PR-1003" in msg else 409
        return HTTPException(status, f"{msg} 重复审批或成员重复登记")
    if isinstance(exc, ValueError):
        return HTTPException(422, str(exc))
    return HTTPException(500, "PR-1999 内部错误")


@router.post("", status_code=201, response_model=ProjectOut, summary="提交承接评价与立项申请（FR-01-01）")
def submit_intake(
    body: IntakeSubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectOut:
    try:
        project = intake.submit_intake(
            db, user, name=body.name, client_id=body.client_id,
            business_type=body.business_type, period=body.period,
            evaluation=body.evaluation.model_dump(),
            pending_members=[m.model_dump() for m in body.members],
            contract=body.contract.model_dump() if body.contract else None,
        )
    except (intake.IntakeRejected, intake.NotFound, intake.MemberConflict) as exc:
        raise _http_for(exc) from exc
    return ProjectOut.model_validate(project)


@router.post("/{project_id}/approve", response_model=ProjectOut, summary="立项审批并自动建档（FR-01-02）")
def approve_intake(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectOut:
    try:
        project = intake.approve_intake(db, user, project_id)
    except (intake.NotFound, intake.MemberConflict) as exc:
        raise _http_for(exc) from exc
    return ProjectOut.model_validate(project)


@router.get("", response_model=list[ProjectOut], summary="项目列表")
def list_projects(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in intake.list_projects(db, user)]


# ---------------------------------------------------------------------------
# FR-02-04 行业模板选择（立项随建档圈定 + 计划阶段圈定/变更）
# ---------------------------------------------------------------------------
@router.get("/industry-templates", response_model=dict[str, IndustryTemplateScope],
            summary="行业模板矩阵（FR-02-04：八类高频行业，随版本发布数据即代码）")
def list_industry_templates(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, IndustryTemplateScope]:
    return {
        t: IndustryTemplateScope.model_validate(s)
        for t, s in intake.list_industry_templates(db, user).items()
    }


@router.post("/{project_id}/industry-template", response_model=ProjectOut,
             summary="计划阶段圈定/变更行业模板（FR-02-04：模板范围重算并全量留痕）")
def select_industry_template(
    project_id: str,
    body: SelectTemplateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectOut:
    try:
        project = intake.select_industry_template(
            db, user, project_id, industry_template=body.industry_template
        )
    except (intake.NotFound, intake.MemberConflict, ValueError) as exc:
        raise _http_for(exc) from exc
    return ProjectOut.model_validate(project)


@router.get("/clients", response_model=list[ClientOut],
            summary="客户列表（立项与项目面板下拉数据源）")
def list_clients(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ClientOut]:
    assert_allowed(user.global_role, "project.approve", Level.VIEW)
    rows = db.scalars(select(Client).order_by(Client.name)).all()
    return [ClientOut.model_validate(c) for c in rows]


# ---------------------------------------------------------------------------
# FR-02-05 团队与工时（项目经理配置；预算/实际对比 + 偏差超 10% 面板告警）
# ---------------------------------------------------------------------------
@router.put("/{project_id}/team-hours", response_model=TeamOverviewOut,
            summary="成员工时配置（FR-02-05：upsert 幂等，配置后回显对比视图）")
def configure_team_hours(
    project_id: str,
    body: TeamHoursIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TeamOverviewOut:
    try:
        intake.configure_team_hours(
            db, user, project_id,
            members=[m.model_dump(exclude_unset=True) for m in body.members],
        )
        data = intake.team_overview(db, user, project_id)
    except (intake.NotFound, intake.MemberConflict) as exc:
        raise _http_for(exc) from exc
    return TeamOverviewOut.model_validate(data)


@router.get("/{project_id}/team", response_model=TeamOverviewOut,
            summary="团队与工时对比视图（FR-02-05：偏差超 10% 告警）")
def team_overview(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TeamOverviewOut:
    try:
        data = intake.team_overview(db, user, project_id)
    except (intake.NotFound, ValueError) as exc:
        raise _http_for(exc) from exc
    return TeamOverviewOut.model_validate(data)
