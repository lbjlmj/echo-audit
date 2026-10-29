"""差错归档单（PRD §15-16 / TECH_DESIGN §4.3.4：归档后差错 v(n+1) 双版本）。

处理链：质控专员登记 → 合伙人批准（解锁受影响底稿副本修订，原稿锁定保留=版本链追加，
已归档版本与旧归档包不动）→ 质控复核 → 重新归档生成 v(n+1) 包（新旧整包哈希均留痕可查）。
状态流转只走 engine.state_machines.CORRECTION（硬约束 2）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONField, PKMixin, TenantMixin, TimestampMixin, sql_in

# 差错单六状态（engine CORRECTION 同口径，禁止自创状态名）
CORRECTION_STATUSES = ("待批准", "已驳回", "修订中", "待质控复核", "复核通过", "已重新归档")
# 在途状态：同一项目同一时刻仅允许一张在途差错单（防交织修订同一底稿）
CORRECTION_OPEN_STATUSES = ("待批准", "修订中", "待质控复核", "复核通过")


class ArchiveCorrection(PKMixin, TenantMixin, TimestampMixin, Base):
    """归档后差错单（§15-16）：登记→批准→副本修订→质控复核→重新归档 v(n+1) 全程留痕。"""

    __tablename__ = "archive_corrections"
    __table_args__ = (
        CheckConstraint(f"status IN {sql_in(CORRECTION_STATUSES)}", name="ck_correction_status"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    code: Mapped[str] = mapped_column(String(40), comment="差错单号（{项目编号}-CORnnn，系统生成）")
    description: Mapped[str] = mapped_column(String(1000), comment="差错描述")
    affected_worksheet_ids: Mapped[list[Any]] = mapped_column(
        JSONField, comment="受影响底稿 id 清单（解锁副本修订范围）"
    )
    status: Mapped[str] = mapped_column(String(10), default="待批准", comment="六状态")
    registered_by: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), comment="登记人（质控专员）"
    )
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), comment="批准人（合伙人）"
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(comment="驳回原因（驳回必附）")
    revision_baseline: Mapped[dict[str, Any] | None] = mapped_column(
        JSONField, comment="批准时点受影响底稿版本计数基线 {worksheet_id: n}（提交复核须逐稿超限）"
    )
    qc_reviewed_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), comment="质控复核人"
    )
    qc_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qc_opinion: Mapped[str | None] = mapped_column(comment="质控复核意见（退回必附）")
    previous_package_hash: Mapped[str | None] = mapped_column(
        String(64), comment="v(n) 归档包哈希（重新归档前登记值，双版本留痕）"
    )
    rearchive_package_hash: Mapped[str | None] = mapped_column(
        String(64), comment="v(n+1) 归档包哈希（重新归档产出）"
    )
    rearchived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
