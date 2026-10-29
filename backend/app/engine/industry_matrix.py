"""行业模板矩阵（FR-02-04）——数据即代码，随版本发布（AGENTS 仓库结构约定）。

八类中小所高频行业（PRD FR-02-04 清单）；每类给出：
- materials：客户资料清单范围（FR-01-05 资料清单前置的数据源）；
- worksheet_templates：底稿模板范围（key + 标题，创建底稿时圈定可选集）。
专项/经责不在高频矩阵内（§3.2 十类业务中仅八类入矩阵）——scope 为空列表 +
注记，模板范围待计划阶段人工圈定，不阻断立项。
"""
from __future__ import annotations

from typing import Any

# 八类高频行业 → 资料清单与底稿模板范围
INDUSTRY_MATRIX: dict[str, dict[str, Any]] = {
    "一般企业年报": {
        "materials": [
            "业务约定书", "营业执照与章程", "年度财务报表（含附注）",
            "总账与明细账", "银行对账单及余额调节表", "纳税申报表",
        ],
        "worksheet_templates": [
            {"key": "ws-general-plan", "title": "总体审计策略与具体计划"},
            {"key": "ws-materiality", "title": "重要性水平测算"},
            {"key": "ws-cash", "title": "货币资金"},
            {"key": "ws-ar", "title": "应收账款与坏账"},
            {"key": "ws-inventory", "title": "存货监盘"},
            {"key": "ws-revenue-cutoff", "title": "收入截止测试"},
        ],
    },
    "小企业准则": {
        "materials": [
            "业务约定书", "营业执照", "财务报表", "银行对账单", "纳税申报表",
        ],
        "worksheet_templates": [
            {"key": "ws-general-plan", "title": "总体审计策略与具体计划"},
            {"key": "ws-cash", "title": "货币资金"},
            {"key": "ws-related", "title": "往来款项"},
            {"key": "ws-income", "title": "收入与费用"},
        ],
    },
    "事业单位": {
        "materials": [
            "业务约定书", "事业单位法人证书", "部门决算报表",
            "财政拨款文件", "固定资产台账",
        ],
        "worksheet_templates": [
            {"key": "ws-general-plan", "title": "总体审计策略与具体计划"},
            {"key": "ws-fiscal", "title": "财政拨款收支"},
            {"key": "ws-fixed-assets", "title": "固定资产"},
            {"key": "ws-special-fund", "title": "专项资金"},
        ],
    },
    "房地产": {
        "materials": [
            "业务约定书", "开发资质与预售许可证", "项目成本决算报告",
            "按揭与销售台账", "财务报表（含附注）",
        ],
        "worksheet_templates": [
            {"key": "ws-general-plan", "title": "总体审计策略与具体计划"},
            {"key": "ws-dev-cost", "title": "开发成本"},
            {"key": "ws-advance", "title": "预收款项"},
            {"key": "ws-revenue-confirm", "title": "收入确认"},
        ],
    },
    "建安": {
        "materials": [
            "业务约定书", "施工合同台账", "工程进度确认单",
            "成本归集表", "财务报表",
        ],
        "worksheet_templates": [
            {"key": "ws-general-plan", "title": "总体审计策略与具体计划"},
            {"key": "ws-contract-progress", "title": "合同履约进度"},
            {"key": "ws-settlement", "title": "工程结算"},
            {"key": "ws-construction-cost", "title": "工程施工成本"},
        ],
    },
    "商贸": {
        "materials": [
            "业务约定书", "进销存台账", "主要购销合同", "库存盘点表", "财务报表",
        ],
        "worksheet_templates": [
            {"key": "ws-general-plan", "title": "总体审计策略与具体计划"},
            {"key": "ws-inventory-flow", "title": "存货进销存"},
            {"key": "ws-related", "title": "往来款项"},
            {"key": "ws-margin", "title": "收入毛利分析"},
        ],
    },
    "科技型中小企业": {
        "materials": [
            "业务约定书", "高新技术企业资质证书", "研发费用辅助账",
            "政府补助文件", "财务报表",
        ],
        "worksheet_templates": [
            {"key": "ws-general-plan", "title": "总体审计策略与具体计划"},
            {"key": "ws-rnd-cost", "title": "研发费用归集"},
            {"key": "ws-grant", "title": "政府补助"},
            {"key": "ws-revenue-confirm", "title": "收入确认"},
        ],
    },
    "非上市": {
        "materials": [
            "业务约定书", "公司章程与股东会决议", "财务报表（含附注）",
            "关联方清单",
        ],
        "worksheet_templates": [
            {"key": "ws-general-plan", "title": "总体审计策略与具体计划"},
            {"key": "ws-related-party", "title": "关联方及关联交易"},
            {"key": "ws-equity", "title": "股东权益"},
            {"key": "ws-notes", "title": "报表附注"},
        ],
    },
}

MATRIX_TYPES = tuple(INDUSTRY_MATRIX)  # 高频八类


def scope_of(business_type: str) -> dict[str, Any]:
    """行业模板范围快照（FR-02-04 验收：模板选择决定资料清单与底稿模板范围）。

    非高频矩阵行业（专项/经责）返回空范围 + 注记，不阻断立项与计划编制。
    """
    in_matrix = business_type in INDUSTRY_MATRIX
    if not in_matrix:
        return {
            "industry_template": business_type,
            "in_matrix": False,
            "materials": [],
            "worksheet_templates": [],
            "note": "非高频矩阵行业，模板范围待计划阶段人工圈定",
        }
    matrix = INDUSTRY_MATRIX[business_type]
    return {
        "industry_template": business_type,
        "in_matrix": True,
        "materials": list(matrix["materials"]),
        "worksheet_templates": [dict(t) for t in matrix["worksheet_templates"]],
        "note": "行业模板矩阵随版本发布（数据即代码）",
    }
