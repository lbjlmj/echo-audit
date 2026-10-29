"""资料域出入参 DTO（FR-03-01 材料登记/解析 / §15-6 字段映射 / 凭证行导入）。"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MaterialIn(BaseModel):
    project_id: str = Field(min_length=1)
    level: str = Field(pattern="^(customer|firm|rule)$", description="层级（customer/firm/rule）")
    file_key: str = Field(min_length=1, max_length=200, description="MinIO 对象键")
    required: bool = Field(default=False, description="必需材料（未到位挂缺口标志）")


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    level: str
    file_key: str
    origin_name: str | None = None
    catalog: str | None = None
    parse_status: str
    vector_ref: str | None = None
    gap_flag: bool
    created_at: datetime | None = None


class ParseIn(BaseModel):
    ok: bool
    vector_ref: str | None = Field(default=None, max_length=200, description="向量库引用（RAG）")


class MappingIn(BaseModel):
    client_id: str = Field(min_length=1)
    source_system: str = Field(min_length=1, max_length=100)
    mapping_type: str = Field(pattern="^(科目余额表|明细账)$")
    field_mapping: dict[str, str] = Field(min_length=1, description="源字段名→标准字段映射")
    reuse_scope: str = Field(default="本客户", pattern="^(本客户|全所)$")


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str
    source_system: str
    mapping_type: str
    field_mapping: dict[str, str]
    reuse_scope: str
    created_at: datetime | None = None


class VoucherLineIn(BaseModel):
    voucher_no: str = Field(min_length=1, max_length=50)
    entry_date: date | None = None
    summary: str | None = Field(default=None, max_length=200)
    account_code: str = Field(min_length=1, max_length=30)
    account_name: str | None = Field(default=None, max_length=100)
    debit: Decimal | None = None
    credit: Decimal | None = None


class VoucherLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    voucher_no: str
    entry_date: date | None = None
    summary: str | None = None
    account_code: str
    account_name: str | None = None
    debit: Decimal | None = None
    credit: Decimal | None = None


class GapsOut(BaseModel):
    """缺口清单（FR-03-01：资料不齐 → 明确列出缺口待补，不出半成品）。"""

    gaps: list[dict[str, Any]]
    missing_levels: list[str]
    complete: bool


# ---------------------------------------------------------------------------
# FR-01-05 资料清单前置（scope 快照 × 客户层材料实时匹配；按清单一次性补齐）
# ---------------------------------------------------------------------------
class ChecklistItemOut(BaseModel):
    item: str
    arrived: bool
    matched_file_keys: list[str]


class ChecklistOut(BaseModel):
    """客户一次性资料清单（缺口实时可见，清单项与到料状态一一对应）。"""

    project_id: str
    industry_template: str | None = None
    in_matrix: bool = False
    items: list[ChecklistItemOut]
    total: int
    arrived_count: int
    missing: list[str]
    complete: bool
    note: str | None = None


class ChecklistEntryIn(BaseModel):
    item: str | None = Field(default=None, max_length=100, description="对应清单项名（选填）")
    file_key: str = Field(min_length=1, max_length=200, description="R6 命名对象键（须携带清单项名）")


class BatchArriveIn(BaseModel):
    project_id: str = Field(min_length=1)
    entries: list[ChecklistEntryIn] = Field(min_length=1, description="按清单一次性补齐项")


class BatchArriveOut(BaseModel):
    registered: list[dict[str, Any]]
    checklist: ChecklistOut


# ---------------------------------------------------------------------------
# FR-01-04 资料自动分类（线索 → 审计员确认采信 → R6 重命名归档）
# ---------------------------------------------------------------------------
class ClassifyEntry(BaseModel):
    material_id: str = Field(min_length=1)
    catalog: str = Field(min_length=2, max_length=2, description="决策档案目录类别简称（R6 枚举）")
    reason: str = Field(default="", max_length=200)


class ClassifyOut(BaseModel):
    """分类线索（三段式前两段产物，未经确认不得归档；确认时原样回传防篡改）。"""

    run_no: str
    entries: list[ClassifyEntry]
    detail: dict[str, Any]


class ConfirmClassifyIn(BaseModel):
    run_no: str = Field(min_length=1)
    entries: list[ClassifyEntry] = Field(min_length=1)
    conclusion: Literal["采信", "不采信"]
    rejection_reason: str | None = Field(default=None, max_length=500)
