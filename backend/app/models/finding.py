"""审计问题与审计报告（FR-03-06/FR-05；PRD §4.5 六状态机、FR-05-05 分歧裁决）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PKMixin, TenantMixin, TimestampMixin, sql_in

# 问题六状态（流转只走 engine/state_machines.FINDING，硬约束 2）
FINDING_STATUSES = ("已归集", "已确认", "处理中", "已解决", "已关闭", "已排除")


class Finding(PKMixin, TenantMixin, TimestampMixin, Base):
    """审计问题。

    AI 归集默认「已归集」；项目经理误报可标记「已排除」并留痕理由；
    涉及报告意见类型的升级合伙人裁决（FR-05-05），裁决前不得关闭。
    """

    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(f"status IN {sql_in(FINDING_STATUSES)}", name="ck_fd_status"),
        CheckConstraint("source IN ('AI 归集','人工')", name="ck_fd_source"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    worksheet_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("worksheets.id"), comment="来源底稿"
    )
    description: Mapped[str] = mapped_column(comment="问题描述（如经营范围与实际销售不符）")
    source: Mapped[str] = mapped_column(String(10), comment="来源（AI 归集/人工）")
    status: Mapped[str] = mapped_column(String(10), default="已归集", comment="六状态")
    responsible_manager_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), comment="责任项目经理"
    )
    escalated: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="涉及报告意见类型，升级合伙人裁决（FR-05-05）"
    )
    escalation_note: Mapped[str | None] = mapped_column(comment="分歧说明（升级登记时填写）")
    escalation_ref: Mapped[str | None] = mapped_column(
        String(100), comment="分歧裁决引用（合伙人裁决记录）；裁决前不得关闭"
    )


class AuditReport(PKMixin, TenantMixin, TimestampMixin, Base):
    """审计报告：附注 AI 产出+人工修订记录；签发需 report.sign 权限（合伙人）。"""

    __tablename__ = "audit_reports"
    __table_args__ = (
        CheckConstraint("status IN ('草稿','会签','已签发')", name="ck_report_status"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    version: Mapped[str] = mapped_column(String(20), comment="报告版本")
    notes_draft_key: Mapped[str | None] = mapped_column(
        String(200), comment="附注草稿对象键（AI 产出经人工复核采信后进入报告稿，FR-05-03）"
    )
    template_key: Mapped[str | None] = mapped_column(
        String(200), comment="报告模板登记键（FR-05-04 模板出稿，templates/report/manifest.json）"
    )
    file_key: Mapped[str | None] = mapped_column(
        String(300), comment="出稿对象键（模板保真写回产物，指纹校验通过后落键）"
    )
    status: Mapped[str] = mapped_column(String(10), default="草稿", comment="状态（草稿/会签/已签发）")
    countersigned_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), comment="会签质控（FR-05-04 审批流）"
    )
    countersigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="会签时间")
    signed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), comment="签发合伙人")
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="签发时间")
