"""风险清单、审计程序与重要性水平（FR-02；§7 R5 风险分级、R8 重要性水平）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONField, PKMixin, TenantMixin, TimestampMixin


class RiskItem(PKMixin, TenantMixin, TimestampMixin, Base):
    """风险清单：AI 风险评估产出经项目经理逐项确认（AI 只产出线索，硬约束 3）。"""

    __tablename__ = "risk_items"
    __table_args__ = (
        CheckConstraint("source IN ('ai','人工')", name="ck_risk_source"),
        CheckConstraint("risk_level IN ('高','中','低')", name="ck_risk_level"),
        CheckConstraint(
            "status IN ('待确认','已确认','已应对')", name="ck_risk_status"
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    source: Mapped[str] = mapped_column(String(10), comment="来源（ai 风险评估/人工）")
    description: Mapped[str] = mapped_column(String(500), comment="财报层风险描述")
    assertion_mapping: Mapped[str | None] = mapped_column(String(200), comment="认定层次映射")
    anchor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("rule_anchors.id"), comment="准则锚点（AI 项必带，硬约束 6：无引用不出项）"
    )
    recommended_programs: Mapped[list[Any] | None] = mapped_column(
        JSONField, comment="AI 推荐程序组合（确认后按 R5 生成程序包）"
    )
    risk_level: Mapped[str] = mapped_column(String(5), comment="风险等级（R5 驱动程序包）")
    status: Mapped[str] = mapped_column(String(10), default="待确认", comment="确认状态")


class AuditProgram(PKMixin, TenantMixin, TimestampMixin, Base):
    """审计程序：高风险全程序包 / 低风险简化程序包（简化包必带准则最低底线标注，R5）。"""

    __tablename__ = "audit_programs"
    __table_args__ = (
        CheckConstraint(
            "package_type IN ('高风险全程序','低风险简化程序包')", name="ck_program_pkg"
        ),
        CheckConstraint(
            "exec_status IN ('未执行','执行中','已完成')", name="ck_program_exec"
        ),
    )

    risk_id: Mapped[str] = mapped_column(String(36), ForeignKey("risk_items.id"), index=True)
    program_code: Mapped[str] = mapped_column(String(32), comment="程序代码")
    package_type: Mapped[str] = mapped_column(String(20), comment="程序包类型（R5）")
    min_standard_note: Mapped[str | None] = mapped_column(
        String(200), comment="简化包必带：准则最低底线标注（R5）"
    )
    worksheet_requirement: Mapped[str | None] = mapped_column(
        String(100), comment="关联底稿需求（底稿编号）"
    )
    exec_status: Mapped[str] = mapped_column(String(10), default="未执行", comment="执行状态")


class Materiality(PKMixin, TenantMixin, TimestampMixin, Base):
    """重要性水平（R8，单点计算在 engine/materiality.py）。

    默认按主体类型预置：盈利主体=营业收入×0.5%、成本费用化=费用总额×1%、
    资产基础型=总资产×0.5%；实际执行比例默认 60%（区间 50%–75%），明显微小比例默认 5%；
    项目经理可改，随审计计划一并报合伙人审批确认，修改全量留痕。
    """

    __tablename__ = "materialities"
    __table_args__ = (
        CheckConstraint(
            "benchmark_type IN ('营业收入','费用总额','总资产')", name="ck_mat_benchmark"
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), unique=True, index=True
    )
    benchmark_type: Mapped[str] = mapped_column(String(10), comment="基准类型")
    benchmark_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), comment="基准金额")
    ratio: Mapped[Decimal] = mapped_column(Numeric(8, 4), comment="计算比例")
    overall_materiality: Mapped[Decimal] = mapped_column(Numeric(18, 2), comment="整体重要性")
    execution_ratio: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("60"), comment="实际执行比例（默认 60%，区间 50–75）"
    )
    trivial_ratio: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("5"), comment="明显微小比例（默认 5%）"
    )
    change_log: Mapped[list[Any] | None] = mapped_column(JSONField, comment="修改留痕（JSONB）")
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), comment="批准合伙人（R8：随计划一并报批）"
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="批准时间")
