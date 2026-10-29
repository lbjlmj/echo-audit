"""计划域出入参 DTO（FR-02：重要性水平 R8、风险评级确认、R5 程序包）。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BasisType = Literal["营业收入", "费用总额", "总资产"]
RiskLevel = Literal["高", "中", "低"]


class MaterialityIn(BaseModel):
    benchmark_type: BasisType
    basis_amount: Decimal = Field(gt=0, description="基准金额（元）")
    exec_ratio_pct: Decimal = Field(default=Decimal("60"), ge=50, le=75, description="实际执行比例 %（50–75）")
    trivial_ratio_pct: Decimal = Field(default=Decimal("5"), gt=0, lt=10, description="明显微小比例 %（0–10）")


class MaterialityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    benchmark_type: str
    benchmark_amount: Decimal
    ratio: Decimal
    overall_materiality: Decimal
    execution_ratio: Decimal
    trivial_ratio: Decimal
    change_log: list | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class RiskIn(BaseModel):
    source: Literal["ai", "人工"]
    description: str = Field(min_length=2, max_length=500, description="财报层风险描述")
    risk_level: RiskLevel
    assertion_mapping: str | None = Field(default=None, max_length=200, description="认定层次映射")


class RiskConfirmIn(BaseModel):
    risk_level: RiskLevel | None = Field(default=None, description="修改评级（不改则沿用）")


class RiskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    source: str
    description: str
    risk_level: str
    status: str
    assertion_mapping: str | None = None
    anchor_id: str | None = None
    recommended_programs: list | None = None


class RiskAiOut(BaseModel):
    """风险评估 AI 输出（FR-02-02）：留痕编号 + 风险清单草稿 + 无锚点跳过项（硬约束 6）。"""

    run_no: str
    status: str
    line_count: int
    risks: list[RiskOut]
    skipped_no_anchor: list[dict]


class ProgramIn(BaseModel):
    program_code: str = Field(min_length=1, max_length=32)
    min_standard_note: str | None = Field(default=None, max_length=200, description="简化包必带：准则最低底线标注（R5）")


class ProgramOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    risk_id: str
    program_code: str
    package_type: str
    min_standard_note: str | None = None
    exec_status: str
