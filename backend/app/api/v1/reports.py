"""报告域路由（§2.3 /reports）：FR-05-03 附注 AI / FR-05-04 模板出稿 + 会签 + 签发。

路由层薄：只做鉴权与出入参，规则在 services/reports（附注 AI 恒为线索走 pipeline 三段式，
硬约束 3；审批流三步全部留痕）。附注的第三段复核标记复用 /worksheets/ai-runs/{run_no}/review。
错误码 RP-1xxx。报告签发后报告门（门 7）由 /quality/gates/报告/pass 串联。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.report import (
    CountersignIn,
    FinalizeIn,
    NoteDraftIn,
    NoteDraftOut,
    ReportOut,
    ReportTemplateOut,
    ReportTrailOut,
)
from ...schemas.worksheet import AiDraftOut
from ...services import reports

router = APIRouter(prefix="/reports", tags=["reports"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → RP-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, reports.NotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, reports.ProjectFrozen):
        return HTTPException(409, f"RP-1005 {exc} 项目暂停期间禁止写操作")
    if isinstance(exc, reports.Locked):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):  # engine 前置拒绝（门未过/附注未采信/分歧未裁决等）
        return HTTPException(422, str(exc))
    return HTTPException(500, "RP-1999 内部错误")


@router.post("/notes-draft", response_model=NoteDraftOut, status_code=201,
             summary="发起附注 AI（FR-05-03：完成门通过后项目经理发起，产物为线索态）")
def generate_note_draft(
    body: NoteDraftIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NoteDraftOut:
    try:
        report, orm = reports.generate_note_draft(db, user, body.project_id)
    except (reports.NotFound, reports.Locked, reports.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return NoteDraftOut(
        report_id=report.id, version=report.version, run_no=orm.run_no, status=orm.status,
    )


@router.get("", response_model=list[ReportOut],
            summary="项目报告清单（附注采信/出稿/会签/签发状态全程可见）")
def list_reports(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReportOut]:
    return [ReportOut.model_validate(r) for r in reports.list_reports(db, project_id)]


@router.get("/{report_id}/ai-runs", response_model=list[AiDraftOut],
            summary="报告的附注 AI 运行线索列表（FR-05-03 第三段采信数据源，线索态醒目）")
def list_report_ai_runs(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AiDraftOut]:
    try:
        rows = reports.list_report_ai_runs(db, report_id)
    except reports.NotFound as exc:
        raise _http_for(exc) from exc
    return [AiDraftOut.model_validate(r) for r in rows]


@router.get("/{report_id}/trails", response_model=list[ReportTrailOut],
            summary="单份报告留痕时间线（附注发起/采信/出稿/会签/签发全程可追溯）")
def list_report_trails(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ReportTrailOut]:
    try:
        rows = reports.list_report_trails(db, report_id)
    except reports.NotFound as exc:
        raise _http_for(exc) from exc
    return [ReportTrailOut.model_validate(r) for r in rows]


@router.get("/templates", response_model=list[ReportTemplateOut],
            summary="已登记报告模板清单（FR-05-04 出稿下拉数据源；数据即代码随版本发布）")
def list_report_templates(
    user: User = Depends(get_current_user),
) -> list[ReportTemplateOut]:
    return [
        ReportTemplateOut(key=key, **entry)
        for key, entry in reports.list_report_templates().items()
    ]


@router.get("/{report_id}/file",
            summary="下载出稿产物（模板保真写回的 xlsx；未出稿 → 422，对象缺失 → 404）")
def download_report_file(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    try:
        report, data = reports.get_report_file(db, report_id)
    except (reports.NotFound, reports.Locked, reports.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    filename = f"audit-report-{report.version}.xlsx"
    return Response(
        content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{report_id}/finalize", response_model=ReportOut,
             summary="报告模板出稿（FR-05-04：附注采信为前置；模板保真写回+结构指纹校验，硬约束 8）")
def finalize_report(
    report_id: str,
    body: FinalizeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportOut:
    try:
        row = reports.finalize_report(db, user, report_id, template_key=body.template_key)
    except (reports.NotFound, reports.Locked, reports.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return ReportOut.model_validate(row)


@router.post("/{report_id}/countersign", response_model=ReportOut,
             summary="质控会签（FR-05-04 审批流：草稿→会签，会签人+意见落库留痕）")
def countersign_report(
    report_id: str,
    body: CountersignIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportOut:
    try:
        row = reports.countersign_report(db, user, report_id, opinion=body.opinion)
    except (reports.NotFound, reports.Locked, reports.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return ReportOut.model_validate(row)


@router.post("/{report_id}/sign", response_model=ReportOut,
             summary="合伙人签发（FR-05-04：须先会签；升级未裁决分歧阻签，FR-05-05）")
def sign_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportOut:
    try:
        row = reports.sign_report(db, user, report_id)
    except (reports.NotFound, reports.Locked, reports.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return ReportOut.model_validate(row)
