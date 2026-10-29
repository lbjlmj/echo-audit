"""复核工作台视图层（§5.5 复核工作台 + §12 队列+时间线；FR-05-01/02）。

职责边界（硬约束 2/9）：状态流转不落本域——受理/通过/退回/终审一律走
services/worksheets 端点（engine.WORKSHEET 单点裁决）；本域只提供复核人视角的
两类只读查询——待复核队列与全部轮次意见往返时间线（FR-05-02 全程留痕可追溯）。
R1（编制=复核拒绝）在此仅做 can_take 前置标注供前端禁用按钮，权威拦截仍在
take_review 的 check_preparer_reviewer_separation（core 单点）。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.permissions import Level, assert_allowed, can
from ...models.user import User
from ...models.worksheet import Worksheet, WorksheetReview, WorksheetVersion

# RV-1xxx 复核工作台域错误码
CODE_NOT_FOUND = "RV-1002"


class NotFound(Exception):
    pass


def _latest_version(db: Session, worksheet_id: str) -> WorksheetVersion | None:
    return db.scalar(
        select(WorksheetVersion)
        .where(WorksheetVersion.worksheet_id == worksheet_id)
        .order_by(WorksheetVersion.created_at.desc())
        .limit(1)
    )


def pending_queue(db: Session, operator: User) -> list[dict[str, object]]:
    """待复核底稿队列（worksheet.review_sign ≥ VIEW）：审计员无复核签发权限不进工作台。

    每行带 R1 前置标注 can_take：编制人=当前用户或角色无复核等级（OPERATE 以下）
    一律 False，前端据此禁用受理入口；后端权威拦截在 /worksheets/{id}/review/take。
    """
    assert_allowed(operator.global_role, "worksheet.review_sign", Level.VIEW)

    rows: list[dict[str, object]] = []
    # 队列含待复核与复核中两段：复核中行供受理人继续出具结论（前端按 status 区分操作）
    pending = db.scalars(
        select(Worksheet)
        .where(Worksheet.status.in_(["待复核", "复核中"]))
        .order_by(Worksheet.created_at)
    ).all()
    for ws in pending:
        latest = _latest_version(db, ws.id)
        n_reviews = db.scalar(
            select(func.count(WorksheetReview.id)).where(WorksheetReview.worksheet_id == ws.id)
        ) or 0
        preparer_id = latest.prepared_by if latest else None
        preparer = db.get(User, preparer_id) if preparer_id else None
        rows.append({
            "worksheet_id": ws.id,
            "code": ws.code,
            "title": ws.title,
            "is_important": bool(ws.is_important),
            "current_version": ws.current_version,
            "status": ws.status,
            "project_id": ws.project_id,
            "prepared_by_name": preparer.name if preparer else None,
            "next_round_no": n_reviews + 1,  # 即将进入的复核轮次（FR-05-02 轮次递增）
            # R1 前置标注：编制人=复核人拒绝；无 OPERATE 等级者同样不可受理；
            # 复核中行不再受理（can_take=False），结论出具经 /worksheets conclude 端点
            "can_take": (
                ws.status == "待复核"
                and preparer_id is not None
                and preparer_id != operator.id
                and can(operator.global_role, "worksheet.review_sign", Level.OPERATE)
            ),
        })
    return rows


def review_timeline(db: Session, operator: User, worksheet_id: str) -> dict[str, object]:
    """某底稿复核时间线：全部轮次意见往返按轮次升序（FR-05-02 留痕可追溯）。

    不设权限断言：退回意见须回传编制人（复核退回流程 §5.5），留痕对项目内可见，
    与知识清单等 GET 视图惯例一致；权威写权限由 WS 域复核端点把控。
    """
    ws = db.get(Worksheet, worksheet_id)
    if ws is None:
        raise NotFound(CODE_NOT_FOUND)

    records: list[dict[str, object]] = []
    reviews = db.scalars(
        select(WorksheetReview)
        .where(WorksheetReview.worksheet_id == worksheet_id)
        .order_by(WorksheetReview.round_no.asc())
    ).all()
    for r in reviews:
        reviewer = db.get(User, r.reviewer_id)
        records.append({
            "round_no": r.round_no,
            "reviewer_name": reviewer.name if reviewer else None,
            "conclusion": r.conclusion,
            "opinion": r.opinion,
            "revision_requirement": r.revision_requirement,
            "signature": r.signature,  # 电子签名=复核人+日期+证书号（FR-05-02）
            "reviewed_date": r.reviewed_date,
        })
    return {
        "worksheet_id": ws.id,
        "code": ws.code,
        "title": ws.title,
        "status": ws.status,
        "current_version": ws.current_version,
        "records": records,
    }
