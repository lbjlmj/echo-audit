"""全量异常 AI 出入参 DTO（FR-03-04：评分/抽凭清单/调整/执行回填）。"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SampleSource = Literal["评分Top10%", "科目Top5", "保底20笔", "人工调整"]


class SampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    voucher_line_id: str
    score: float | None = None
    source: SampleSource
    exec_status: Literal["未执行", "已执行"]
    exec_note: str | None = None
    executed_by: str | None = None
    executed_at: datetime | None = None


class AdjustIn(BaseModel):
    add_ids: list[str] = Field(default_factory=list, description="追加凭证行 id")
    remove_ids: list[str] = Field(default_factory=list, description="移除清单行 id")


class ExecuteIn(BaseModel):
    exec_note: str = Field(min_length=2, max_length=500, description="执行情况说明（必填）")
