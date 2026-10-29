"""问题域路由（§2.3 /findings）：FR-03-06 归集 / §4.5 六状态 / FR-05-05 分歧裁决。

路由层薄：只做鉴权与出入参，规则在 services/findings（状态只走 engine，硬约束 2）；
错误码 FD-1xxx。权限语义（PRD §3）：合伙人查看分歧项 / 项目经理确认与处理 /
审计员反馈说明 / 质控抽查。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...core.permissions import Level, assert_allowed
from ...db import get_db
from ...models.user import User
from ...schemas.finding import (
    FindingAdjudicateIn,
    FindingCollectIn,
    FindingCollectOut,
    FindingEscalateIn,
    FindingOut,
    FindingRejectIn,
    FindingResolveIn,
)
from ...services import findings

router = APIRouter(prefix="/findings", tags=["findings"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → FD-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, findings.NotFound):
        return HTTPException(404, f"FD-1002 {exc}")
    if isinstance(exc, findings.TransitionRejected):
        return HTTPException(409, str(exc))
    if isinstance(exc, findings.ProjectFrozen):
        return HTTPException(409, f"FD-1005 {exc} 项目暂停期间禁止写操作")
    if isinstance(exc, findings.Locked):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):  # engine 参数拒绝（理由/结果/裁决引用缺失等）
        return HTTPException(422, str(exc))
    return HTTPException(500, "FD-1999 内部错误")


@router.post("", response_model=FindingCollectOut, status_code=201,
             summary="问题归集（FR-03-06：人工反馈或 AI 归集批量登记，进「已归集」待项目经理确认）")
def collect_findings(
    body: FindingCollectIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> FindingCollectOut:
    try:
        rows, run_no = findings.collect_findings(
            db, user, body.project_id, body.worksheet_id,
            items=[i.description for i in body.items], source=body.source,
        )
    except (findings.NotFound, findings.Locked, findings.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return FindingCollectOut(
        findings=[FindingOut.model_validate(r) for r in rows], ai_run_no=run_no,
    )


@router.get("", response_model=list[FindingOut],
            summary="问题清单（按项目；合伙人查看分歧项/质控抽查）")
def list_findings(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[FindingOut]:
    assert_allowed(user.global_role, "finding.handle", Level.VIEW)
    return [FindingOut.model_validate(r) for r in findings.list_findings(db, project_id)]


@router.post("/{finding_id}/confirm", response_model=FindingOut,
             summary="确认成立（已归集→已确认，项目经理人工复核标记）")
def confirm_finding(
    finding_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> FindingOut:
    try:
        row = findings.confirm_finding(db, user, finding_id)
    except (
        findings.NotFound, findings.TransitionRejected, findings.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return FindingOut.model_validate(row)


@router.post("/{finding_id}/reject", response_model=FindingOut,
             summary="误报排除（已归集→已排除，必附理由留痕）")
def reject_finding(
    finding_id: str, body: FindingRejectIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> FindingOut:
    try:
        row = findings.reject_finding(db, user, finding_id, reason=body.reason)
    except (
        findings.NotFound, findings.TransitionRejected, findings.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return FindingOut.model_validate(row)


@router.post("/{finding_id}/start", response_model=FindingOut,
             summary="启动处理（已确认→处理中）")
def start_handling(
    finding_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> FindingOut:
    try:
        row = findings.start_handling(db, user, finding_id)
    except (
        findings.NotFound, findings.TransitionRejected, findings.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return FindingOut.model_validate(row)


@router.post("/{finding_id}/resolve", response_model=FindingOut,
             summary="登记解决（处理中→已解决，处理结果必填）")
def resolve_finding(
    finding_id: str, body: FindingResolveIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> FindingOut:
    try:
        row = findings.resolve_finding(db, user, finding_id, resolution=body.resolution)
    except (
        findings.NotFound, findings.TransitionRejected, findings.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return FindingOut.model_validate(row)


@router.post("/{finding_id}/escalate", response_model=FindingOut,
             summary="分歧升级登记（FR-05-05：涉及报告意见类型，升级后不得关闭）")
def escalate_finding(
    finding_id: str, body: FindingEscalateIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> FindingOut:
    try:
        row = findings.escalate_finding(db, user, finding_id, note=body.note)
    except (
        findings.NotFound, findings.TransitionRejected, findings.ProjectFrozen,
        findings.Locked, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return FindingOut.model_validate(row)


@router.post("/{finding_id}/adjudicate", response_model=FindingOut,
             summary="合伙人裁决（FR-05-05：落裁决引用，解除关闭阻塞）")
def adjudicate_finding(
    finding_id: str, body: FindingAdjudicateIn,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
) -> FindingOut:
    try:
        row = findings.adjudicate_finding(db, user, finding_id, adjudication_ref=body.adjudication_ref)
    except (
        findings.NotFound, findings.TransitionRejected, findings.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return FindingOut.model_validate(row)


@router.post("/{finding_id}/close", response_model=FindingOut,
             summary="关闭问题（已解决→已关闭，复核确认；分歧未裁决被 engine 前置拦截）")
def close_finding(
    finding_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> FindingOut:
    try:
        row = findings.close_finding(db, user, finding_id)
    except (
        findings.NotFound, findings.TransitionRejected, findings.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return FindingOut.model_validate(row)
