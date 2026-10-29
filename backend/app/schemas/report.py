"""报告域出入参 DTO（FR-05-03 附注 AI / FR-05-04 报告审批流）。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NoteDraftIn(BaseModel):
    project_id: str = Field(min_length=1)


class NoteDraftOut(BaseModel):
    """附注 AI 发起结果：报告建档 + 留痕编号（线索态，复核采信前不进报告稿）。"""

    report_id: str
    version: str
    run_no: str
    status: str


class FinalizeIn(BaseModel):
    template_key: str = Field(min_length=2, max_length=200, description="报告模板对象键（FR-05-04）")


class CountersignIn(BaseModel):
    opinion: str = Field(min_length=2, max_length=500, description="会签意见（必填留痕）")


class ReportTemplateOut(BaseModel):
    """已登记报告模板（templates/report/manifest.json，FR-05-04 下拉数据源）。"""

    key: str
    label: str
    version: str
    file: str


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    version: str
    status: str
    notes_draft_key: str | None = None
    template_key: str | None = None
    file_key: str | None = None
    countersigned_by: str | None = None
    countersigned_at: datetime | None = None
    signed_by: str | None = None
    signed_at: datetime | None = None


class ReportTrailOut(BaseModel):
    """报告留痕时间线（审批流三步 + 附注两步全程可追溯；哈希链字段不出站）。"""

    model_config = ConfigDict(from_attributes=True)

    action: str
    operator: str
    before_snapshot: dict | None = None
    after_snapshot: dict | None = None
    created_at: datetime
