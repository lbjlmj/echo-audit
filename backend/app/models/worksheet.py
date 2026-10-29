"""工作底稿三实体（FR-03/FR-05；PRD §4.3 六状态机、§15-11 重要底稿清单）。"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONField, PKMixin, TenantMixin, TimestampMixin, sql_in

# 底稿六状态（PRD §4.3；流转只走 engine/state_machines.WORKSHEET，硬约束 2）
WORKSHEET_STATUSES = ("草稿", "待复核", "复核中", "复核通过", "终审通过", "已归档")


class Worksheet(PKMixin, TenantMixin, TimestampMixin, Base):
    """工作底稿。

    rule_anchor_id 不可空（红线 2）：底稿必带准则锚点（准则号+条款+程序目的），库端 100% 可反查。
    重要底稿判定清单（触发终审，§15-11）：审计报告与附注、总体策略与重要性水平、
    风险评估总表、关键审计事项、含调整分录、货币资金/收入/关联方、意见分歧涉及底稿。
    """

    __tablename__ = "worksheets"
    __table_args__ = (
        CheckConstraint(f"status IN {sql_in(WORKSHEET_STATUSES)}", name="ck_ws_status"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    code: Mapped[str] = mapped_column(String(32), comment="底稿编号（命名引擎 R6）")
    title: Mapped[str] = mapped_column(String(200), comment="底稿标题")
    template_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("templates.id"))
    rule_anchor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("rule_anchors.id"), comment="准则锚点（不可空，红线 2）"
    )
    current_version: Mapped[str] = mapped_column(String(20), default="v1", comment="当前版本号")
    status: Mapped[str] = mapped_column(String(10), default="草稿", comment="六状态")
    file_key: Mapped[str | None] = mapped_column(String(200), comment="当前文件对象键")
    is_important: Mapped[bool | None] = mapped_column(comment="重要底稿（触发终审，§15-11）")
    cross_refs: Mapped[list[Any] | None] = mapped_column(
        JSONField, comment="交叉索引引用（JSONB 数组，质控 AI 断链校验，R7）"
    )


class WorksheetVersion(PKMixin, TenantMixin, TimestampMixin, Base):
    """底稿版本链（版本链而非覆盖写，归档后不可变）。"""

    __tablename__ = "worksheet_versions"

    worksheet_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("worksheets.id"), index=True
    )
    version_no: Mapped[str] = mapped_column(String(20), comment="版本号")
    file_key: Mapped[str] = mapped_column(String(200), comment="文件对象键")
    change_summary: Mapped[str | None] = mapped_column(String(500), comment="变更摘要")
    prepared_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), comment="编制人")


class WorksheetReview(PKMixin, TenantMixin, TimestampMixin, Base):
    """复核痕迹：全部轮次留痕可追溯（FR-05-02）；电子签名=复核人+日期+证书号。"""

    __tablename__ = "worksheet_reviews"
    __table_args__ = (
        CheckConstraint("conclusion IN ('通过','退回')", name="ck_review_conclusion"),
    )

    worksheet_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("worksheets.id"), index=True
    )
    version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("worksheet_versions.id"), comment="复核针对的版本"
    )
    round_no: Mapped[int] = mapped_column(default=1, comment="复核轮次")
    reviewer_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), comment="复核人")
    opinion: Mapped[str | None] = mapped_column(comment="复核意见（退回必附，FR-05-02）")
    revision_requirement: Mapped[str | None] = mapped_column(comment="修订要求")
    signature: Mapped[dict[str, Any] | None] = mapped_column(
        JSONField, comment="电子签名（复核人+日期+证书号）"
    )
    conclusion: Mapped[str | None] = mapped_column(String(10), comment="结论（通过/退回）")
    reviewed_date: Mapped[date | None] = mapped_column(comment="复核日期")
