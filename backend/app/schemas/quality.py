"""质控域出入参 DTO（FR-06：质控 AI 检查 / 阶段门卡控 / 归档）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class QcCheckIn(BaseModel):
    project_id: str = Field(min_length=1)


class InspectionPackIn(BaseModel):
    project_id: str = Field(min_length=1)


class AnchorMapOut(BaseModel):
    """锚点反查行（FR-06-05）：底稿 → 准则依据（红线 2 库端 100% 可反查）。"""

    worksheet_code: str
    title: str
    status: str
    standard_no: str
    clause: str
    purpose: str


class InspectionPackOut(BaseModel):
    """检查应对材料包（FR-06-05）：锚点反查 + 质控报表 + 封包摘要（同批可复算）。"""

    project_id: str
    project_no: str
    anchor_map: list[AnchorMapOut]
    report: dict[str, Any]
    package_digest: str
    generated_at: datetime


class QcCheckOut(BaseModel):
    """检查报告（FR-06-01）：留痕编号 + 检查项结果 + 未通过项清单（线索态）。"""

    run_no: str
    status: str
    output_digest: str | None = None
    checks: dict[str, bool]
    failed_items: list[dict[str, str]]
    passed: bool


class QcRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_no: str
    status: str
    output_digest: str | None = None
    output_key: str | None = None
    triggered_at: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class GatePassIn(BaseModel):
    project_id: str = Field(min_length=1)


class GateSkipIn(BaseModel):
    project_id: str = Field(min_length=1)
    qc_reviewer_id: str = Field(min_length=1, description="双批质控专员 id（R3）")


class GateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    stage: str
    gate_status: str
    conditions_snapshot: dict[str, Any] | None = None
    passed_at: datetime | None = None
    skip_partner_id: str | None = None
    skip_qc_id: str | None = None


class PrecheckIn(BaseModel):
    project_id: str = Field(min_length=1)


class PrecheckOut(BaseModel):
    blocked: bool
    blockers: list[dict[str, Any]]


class ArchiveIn(BaseModel):
    project_id: str = Field(min_length=1)


class ProjectOptionOut(BaseModel):
    """质控驾驶舱项目选项（含已归档/终止——归档调阅场景，/worksheets/projects 不含已归档）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_no: str
    name: str
    status: str


class ArchiveOut(BaseModel):
    project_id: str
    status: str
    stage: str
    archive_package_hash: str
    archive_object_key: str | None = None  # 实包对象键（archives/…/{封包哈希}.zip，可下载）
    archived_worksheets: int
    ai_runs: int
    knowledge_proposals: int = 0  # FR-07-04 经验沉淀提案数（归档自动触发）


class ArchiveVerifyOut(BaseModel):
    """归档实包防篡改校验结果（FR-06-04：成员哈希 + 包库自证，QU-1009 失败 422）。"""

    project_id: str
    ok: bool
    object_key: str
    package_hash: str
    members: int
    zip_sha256: str
    verified_at: datetime


# ---------------------------------------------------------------------------
# §15-16 归档后差错处理链（登记→批准→副本修订→质控复核→重新归档 v(n+1)）
# ---------------------------------------------------------------------------
class CorrectionRegisterIn(BaseModel):
    project_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    affected_worksheet_ids: list[str] = Field(min_length=1, description="受影响底稿 id 清单")


class CorrectionApproveIn(BaseModel):
    approve: bool
    reason: str | None = Field(default=None, description="驳回必附原因")


class CorrectionRevisionIn(BaseModel):
    worksheet_id: str = Field(min_length=1)
    file_key: str = Field(min_length=1, description="修订产物对象键（先上传再登记）")
    change_summary: str = Field(min_length=1)


class CorrectionQcReviewIn(BaseModel):
    conclusion: str = Field(pattern="^(通过|退回)$")
    opinion: str | None = Field(default=None, description="退回必附意见")


class CorrectionOut(BaseModel):
    """差错单（§15-16：新旧包哈希随单留痕，双版本可查）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    code: str
    description: str
    affected_worksheet_ids: list[str]
    status: str
    registered_by: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None
    qc_reviewed_by: str | None = None
    qc_reviewed_at: datetime | None = None
    qc_opinion: str | None = None
    previous_package_hash: str | None = None
    rearchive_package_hash: str | None = None
    rearchived_at: datetime | None = None
    created_at: datetime


class CorrectionArtifactOut(BaseModel):
    """修订产物上传回执（内容寻址键已落存，凭 file_key 登记版本链）。"""

    file_key: str
    size: int
    sha256: str


class CorrectionRevisionOut(BaseModel):
    """副本修订登记回执（版本链追加 v(n+1)，原稿版本锁定保留）。"""

    model_config = ConfigDict(from_attributes=True)

    worksheet_id: str
    version_no: str
    file_key: str
    change_summary: str | None = None


class RearchiveOut(BaseModel):
    """重新归档 v(n+1) 回执：新旧整包哈希 + supersedes 链。"""

    correction_id: str
    correction_code: str
    status: str
    project_id: str
    previous_package_hash: str
    archive_package_hash: str
    archive_object_key: str
    zip_sha256: str
    zip_members: int
    missing_objects: int


class ChecklistExportOut(BaseModel):
    """完成情况核对表出稿结果（FR-06-01；6600 保真写回产物）。"""

    project_id: str
    template_key: str
    file_key: str
