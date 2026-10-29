"""复核工作台域路由（§2.3 /reviews）：FR-05-01/02 只读视图——队列 + 时间线。

路由层薄：只做鉴权与出入参，规则在 services/review_flow；错误码 RV-1xxx。
复核动作（受理/通过/退回/终审）不落本域，一律走 /worksheets 复核端点（硬约束 2/9）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.review import PendingReviewOut, ReviewTimelineOut
from ...services import review_flow

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → RV-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, review_flow.NotFound):
        return HTTPException(404, f"RV-1002 {exc}")
    return HTTPException(500, "RV-1999 内部错误")


@router.get("/pending", response_model=list[PendingReviewOut],
            summary="待复核底稿队列（复核工作台主视图；can_take 为 R1 前置标注）")
def pending_queue(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PendingReviewOut]:
    rows = review_flow.pending_queue(db, user)  # 权限拒绝由全局 handler 转 403
    return [PendingReviewOut.model_validate(r) for r in rows]


@router.get("/worksheets/{worksheet_id}/timeline", response_model=ReviewTimelineOut,
            summary="复核时间线（全部轮次意见往返按轮次升序，FR-05-02 留痕可追溯）")
def review_timeline(
    worksheet_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReviewTimelineOut:
    try:
        data = review_flow.review_timeline(db, user, worksheet_id)
    except review_flow.NotFound as exc:
        raise _http_for(exc) from exc
    return ReviewTimelineOut.model_validate(data)
