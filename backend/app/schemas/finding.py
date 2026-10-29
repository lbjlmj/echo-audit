"""问题域出入参 DTO（FR-03-06 归集 + §4.5 六状态 + FR-05-05 分歧裁决）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FindingSource = Literal["人工", "AI 归集"]


class FindingItemIn(BaseModel):
    description: str = Field(min_length=4, max_length=2000, description="问题描述")


class FindingCollectIn(BaseModel):
    project_id: str = Field(min_length=1)
    worksheet_id: str = Field(min_length=1, description="来源底稿")
    items: list[FindingItemIn] = Field(min_length=1, description="归集问题条目")
    source: FindingSource = "人工"


class FindingRejectIn(BaseModel):
    reason: str = Field(min_length=2, max_length=500, description="误报排除理由（§4.5 留痕）")


class FindingResolveIn(BaseModel):
    resolution: str = Field(min_length=2, max_length=2000, description="处理结果")


class FindingEscalateIn(BaseModel):
    note: str = Field(min_length=2, max_length=1000, description="分歧说明（FR-05-05）")


class FindingAdjudicateIn(BaseModel):
    adjudication_ref: str = Field(min_length=2, max_length=100, description="合伙人裁决记录引用")


class FindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    worksheet_id: str
    description: str
    source: FindingSource
    status: str
    responsible_manager_id: str | None = None
    escalated: bool = False
    escalation_note: str | None = None
    escalation_ref: str | None = None
    created_at: datetime | None = None


class FindingCollectOut(BaseModel):
    """归集响应：清单 + AI 归集时的留痕编号（线索态 AIRun，供复核标记反查）。"""

    findings: list[FindingOut]
    ai_run_no: str | None = None
