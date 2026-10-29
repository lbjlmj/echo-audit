"""权限码 RBAC（TECH_DESIGN §4.2.2，覆盖 PRD §3 矩阵 10 项动作）。

权限码-角色矩阵配置化；全局角色∩项目角色两层叠加，前端仅兜底、后端为准（AGENTS.md 硬约束 5）。
矩阵动作等级：approve(审批/终审/签发/批准) > operate(编制/复核/确认/处理/会签/审核)
           > propose(申请/提案/提交/草稿/建议) > view(查看/抽查/调阅) > none(无)。
PRD §3 原文动作保留在 ACTIONS 注释，联调时逐项对照测试（§7.3 权限矩阵测试）。
"""
from __future__ import annotations

from enum import IntEnum


class Level(IntEnum):
    NONE = 0
    VIEW = 1
    PROPOSE = 2
    OPERATE = 3
    APPROVE = 4


# 全局角色（PRD §3）
PARTNER = "合伙人"
MANAGER = "项目经理"
AUDITOR = "审计员"
QC = "质控专员"
ALL_ROLES = (PARTNER, MANAGER, AUDITOR, QC)

# 10 权限码 × 4 全局角色（值为该角色可执行的最高等级）
# ACTIONS 记录 PRD §3 原文：{权限码: {角色: 原文动作}}
MATRIX: dict[str, dict[str, Level]] = {
    # 立项/终止：审批 | 申请 | 无 | 建议
    "project.approve": {PARTNER: Level.APPROVE, MANAGER: Level.PROPOSE, AUDITOR: Level.NONE, QC: Level.PROPOSE},
    # 计划确认：审批 | 编制确认 | 无 | 查看
    "plan.confirm": {PARTNER: Level.APPROVE, MANAGER: Level.OPERATE, AUDITOR: Level.NONE, QC: Level.VIEW},
    # 底稿编制：查看 | 编制/复核 | 编制 | 查看
    "worksheet.edit": {PARTNER: Level.VIEW, MANAGER: Level.OPERATE, AUDITOR: Level.OPERATE, QC: Level.VIEW},
    # 复核签发：终审 | 复核（非本人编制） | 无 | 质控复核
    "worksheet.review_sign": {PARTNER: Level.APPROVE, MANAGER: Level.OPERATE, AUDITOR: Level.NONE, QC: Level.OPERATE},
    # AI 采信：审批 | 复核确认 | 提交待复核 | 抽查
    "ai_output.confirm": {PARTNER: Level.APPROVE, MANAGER: Level.OPERATE, AUDITOR: Level.PROPOSE, QC: Level.VIEW},
    # 问题处理：查看分歧项 | 确认与处理 | 反馈说明 | 抽查
    "finding.handle": {PARTNER: Level.VIEW, MANAGER: Level.OPERATE, AUDITOR: Level.PROPOSE, QC: Level.VIEW},
    # 报告签发：签发 | 草稿 | 无 | 会签
    "report.sign": {PARTNER: Level.APPROVE, MANAGER: Level.PROPOSE, AUDITOR: Level.NONE, QC: Level.OPERATE},
    # Skill/模板发布：批准 | 提案 | 无 | 审核
    "skill.publish": {PARTNER: Level.APPROVE, MANAGER: Level.PROPOSE, AUDITOR: Level.NONE, QC: Level.OPERATE},
    # 知识库写入：批准（保留驳回权） | 提交（经审核） | 提案 | 直接写入（免自审）+审核他人
    "knowledge.write": {PARTNER: Level.APPROVE, MANAGER: Level.PROPOSE, AUDITOR: Level.PROPOSE, QC: Level.OPERATE},
    # 归档调阅：全所调阅 | 项目内 | 项目内只读 | 全所调阅
    "archive.access": {PARTNER: Level.APPROVE, MANAGER: Level.OPERATE, AUDITOR: Level.VIEW, QC: Level.APPROVE},
}

PERMISSIONS: tuple[str, ...] = tuple(MATRIX)


class PermissionDenied(Exception):
    pass


def can(role: str, permission: str, required: Level = Level.VIEW) -> bool:
    if permission not in MATRIX:
        raise KeyError(f"未注册权限码：{permission}（合法值：{PERMISSIONS}）")
    if role not in ALL_ROLES:
        raise KeyError(f"未知全局角色：{role}")
    return MATRIX[permission][role] >= required


def assert_allowed(role: str, permission: str, required: Level = Level.VIEW) -> None:
    if not can(role, permission, required):
        raise PermissionDenied(f"角色「{role}」无权限「{permission}」（要求等级 {required.name}）")


def check_preparer_reviewer_separation(preparer_id: str, reviewer_id: str) -> None:
    """职责分离（§7 R1）：编制与复核不得同一人。服务层校验；库端另有 CHECK 约束。"""
    if preparer_id == reviewer_id:
        raise PermissionDenied("编制人与复核人不得为同一人（R1 双签职责分离）")
