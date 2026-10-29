"""底稿域路由（§2.3 /worksheets）：FR-03 AI 初稿三段式 + §4.3 六状态流转。

路由层薄：只做鉴权与出入参，规则在 services/worksheets；错误码 WS-1xxx。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.worksheet import (
    AnchorOut,
    CrossRefsIn,
    CrossRefsOut,
    AiDraftOut,
    AiReviewIn,
    ProjectOptionOut,
    ReviewIn,
    ReviewSheetOut,
    TemplateOptionOut,
    WorksheetCreateIn,
    WorksheetOut,
)
from ...services import worksheets

router = APIRouter(prefix="/worksheets", tags=["worksheets"])


@router.get("", response_model=list[WorksheetOut],
            summary="底稿列表（?project_id= 按项目过滤；worksheet.edit ≥ VIEW）")
def list_worksheets(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[WorksheetOut]:
    rows = worksheets.list_worksheets(db, user, project_id)
    return [WorksheetOut.model_validate(r) for r in rows]


@router.get("/anchors", response_model=list[AnchorOut],
            summary="准则锚点下拉数据源（红线 2：标准号+条款+程序目的）")
def list_anchors(db: Session = Depends(get_db)) -> list[AnchorOut]:
    return [AnchorOut.model_validate(a) for a in worksheets.list_anchors(db)]


@router.get("/templates", response_model=list[TemplateOptionOut],
            summary="底稿模板下拉数据源（模板矩阵 Template 行，随版本发布）")
def list_templates(db: Session = Depends(get_db)) -> list[TemplateOptionOut]:
    return [TemplateOptionOut.model_validate(t) for t in worksheets.list_templates(db)]


@router.get("/projects", response_model=list[ProjectOptionOut],
            summary="底稿工作台可选项目（worksheet.edit ≥ VIEW，不含已归档；编制人可用）")
def list_project_options(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ProjectOptionOut]:
    rows = worksheets.list_project_options(db, user)
    return [ProjectOptionOut.model_validate(r) for r in rows]


@router.get("/{worksheet_id}/ai-runs", response_model=list[AiDraftOut],
            summary="底稿的 AI 运行线索列表（FR-03-02 第三段确认数据源）")
def list_ai_runs(
    worksheet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[AiDraftOut]:
    try:
        rows = worksheets.list_ai_runs(db, user, worksheet_id)
    except worksheets.NotFound as exc:
        raise _http_for(exc) from exc
    return [AiDraftOut.model_validate(r) for r in rows]


@router.get("/{worksheet_id}/file",
            summary="下载底稿当前版本产物（AI 初稿采信后的模板保真写回 xlsx；无版本 → 409）")
def download_worksheet_file(
    worksheet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    try:
        ws, data, media = worksheets.get_worksheet_file(db, worksheet_id)
    except (worksheets.NotFound, worksheets.Locked, worksheets.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    filename = f"worksheet-{ws.code}-{ws.current_version}.xlsx"
    return Response(
        content=data, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → WS-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, worksheets.NotFound):
        return HTTPException(404, f"WS-1002 {exc}")
    if isinstance(exc, worksheets.TransitionRejected):
        return HTTPException(409, str(exc))
    if isinstance(exc, worksheets.ProjectFrozen):
        return HTTPException(409, f"WS-1005 {exc} 项目暂停期间禁止写操作")
    if isinstance(exc, worksheets.Locked):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):  # engine/三层缺口等参数拒绝
        return HTTPException(422, str(exc))
    return HTTPException(500, "WS-1999 内部错误")


@router.post("", response_model=WorksheetOut, status_code=201,
             summary="创建底稿（FR-03-03：准则锚点强制带出，红线 2）")
def create_worksheet(
    project_id: str,
    body: WorksheetCreateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorksheetOut:
    try:
        ws = worksheets.create_worksheet(
            db, user, project_id,
            title=body.title, template_id=body.template_id,
            rule_anchor_id=body.rule_anchor_id, is_important=body.is_important,
        )
    except (worksheets.NotFound, worksheets.Locked, worksheets.ProjectFrozen) as exc:
        raise _http_for(exc) from exc
    return WorksheetOut.model_validate(ws)


@router.post("/{worksheet_id}/ai-draft", response_model=AiDraftOut,
             summary="AI 初稿生成（FR-03-02：三层材料齐备才解锁，产物为线索态）")
def generate_ai_draft(
    worksheet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AiDraftOut:
    try:
        row = worksheets.generate_ai_draft(db, user, worksheet_id)
    except (worksheets.NotFound, worksheets.Locked, worksheets.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return AiDraftOut.model_validate(row)


@router.post("/ai-runs/{run_no}/review", response_model=AiDraftOut,
             summary="AI 输出人工复核标记（红线 1/R2 第三段：采信才落版本链）")
def mark_ai_review(
    run_no: str,
    body: AiReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> AiDraftOut:
    try:
        row = worksheets.mark_ai_review(
            db, user, run_no,
            conclusion=body.conclusion, rejection_reason=body.rejection_reason,
        )
    except (worksheets.NotFound, worksheets.Locked, worksheets.ProjectFrozen, ValueError) as exc:
        raise _http_for(exc) from exc
    return AiDraftOut.model_validate(row)


@router.post("/{worksheet_id}/submit", response_model=WorksheetOut,
             summary="提交复核（草稿→待复核：AI 内容须完成复核标记，§7 R2）")
def submit_worksheet(
    worksheet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorksheetOut:
    try:
        ws = worksheets.submit_worksheet(db, user, worksheet_id)
    except (
        worksheets.NotFound, worksheets.TransitionRejected,
        worksheets.Locked, worksheets.ProjectFrozen,
    ) as exc:
        raise _http_for(exc) from exc
    return WorksheetOut.model_validate(ws)


@router.post("/{worksheet_id}/review/take", response_model=WorksheetOut,
             summary="受理复核（待复核→复核中：编制人=复核人拒绝，R1）")
def take_review(
    worksheet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorksheetOut:
    try:
        ws = worksheets.take_review(db, user, worksheet_id)
    except (
        worksheets.NotFound, worksheets.TransitionRejected,
        worksheets.Locked, worksheets.ProjectFrozen,
    ) as exc:
        raise _http_for(exc) from exc
    return WorksheetOut.model_validate(ws)


@router.post("/{worksheet_id}/review/conclude", response_model=WorksheetOut,
             summary="复核结论（通过/退回：退回必附意见，FR-05-02）")
def conclude_review(
    worksheet_id: str,
    body: ReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorksheetOut:
    try:
        ws = worksheets.conclude_review(
            db, user, worksheet_id,
            conclusion=body.conclusion, opinion=body.opinion,
            revision_requirement=body.revision_requirement,
        )
    except (
        worksheets.NotFound, worksheets.TransitionRejected,
        worksheets.Locked, worksheets.ProjectFrozen,
    ) as exc:
        raise _http_for(exc) from exc
    return WorksheetOut.model_validate(ws)


@router.post("/{worksheet_id}/final-approve", response_model=WorksheetOut,
             summary="终审（复核通过→终审通过：仅重要底稿，§15-11）")
def final_approve(
    worksheet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> WorksheetOut:
    try:
        ws = worksheets.final_approve(db, user, worksheet_id)
    except (
        worksheets.NotFound, worksheets.TransitionRejected,
        worksheets.Locked, worksheets.ProjectFrozen,
    ) as exc:
        raise _http_for(exc) from exc
    return WorksheetOut.model_validate(ws)


@router.put("/{worksheet_id}/cross-refs", response_model=CrossRefsOut,
            summary="交叉索引写入（FR-03-05：草稿态保存时自动校验断链，R7）")
def update_cross_refs(
    worksheet_id: str,
    body: CrossRefsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CrossRefsOut:
    try:
        data = worksheets.update_cross_refs(
            db, user, worksheet_id, cross_refs=[r.model_dump() for r in body.refs]
        )
    except (worksheets.NotFound, worksheets.Locked, worksheets.ProjectFrozen) as exc:
        raise _http_for(exc) from exc
    return CrossRefsOut.model_validate(data)


@router.post("/{worksheet_id}/review-export", response_model=ReviewSheetOut, status_code=201,
             summary="复核核对表出稿（FR-05-02：6700 模板保真写回，须复核通过后；WS-1020 指纹拒绝→422）")
def export_review_sheet(
    worksheet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewSheetOut:
    try:
        template_key, file_key, status = worksheets.export_review_sheet(db, user, worksheet_id)
    except (
        worksheets.NotFound, worksheets.Locked, worksheets.ProjectFrozen, ValueError,
    ) as exc:
        raise _http_for(exc) from exc
    return ReviewSheetOut(
        worksheet_id=worksheet_id, worksheet_status=status,
        template_key=template_key, file_key=file_key,
    )
