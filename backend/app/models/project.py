"""客户、审计项目与阶段门（TECH_DESIGN §3.2；PRD §4.2 八阶段门、§15-17 暂停/终止）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONField, PKMixin, TenantMixin, TimestampMixin, sql_in

# 八阶段（PRD §4.2；engine/stage_gate.py 同口径）
PROJECT_STAGES = ("承接", "计划", "风险评估", "内控", "实质性", "完成", "报告", "归档")
# 项目四状态（PRD §4 + §15-17；流转只走 engine，硬约束 2）
PROJECT_STATUSES = ("进行中", "暂停", "已归档", "终止")
# 业务类型（§3.2 命名引擎种子关联）
BUSINESS_TYPES = (
    "一般企业年报", "小企业准则", "事业单位", "房地产", "建安",
    "商贸", "科技型中小企业", "非上市", "专项", "经责",
)


class Client(PKMixin, TenantMixin, TimestampMixin, Base):
    """被审计单位；数据等级（核心/重要/一般）驱动脱敏与上云管控（§7 R4、NFS-01）。"""

    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint("data_level IN ('核心','重要','一般')", name="ck_client_data_level"),
        UniqueConstraint("tenant_id", "uscc", name="uq_client_tenant_uscc"),
    )

    name: Mapped[str] = mapped_column(String(200), comment="客户名称")
    uscc: Mapped[str] = mapped_column(String(18), comment="统一社会信用代码")
    industry: Mapped[str] = mapped_column(String(50), comment="行业（模板矩阵选择依据）")
    data_level: Mapped[str] = mapped_column(String(10), comment="数据等级（财政部三级）")


class AuditProject(PKMixin, TenantMixin, TimestampMixin, Base):
    """审计项目。

    暂停=合伙人批准后阶段门冻结、项目内禁止写操作（engine 拒绝非只读事务，§15-17）；
    终止=触发质控封存「终止归档包」，不纳入常规归档考核。
    """

    __tablename__ = "audit_projects"
    __table_args__ = (
        CheckConstraint(f"current_stage IN {sql_in(PROJECT_STAGES)}", name="ck_project_stage"),
        CheckConstraint(f"status IN {sql_in(PROJECT_STATUSES)}", name="ck_project_status"),
        CheckConstraint(f"business_type IN {sql_in(BUSINESS_TYPES)}", name="ck_project_biz_type"),
    )

    project_no: Mapped[str] = mapped_column(String(32), unique=True, comment="项目编号（命名引擎 FR-01-02）")
    name: Mapped[str] = mapped_column(String(200), comment="项目名称")
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id"), index=True)
    business_type: Mapped[str] = mapped_column(String(20), comment="业务类型（§3.2 十类）")
    template_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("templates.id"))
    current_stage: Mapped[str] = mapped_column(String(10), default="承接", comment="当前八阶段")
    status: Mapped[str] = mapped_column(String(10), default="进行中", comment="项目状态（四状态）")
    period: Mapped[str] = mapped_column(String(20), comment="审计期间（如 2025 年度）")
    fee_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), comment="合同收费（元，FR-01-06 校验入参）"
    )
    total_assets: Mapped[Decimal | None] = mapped_column(
        Numeric(16, 2), comment="客户资产总额（元，收费档位依据，FR-01-06）"
    )
    approval_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSONField, comment="立项评价结论（JSONB）"
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="暂停时间（超 12 个月提示转归档/终止，§15-17）"
    )
    archive_package_hash: Mapped[str | None] = mapped_column(
        String(64), comment="归档包哈希（FR-06-04：底稿+复核+AI 留痕清单快照 sha256，出证依据）"
    )


class StageGate(PKMixin, TenantMixin, TimestampMixin, Base):
    """八阶段门（FR-06-03）：跳过必须同时登记合伙人与质控双批（§7 R3）。"""

    __tablename__ = "stage_gates"
    __table_args__ = (
        CheckConstraint(f"stage IN {sql_in(PROJECT_STAGES)}", name="ck_gate_stage"),
        CheckConstraint("gate_status IN ('未开始','进行中','已通过','跳过')", name="ck_gate_status"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    stage: Mapped[str] = mapped_column(String(10), comment="阶段（八阶段枚举）")
    gate_status: Mapped[str] = mapped_column(String(10), default="未开始", comment="门状态")
    conditions_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONField, comment="通过条件快照（按 §4.2 八门准入条件生成）"
    )
    passed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    passed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skip_partner_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), comment="跳过双批：合伙人（R3）"
    )
    skip_qc_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id"), comment="跳过双批：质控专员（R3）"
    )
