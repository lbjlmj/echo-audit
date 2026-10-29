"""函证域路由（§2.3 /confirmations）：FR-04 台账 / 发出 / 回函自动核对 / 替代程序 / 闭环。

路由层薄：只做鉴权与出入参，规则在 services/confirmations（状态只走 engine，硬约束 2）；
错误码 CF-1xxx。超期扫描为 Beat 系统任务（workers/tasks.confirm_overdue），不暴露 API。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.confirmation import (
    AltIn,
    ConfirmationOut,
    IssueIn,
    LedgerIn,
    LetterOut,
    LetterRenderIn,
    ReplyIn,
    TrailOut,
)
from ...services import confirmations

router = APIRouter(prefix="/confirmations", tags=["confirmations"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → CF-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, confirmations.NotFound):
        return HTTPException(404, f"CF-1002 {exc}")
    if isinstance(exc, confirmations.TransitionRejected):
        return HTTPException(409, str(exc))
    if isinstance(exc, confirmations.ProjectFrozen):
        return HTTPException(409, f"CF-1005 {exc} 项目暂停期间禁止写操作")
    if isinstance(exc, confirmations.Locked):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):  # engine 参数拒绝（单号/文件引用缺失等）
        return HTTPException(422, str(exc))
    return HTTPException(500, "CF-1999 内部错误")


@router.get("", response_model=list[ConfirmationOut],
            summary="函证台账列表（?project_id= 按项目过滤；worksheet.edit ≥ VIEW）")
def list_confirmations(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ConfirmationOut]:
    rows = confirmations.list_confirmations(db, user, project_id)
    return [ConfirmationOut.model_validate(r) for r in rows]


@router.get("/{confirmation_id}/trails", response_model=list[TrailOut],
            summary="单张函证留痕时间线（含 Beat 超期扫描记录，所见即所查）")
def list_confirmation_trails(
    confirmation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[TrailOut]:
    try:
        rows = confirmations.list_trails(db, user, confirmation_id)
    except confirmations.NotFound as exc:
        raise _http_for(exc) from exc
    return [TrailOut.model_validate(r) for r in rows]


@router.post("", response_model=list[ConfirmationOut], status_code=201,
             summary="生成函证台账（FR-04-01：台账项进入「台账已生成」，§4.4）")
def generate_ledger(
    body: LedgerIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ConfirmationOut]:
    try:
        rows = confirmations.generate_ledger(
            db, user, body.project_id,
            items=[i.model_dump() for i in body.items],
        )
    except (confirmations.NotFound, confirmations.Locked, confirmations.ProjectFrozen) as exc:
        raise _http_for(exc) from exc
    return [ConfirmationOut.model_validate(r) for r in rows]


@router.post("/{confirmation_id}/issue", response_model=ConfirmationOut,
             summary="发出函证（登记运单号→「已发出」；顺丰适配器沙箱联调后自动回写）")
def issue_confirmation(
    confirmation_id: str,
    body: IssueIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConfirmationOut:
    try:
        row = confirmations.issue_confirmation(db, user, confirmation_id, waybill_no=body.waybill_no)
    except (
        confirmations.NotFound, confirmations.TransitionRejected,
        confirmations.Locked, confirmations.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return ConfirmationOut.model_validate(row)


@router.post("/{confirmation_id}/reply", response_model=ConfirmationOut,
             summary="回函录入并自动核对（FR-04-03：一致→核对一致；差异→差异处理）")
def register_reply(
    confirmation_id: str,
    body: ReplyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConfirmationOut:
    try:
        row = confirmations.register_reply(
            db, user, confirmation_id,
            reply_amount=body.reply_amount, reply_doc=body.reply_doc,
        )
    except (
        confirmations.NotFound, confirmations.TransitionRejected,
        confirmations.Locked, confirmations.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return ConfirmationOut.model_validate(row)


@router.post("/{confirmation_id}/alt", response_model=ConfirmationOut,
             summary="执行替代程序（FR-04-04：超期未回/差异处理→替代程序）")
def propose_alt_test(
    confirmation_id: str,
    body: AltIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConfirmationOut:
    try:
        row = confirmations.propose_alt_test(
            db, user, confirmation_id, alt_test_doc=body.alt_test_doc
        )
    except (
        confirmations.NotFound, confirmations.TransitionRejected,
        confirmations.Locked, confirmations.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return ConfirmationOut.model_validate(row)


@router.post("/{confirmation_id}/letter", response_model=LetterOut, status_code=201,
             summary="函证件出稿（FR-04-01：询证函模板保真写回，金额不出站；CF-1010 指纹拒绝→422）")
def render_letter(
    confirmation_id: str,
    body: LetterRenderIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> LetterOut:
    try:
        row, file_key, template_key, letter_no = confirmations.render_confirmation_letter(
            db, user, confirmation_id, template_key=body.template_key,
        )
    except (
        confirmations.NotFound, confirmations.Locked, confirmations.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return LetterOut(
        confirmation_id=row.id, template_key=template_key,
        letter_no=letter_no, file_key=file_key,
    )


@router.post("/{confirmation_id}/close", response_model=ConfirmationOut,
             summary="函证闭环（核对一致/替代程序→闭环；全闭环为实质性门 5 准入条件）")
def close_confirmation(
    confirmation_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ConfirmationOut:
    try:
        row = confirmations.close_confirmation(db, user, confirmation_id)
    except (
        confirmations.NotFound, confirmations.TransitionRejected,
        confirmations.Locked, confirmations.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return ConfirmationOut.model_validate(row)
