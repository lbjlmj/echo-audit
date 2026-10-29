"""复核工作台域出入参 DTO（§5.5/§12：待复核队列 + 意见往返时间线；只读视图）。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class PendingReviewOut(BaseModel):
    """待复核队列行：can_take 为 R1 前置标注（编制人=当前用户 → False）。"""

    worksheet_id: str
    code: str
    title: str
    is_important: bool
    current_version: str
    status: str
    project_id: str
    prepared_by_name: str | None = None
    next_round_no: int
    can_take: bool


class ReviewRecordOut(BaseModel):
    """单轮复核痕迹：意见/修订要求/电子签名全程可追溯（FR-05-02）。"""

    round_no: int
    reviewer_name: str | None = None
    conclusion: str | None = None
    opinion: str | None = None
    revision_requirement: str | None = None
    signature: dict[str, Any] | None = None
    reviewed_date: date | None = None


class ReviewTimelineOut(BaseModel):
    worksheet_id: str
    code: str
    title: str
    status: str
    current_version: str
    records: list[ReviewRecordOut]
