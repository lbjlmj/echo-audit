"""业务对象状态机（严格对齐 PRD §4.3/§4.4/§4.5，禁止自创状态名）。

流转由 engine 统一裁决；任何 service 不得直接改状态字段。
非法流转抛 IllegalTransition，并要求附带业务上下文（如退回必附意见）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class IllegalTransition(Exception):
    """非法状态流转。"""


@dataclass(frozen=True)
class Transition:
    target: str
    # 触发该流转必须满足的业务前置（由调用方传入 kwargs 校验）
    requires: tuple[str, ...] = ()


class StateMachine:
    name: str
    transitions: Mapping[str, tuple[Transition, ...]]

    def __init__(self, name: str, transitions: Mapping[str, tuple[Transition, ...]]) -> None:
        self.name = name
        self.transitions = transitions

    def can(self, current: str, target: str) -> bool:
        return any(t.target == target for t in self.transitions.get(current, ()))

    def transit(self, current: str, target: str, **ctx: object) -> str:
        """校验并执行流转；ctx 必须覆盖该流转 requires 的全部前置。"""
        allowed = {t.target: t for t in self.transitions.get(current, ())}
        if target not in allowed:
            raise IllegalTransition(
                f"[{self.name}] 不允许 {current} -> {target}（合法目标："
                f"{[t.target for t in self.transitions.get(current, ())]}）"
            )
        missing = [k for k in allowed[target].requires if not ctx.get(k)]
        if missing:
            raise IllegalTransition(f"[{self.name}] {current} -> {target} 缺少前置：{missing}")
        return target


# ---------------------------------------------------------------------------
# 底稿六状态（PRD §4.3）
# 草稿→待复核（提交，AI 内容须已完成复核标记 §7 R2）→复核中→复核通过（退回必附意见回草稿）
# →终审通过（仅重要底稿，PRD §15-11）→已归档（随项目归档）
# ---------------------------------------------------------------------------
WORKSHEET = StateMachine("底稿", {
    "草稿": (
        Transition("待复核", requires=("submitted_by", "ai_review_marked")),
    ),
    "待复核": (
        Transition("复核中", requires=("reviewer",)),
    ),
    "复核中": (
        Transition("复核通过", requires=("reviewer",)),
        Transition("草稿", requires=("reviewer", "opinion")),  # 退回必附意见（FR-05-02）
    ),
    "复核通过": (
        Transition("终审通过", requires=("approver", "is_important")),  # 仅重要底稿
        Transition("已归档", requires=("archived_with_project",)),
    ),
    "终审通过": (
        Transition("已归档", requires=("archived_with_project",)),
    ),
    "已归档": (),
})

# ---------------------------------------------------------------------------
# 函证八状态（PRD §4.4；§15-7 超期 30 天转态、15 天提醒 ×2 由 Beat 驱动）
# ---------------------------------------------------------------------------
CONFIRMATION = StateMachine("函证", {
    "台账已生成": (
        Transition("已发出", requires=("issued_by", "waybill_no")),
    ),
    "已发出": (
        Transition("已回函", requires=("reply_doc",)),
        Transition("超期未回", requires=("overdue_days",)),  # overdue_days >= 30 由 Beat 校验
    ),
    "超期未回": (
        Transition("已回函", requires=("reply_doc",)),
        Transition("替代程序", requires=("alt_test_doc",)),  # 累计两次提醒后出具替代程序建议
    ),
    "已回函": (
        Transition("核对一致", requires=("matched_amount",)),
        Transition("差异处理", requires=("difference_record",)),
    ),
    "差异处理": (
        Transition("替代程序", requires=("alt_test_doc",)),
    ),
    "替代程序": (
        Transition("闭环", requires=("closed_by",)),
    ),
    "核对一致": (
        Transition("闭环", requires=("closed_by",)),
    ),
    "闭环": (),
})

# ---------------------------------------------------------------------------
# 问题六状态（PRD §4.5；AI 归集默认「已归集」，误报→已排除留痕理由）
# ---------------------------------------------------------------------------
FINDING = StateMachine("问题", {
    "已归集": (
        Transition("已确认", requires=("confirmed_by",)),
        Transition("已排除", requires=("operator", "reason")),  # 误报标记留痕理由
    ),
    "已确认": (
        Transition("处理中", requires=("handler",)),
    ),
    "处理中": (
        Transition("已解决", requires=("resolution",)),
    ),
    "已解决": (
        Transition("已关闭", requires=("closer", "escalation_cleared")),
        # escalation_cleared：涉及报告意见类型的分歧须先经合伙人裁决（FR-05-05）
    ),
    "已排除": (),
    "已关闭": (),
})

# ---------------------------------------------------------------------------
# 项目四状态（PRD §4 + §15-17：暂停=阶段门冻结禁止写操作；终止=终止归档包）
# ---------------------------------------------------------------------------
PROJECT = StateMachine("项目", {
    "进行中": (
        Transition("暂停", requires=("approved_by_partner", "reason")),
        Transition("已归档", requires=("archive_package_hash",)),
        Transition("终止", requires=("approved_by_partner", "termination_package_hash")),
    ),
    "暂停": (
        Transition("进行中", requires=("approved_by_partner",)),  # 恢复从冻结门继续；超12个月提示转归档/终止
        Transition("终止", requires=("approved_by_partner", "termination_package_hash")),
    ),
    "已归档": (),
    "终止": (),
})


# ---------------------------------------------------------------------------
# 差错单六状态（PRD §15-16 归档后差错处理链：质控登记→合伙人批准→解锁受影响底稿
# 副本修订（原稿锁定保留，版本链追加）→质控复核→重新归档生成 v(n+1) 归档包）
# ---------------------------------------------------------------------------
CORRECTION = StateMachine("差错单", {
    "待批准": (
        Transition("修订中", requires=("approved_by_partner",)),  # 批准即解锁副本修订
        Transition("已驳回", requires=("approved_by_partner", "reason")),  # 驳回必附原因
    ),
    "修订中": (
        Transition("待质控复核", requires=("submitted_by", "revision_registered")),
    ),
    "待质控复核": (
        Transition("复核通过", requires=("qc_reviewer",)),
        Transition("修订中", requires=("qc_reviewer", "opinion")),  # 退回必附意见
    ),
    "复核通过": (
        Transition("已重新归档", requires=("rearchive_package_hash",)),
    ),
    "已重新归档": (),
    "已驳回": (),
})


ALL_MACHINES = {
    "worksheet": WORKSHEET,
    "confirmation": CONFIRMATION,
    "finding": FINDING,
    "project": PROJECT,
    "correction": CORRECTION,
}
