"""八阶段门（FR-06-03，PRD §4.2）与八门准入骨架。

阶段：承接→计划→风险评估→内控→实质性→完成→报告→归档
- 门状态：未开始/进行中/已通过/跳过；跳过必须同时登记合伙人与质控双批（§7 R3）。
- 函证全部「闭环」是实质性阶段门准入条件之一（§4.2 门 5）。
- 项目暂停时阶段门冻结，engine 拒绝非只读事务（PRD §15-17）。
"""
from __future__ import annotations

STAGES: tuple[str, ...] = ("承接", "计划", "风险评估", "内控", "实质性", "完成", "报告", "归档")

GATE_STATUSES: tuple[str, ...] = ("未开始", "进行中", "已通过", "跳过")

# 各门准入条件快照的生成键（快照内容随模板/项目数据在服务层填充，此处定义结构）
GATE_CONDITION_KEYS: dict[str, tuple[str, ...]] = {
    "承接": ("project_approved", "engagement_letter", "independence_check"),
    "计划": ("plan_approved", "materiality_confirmed"),  # 重要性水平随计划报合伙人审批（R8）
    "风险评估": ("risk_list_confirmed",),
    "内控": ("control_tests_done",),
    "实质性": ("confirmations_closed", "programs_executed"),  # confirmations_closed：函证全闭环
    # qc_check_passed：质控 AI 完整性检查已采信且无未通过项（FR-06-01，§4.2 门 6）
    "完成": ("findings_closed_or_escalated", "worksheet_review_passed", "qc_check_passed"),
    "报告": ("report_signed", "escrow_opinion_resolved"),
    "归档": ("archive_package_sealed",),
}

# 各门卡控权限（FR-06-03，§4.2 卡控责任列）：门流转统一走 engine 裁决 + 此处权限单点。
# 服务层以 assert_allowed(权限码, 等级) 卡控；R3 双批跳过另须质控专员会批（services/quality）。
GATE_APPROVERS: dict[str, tuple[str, int]] = {
    "承接": ("project.approve", 4),    # 合伙人审批（intake.approve_intake 串联）
    "计划": ("project.approve", 4),    # 合伙人审批
    "风险评估": ("plan.confirm", 3),   # 项目经理确认
    "内控": ("plan.confirm", 3),       # 项目经理复核
    "实质性": ("plan.confirm", 3),     # 项目经理复核 + 质控抽查
    "完成": ("archive.access", 3),     # 质控专员（经理 OPERATE 可代，审计员 VIEW 拒绝）
    "报告": ("report.sign", 4),        # 合伙人签发 + 质控会签
    "归档": ("archive.access", 3),     # 质控专员（FR-06-04 归档动作串联）
}


def validate_stage(stage: str) -> str:
    if stage not in STAGES:
        raise ValueError(f"非法阶段：{stage}，合法值：{STAGES}")
    return stage


def pass_gate(stage: str, conditions: dict[str, bool], *, skip: bool = False) -> str:
    """门流转裁决：条件齐备才可「已通过」；skip 必须带双批记录。"""
    validate_stage(stage)
    required = GATE_CONDITION_KEYS[stage]
    if skip:
        return "跳过"  # 调用方必须已登记 partner_approval + qc_approval（R3）
    missing = [k for k in required if not conditions.get(k)]
    if missing:
        raise ValueError(f"阶段门「{stage}」准入条件未满足：{missing}")
    return "已通过"
