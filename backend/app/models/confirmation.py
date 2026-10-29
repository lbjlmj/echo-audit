"""函证台账（FR-04；PRD §4.4 八状态机、§15-7 超期 30 天/提醒 15 天×2）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONField, PKMixin, TenantMixin, TimestampMixin, sql_in

# 函证八状态（流转只走 engine/state_machines.CONFIRMATION，硬约束 2）
CONFIRMATION_STATUSES = (
    "台账已生成", "已发出", "已回函", "超期未回", "核对一致", "差异处理", "替代程序", "闭环",
)


class Confirmation(PKMixin, TenantMixin, TimestampMixin, Base):
    """函证。

    超期机制（Beat 驱动）：发出后 30 天未回函自动转「超期未回」，此后每 15 天提醒一次，
    累计两次提醒后系统建议启动替代程序（§15-7）；全部函证「闭环」为实质性阶段门准入条件之一（门 5）。
    快递单号由顺丰适配器回写（integrations/sf_express.py，仅物流字段出站，函证金额不出站）。
    """

    __tablename__ = "confirmations"
    __table_args__ = (
        CheckConstraint(f"status IN {sql_in(CONFIRMATION_STATUSES)}", name="ck_cf_status"),
        CheckConstraint("type IN ('银行','往来')", name="ck_cf_type"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    type: Mapped[str] = mapped_column(String(10), comment="类型（银行/往来）")
    counterparty: Mapped[str] = mapped_column(String(200), comment="对方单位")
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), comment="函证金额（不出站）")
    status: Mapped[str] = mapped_column(String(10), default="台账已生成", comment="八状态")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="发出时间")
    planned_reply_date: Mapped[date | None] = mapped_column(Date, comment="计划回函日期")
    waybill_no: Mapped[str | None] = mapped_column(String(50), comment="快递单号（适配器回写）")
    logistics_status: Mapped[str | None] = mapped_column(String(50), comment="物流状态（适配器回写）")
    difference: Mapped[dict[str, Any] | None] = mapped_column(JSONField, comment="回函差异（JSONB）")
    alt_test_record: Mapped[str | None] = mapped_column(comment="替代测试记录")
    remind_count: Mapped[int] = mapped_column(Integer, default=0, comment="超期提醒计数（上限 2 次）")
