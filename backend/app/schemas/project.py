"""项目域出入参 DTO（FR-01 立项行为链）。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# 业务类型（§3.2 十类，与 models/project.BUSINESS_TYPES 同口径）
BusinessType = Literal[
    "一般企业年报", "小企业准则", "事业单位", "房地产", "建安",
    "商贸", "科技型中小企业", "非上市", "专项", "经责",
]


class IntakeEvaluation(BaseModel):
    """业务承接评价（FR-01-01）：任一项未通过即拦截立项。"""

    independence_ok: bool = Field(description="独立性检查")
    client_risk_ok: bool = Field(description="客户风险初评")
    competence_ok: bool = Field(description="胜任能力自评")
    engagement_letter_ok: bool = Field(default=False, description="业务约定书已核（承接门准入）")
    notes: str | None = Field(default=None, max_length=500, description="评价备注")


class ClientOut(BaseModel):
    """客户下拉数据源（立项/项目面板用）。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    industry: str | None = None
    data_level: str | None = None


class IntakeSubmitRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200, description="项目名称")
    client_id: str = Field(description="客户 id")
    business_type: BusinessType
    period: str = Field(min_length=4, max_length=20, description="审计期间（如 2025 年度）")
    evaluation: IntakeEvaluation
    members: list[MemberIn] = Field(default_factory=list, description="拟任团队（审批通过后自动初始化）")
    contract: ContractIn | None = Field(default=None, description="合同信息（录入即收费校验，FR-01-06）")


class ContractIn(BaseModel):
    """合同信息（FR-01-06：报价对照《业务收费管理办法》校验，不通过阻断立项）。"""

    total_assets: float = Field(ge=0, description="客户资产总额（元，档位依据）")
    fee_amount: float = Field(ge=0, description="合同收费（元）")


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_no: str
    name: str
    client_id: str
    business_type: str
    current_stage: str
    status: str
    period: str
    approval_summary: dict[str, Any] | None = None


class MemberIn(BaseModel):
    user_id: str = Field(description="用户 id")
    project_role: Literal["编制", "复核", "查看"]


# ---------------------------------------------------------------------------
# FR-02-05 团队与工时（项目经理配置；预算/实际对比 + 偏差超 10% 告警）
# ---------------------------------------------------------------------------
class MemberHoursIn(BaseModel):
    user_id: str = Field(description="用户 id")
    project_role: Literal["编制", "复核", "查看"] | None = None
    budget_hours: float = Field(default=0, ge=0)
    actual_hours: float = Field(default=0, ge=0)


class TeamHoursIn(BaseModel):
    members: list[MemberHoursIn] = Field(min_length=1, max_length=50)


class TeamMemberOut(BaseModel):
    member_id: str
    user: str
    project_role: str
    budget_hours: float
    actual_hours: float
    deviation_pct: float


class TeamOverviewOut(BaseModel):
    project_id: str
    members: list[TeamMemberOut]
    budget_total: float
    actual_total: float
    deviation_pct: float
    over_threshold: bool = Field(description="偏差超 10% 面板告警（FR-02-05）")


# ---------------------------------------------------------------------------
# FR-02-04 行业模板选择（立项随建档圈定 + 计划阶段圈定/变更）
# ---------------------------------------------------------------------------
class WorksheetTemplateBrief(BaseModel):
    key: str
    title: str


class IndustryTemplateScope(BaseModel):
    """行业模板范围快照：模板选择决定资料清单与底稿模板范围（FR-02-04）。"""

    industry_template: str
    in_matrix: bool
    materials: list[str]
    worksheet_templates: list[WorksheetTemplateBrief]
    note: str


class SelectTemplateIn(BaseModel):
    industry_template: str = Field(min_length=2, max_length=20, description="高频矩阵八类之一")
