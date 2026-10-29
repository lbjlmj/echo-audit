"""三层级材料、字段映射与凭证解析产物（FR-03-01/03-04、§15-6）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONField, PKMixin, TenantMixin, TimestampMixin

# 三层级（§15-6）：客户层 / 事务所层 / 填表说明层
MATERIAL_LEVELS = ("customer", "firm", "rule")


class Material(PKMixin, TenantMixin, TimestampMixin, Base):
    """三层级材料登记。

    资料不齐=存在缺口标志的必需材料未到位，底稿引擎拒绝出稿（策略模式拦截点，"不出半成品"）。
    """

    __tablename__ = "materials"
    __table_args__ = (
        CheckConstraint(
            "level IN ('customer','firm','rule')", name="ck_material_level"
        ),
        CheckConstraint(
            "parse_status IN ('待解析','解析中','已解析','失败')", name="ck_material_parse_status"
        ),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    level: Mapped[str] = mapped_column(String(10), comment="层级（customer/firm/rule）")
    file_key: Mapped[str] = mapped_column(String(200), comment="对象键（MinIO，不入库明文路径）")
    origin_name: Mapped[str | None] = mapped_column(
        String(200), comment="客户原始文件名（R6 重命名归档前保留，审计追溯 + 清单匹配）"
    )
    catalog: Mapped[str | None] = mapped_column(
        String(10), comment="决策档案目录类别简称（R6 枚举，AI 分类采信后回写）"
    )
    parse_status: Mapped[str] = mapped_column(String(10), default="待解析", comment="解析状态")
    vector_ref: Mapped[str | None] = mapped_column(String(200), comment="向量库引用（pgvector）")
    gap_flag: Mapped[bool] = mapped_column(Boolean, default=False, comment="缺口标志（必需材料未到位）")


class ImportFieldMapping(PKMixin, TenantMixin, TimestampMixin, Base):
    """非标数据字段映射（§15-6）：人工映射一次、同客户复用；解析前先跑最低字段校验。

    科目余额表最低字段：科目编码/名称/期初/借方/贷方/期末且级次≥3；
    明细账最低字段：日期、凭证号、摘要、科目编码、科目名称、借方、贷方（对方科目可选）。
    """

    __tablename__ = "import_field_mappings"
    __table_args__ = (
        CheckConstraint(
            "mapping_type IN ('科目余额表','明细账')", name="ck_mapping_type"
        ),
    )

    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id"), index=True)
    source_system: Mapped[str] = mapped_column(String(100), comment="客户/来源系统")
    mapping_type: Mapped[str] = mapped_column(
        String(10), comment="映射类型（科目余额表/明细账，§15-6 最低字段按类型校验）"
    )
    field_mapping: Mapped[dict[str, str]] = mapped_column(
        JSONField, comment="源字段名→标准字段映射（JSONB）"
    )
    mapped_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), comment="映射人")
    reuse_scope: Mapped[str] = mapped_column(
        String(10), default="本客户", comment="复用范围（本客户/全所）"
    )


class VoucherLine(PKMixin, TenantMixin, TimestampMixin, Base):
    """凭证解析行（明细账最低字段，FR-03-04 全量异常评分的数据池）。

    明细账最低字段：日期、凭证号、摘要、科目编码、科目名称、借方、贷方（对方科目可选，§15-6）。
    """

    __tablename__ = "voucher_lines"

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    voucher_no: Mapped[str] = mapped_column(String(50), comment="凭证号")
    entry_date: Mapped[date | None] = mapped_column(comment="记账日期")
    summary: Mapped[str | None] = mapped_column(String(200), comment="摘要")
    account_code: Mapped[str] = mapped_column(String(30), index=True, comment="科目编码")
    account_name: Mapped[str | None] = mapped_column(String(100), comment="科目名称")
    debit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="借方金额")
    credit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), comment="贷方金额")


class VoucherSample(PKMixin, TenantMixin, TimestampMixin, Base):
    """抽凭清单行（FR-03-04）：AI 评分采信后落库，项目经理可调整（下限 5%），审计员回填执行。"""

    __tablename__ = "voucher_samples"
    __table_args__ = (
        CheckConstraint(
            "source IN ('评分Top10%','科目Top5','保底20笔','人工调整')", name="ck_sample_source"
        ),
        CheckConstraint("exec_status IN ('未执行','已执行')", name="ck_sample_exec_status"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    voucher_line_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("voucher_lines.id"), unique=True
    )
    score: Mapped[float | None] = mapped_column(comment="异常评分（人工调整行为空）")
    source: Mapped[str] = mapped_column(String(20), comment="入清单来源")
    exec_status: Mapped[str] = mapped_column(String(10), default="未执行", comment="执行状态")
    exec_note: Mapped[str | None] = mapped_column(String(500), comment="回填执行说明")
    executed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
