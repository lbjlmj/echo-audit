"""立项域业务逻辑（FR-01-01 承接评价、FR-01-02 立项建档；PRD §5 FR-01）。

规则落点：
- 承接评价任一项未通过即拦截（FR-01-01），拦截动作本身留痕可追溯；
- 立项审批通过（合伙人，project.approve=APPROVE）后自动建档：生成项目编号、
  初始化八阶段门、承接门按 engine.pass_gate 裁决（FR-01-02）；
- 项目状态/阶段流转只经 engine（硬约束 2）；留痕一律经 core.append_trail 续链（硬约束 4/9）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.audit_middleware import append_trail
from ...core.permissions import Level, assert_allowed
from ...engine.fee_rules import check_fee
from ...engine.industry_matrix import MATRIX_TYPES, scope_of
from ...engine.stage_gate import GATE_CONDITION_KEYS, STAGES, pass_gate
from ...models.project import AuditProject, Client, StageGate
from ...models.user import ProjectMember, User

# PR-1xxx 立项域错误码
CODE_REJECTED = "PR-1001"  # 承接评价未通过
CODE_NOT_FOUND = "PR-1002"  # 项目或客户不存在
CODE_ALREADY_APPROVED = "PR-1003"  # 重复审批
CODE_MEMBER_CONFLICT = "PR-1004"  # 成员重复登记
CODE_PARAM = "PR-1005"  # 非法行业模板 / 项目状态不可变更

PROJECT_ROLE_APPROVE = "project.approve"


class IntakeRejected(Exception):
    """承接评价未通过（FR-01-01 拦截）。"""

    def __init__(self, failed: list[str]) -> None:
        self.failed = failed
        super().__init__(f"承接评价未通过项：{failed}（FR-01-01 拦截立项）")


class NotFound(Exception):
    pass


class MemberConflict(Exception):
    pass


def _snapshot(project: AuditProject) -> dict:
    return {
        "project_no": project.project_no,
        "name": project.name,
        "stage": project.current_stage,
        "status": project.status,
    }


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def generate_project_no(db: Session, year: int | None = None) -> str:
    """项目编号：A{年份}-{四位当年序号}（申请时生成；唯一约束兜底并发）。"""
    year = year or _now().year
    count = db.scalar(
        select(func.count(AuditProject.id)).where(AuditProject.project_no.like(f"A{year}-%"))
    )
    return f"A{year}-{(count or 0) + 1:04d}"


def submit_intake(
    db: Session,
    operator: User,
    *,
    name: str,
    client_id: str,
    business_type: str,
    period: str,
    evaluation: dict,
    pending_members: list[dict] | None = None,
    contract: dict | None = None,
) -> AuditProject:
    """FR-01-01：提交业务承接评价 + 立项申请（project.approve ≥ PROPOSE）。

    pending_members：拟任团队（一人一项目单角色，重复即拒绝——R1 在入口即拦截）。
    contract：合同信息（total_assets/fee_amount，可空）——提交即按《业务收费
    管理办法》校验报价合规性（FR-01-06），不通过阻断立项并提示差异项。
    """
    assert_allowed(operator.global_role, PROJECT_ROLE_APPROVE, Level.PROPOSE)
    if db.get(Client, client_id) is None:
        raise NotFound("客户不存在")
    member_list = pending_members or []
    if len({m["user_id"] for m in member_list}) != len(member_list):
        raise MemberConflict(CODE_MEMBER_CONFLICT)  # 同一用户不可重复配置角色

    checks = {
        "independence_ok": bool(evaluation.get("independence_ok")),
        "client_risk_ok": bool(evaluation.get("client_risk_ok")),
        "competence_ok": bool(evaluation.get("competence_ok")),
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        # 拦截动作本身留痕（拒立项也要可追溯）
        append_trail(
            db, tenant_id=operator.tenant_id, entity_type="client", entity_id=client_id,
            action="承接评价拦截", operator=operator.name,
            after={"failed": failed, "notes": evaluation.get("notes")},
        )
        raise IntakeRejected(failed)

    # FR-01-06 收费校验：录入合同即校验，不通过阻断立项（差异项随拦截信息提示）
    contract = contract or {}
    fee_result: dict[str, object] | None = None
    if "total_assets" in contract and "fee_amount" in contract:
        try:
            fee_result = check_fee(
                Decimal(str(contract["total_assets"])),
                Decimal(str(contract["fee_amount"])),
            )
        except (ValueError, ArithmeticError) as exc:
            raise IntakeRejected([f"收费校验入参非法：{exc}"]) from exc
        if not fee_result["passed"]:
            append_trail(
                db, tenant_id=operator.tenant_id, entity_type="client", entity_id=client_id,
                action="收费校验拦截（FR-01-06）", operator=operator.name,
                after=fee_result,
            )
            raise IntakeRejected([
                f"报价低于收费基准（{fee_result['tier_label']} 应不低于 "
                f"{fee_result['min_fee']} 元，缺口 {fee_result['gap']} 元）"
            ])

    project = AuditProject(
        project_no=generate_project_no(db),
        name=name,
        client_id=client_id,
        business_type=business_type,
        period=period,
        fee_amount=(Decimal(str(contract["fee_amount"]))
                    if "fee_amount" in contract else None),
        total_assets=(Decimal(str(contract["total_assets"]))
                      if "total_assets" in contract else None),
        approval_summary={
            **evaluation,
            "submitted_by": operator.name,
            "approval_status": "待审批",
            "fee_check": fee_result,  # 校验结论留档（审批时可见）
            "pending_members": pending_members,  # 拟任团队，审批通过后自动落库（FR-01-02）
        },
    )
    db.add(project)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_project", entity_id=project.id,
        action="立项申请", operator=operator.name, after=_snapshot(project),
    )
    if fee_result is not None:
        append_trail(
            db, tenant_id=operator.tenant_id, entity_type="audit_project",
            entity_id=project.id, action="收费校验通过（FR-01-06）",
            operator=operator.name, after=fee_result,
        )
    return project


def approve_intake(db: Session, operator: User, project_id: str) -> AuditProject:
    """FR-01-02：立项审批（project.approve=APPROVE）→ 自动建档。

    建档动作：生成并固化项目编号、初始化八阶段门（全部未开始）、
    承接门按 engine.pass_gate 裁决（约定书已核 + 独立性通过 → 已通过）。
    """
    assert_allowed(operator.global_role, PROJECT_ROLE_APPROVE, Level.APPROVE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound("项目不存在")
    summary = dict(project.approval_summary or {})
    if summary.get("approval_status") == "已批准":
        raise MemberConflict(CODE_ALREADY_APPROVED)  # 复用冲突语义：PR-1003

    before = _snapshot(project)
    summary |= {
        "approved_by": operator.name, "approved_at": _now().isoformat(), "approval_status": "已批准",
        # FR-02-04：行业模板随建档圈定——快照模板范围（资料清单 + 底稿模板）供计划域与资料域消费
        "template_scope": scope_of(project.business_type),
    }
    project.approval_summary = summary  # JSONB 整体赋值以触发更新

    for stage in STAGES:  # 初始化八阶段门
        db.add(
            StageGate(
                project_id=project.id,
                stage=stage,
                gate_status="未开始",
                conditions_snapshot={k: False for k in GATE_CONDITION_KEYS[stage]},
            )
        )
    db.flush()

    engagement_ok = bool(summary.get("engagement_letter_ok"))
    independence_ok = bool(summary.get("independence_ok"))
    gate = db.scalar(
        select(StageGate).where(StageGate.project_id == project.id, StageGate.stage == "承接")
    )
    try:  # 条件未满足保持「未开始」，不阻断审批（约定书可后补再过门）
        gate.gate_status = pass_gate(
            "承接",
            {"project_approved": True, "engagement_letter": engagement_ok, "independence_check": independence_ok},
        )
    except ValueError:
        gate.gate_status = "未开始"
    gate.passed_by = operator.id
    gate.passed_at = _now()

    # FR-01-02：团队权限初始化——申请时提交的拟任团队随审批通过自动落库
    for pending in summary.get("pending_members", []):
        db.add(
            ProjectMember(
                project_id=project.id, user_id=pending["user_id"], project_role=pending["project_role"]
            )
        )
    summary.pop("pending_members", None)
    project.approval_summary = summary

    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_project", entity_id=project.id,
        action="立项审批", operator=operator.name, before=before,
        after=_snapshot(project) | {
            "template_scope": summary["template_scope"],  # FR-02-04 圈定留痕
        },
    )
    return project


def list_industry_templates(db: Session, operator: User) -> dict[str, dict]:
    """行业模板矩阵概览（FR-02-04，project.approve ≥ VIEW）：八类高频行业。"""
    assert_allowed(operator.global_role, PROJECT_ROLE_APPROVE, Level.VIEW)
    return {t: scope_of(t) for t in MATRIX_TYPES}


def select_industry_template(
    db: Session, operator: User, project_id: str, *, industry_template: str
) -> AuditProject:
    """计划阶段圈定/变更行业模板（FR-02-04，plan.confirm ≥ OPERATE，经理职能）。

    PRD「立项或计划阶段选择」：立项时随建档圈定，计划阶段可按实际改判；
    变更同步 business_type 并重算模板范围快照，before/after 全量留痕；
    非进行中项目（暂停冻结/终止/已归档）拒绝变更（§15-17 冻结口径）。
    """
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound("项目不存在")
    if industry_template not in MATRIX_TYPES:
        raise ValueError(f"{CODE_PARAM} 非高频矩阵行业：{industry_template}")
    if project.status != "进行中":
        raise ValueError(f"{CODE_PARAM} 项目状态 {project.status} 不可变更行业模板（§15-17）")
    summary = dict(project.approval_summary or {})
    before = {
        "business_type": project.business_type,
        "template_scope": summary.get("template_scope"),
    }
    project.business_type = industry_template
    scope = scope_of(industry_template)
    summary["template_scope"] = scope
    project.approval_summary = summary  # JSONB 整体赋值以触发更新
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_project", entity_id=project.id,
        action="行业模板圈定/变更（FR-02-04）", operator=operator.name,
        before=before,
        after={"business_type": industry_template,
               "materials": len(scope["materials"]),
               "worksheet_templates": len(scope["worksheet_templates"])},
    )
    return project


def list_projects(db: Session, operator: User) -> list[AuditProject]:
    """项目列表（project.approve ≥ VIEW；审计员按 PRD §3 矩阵无立项查看权限）。"""
    assert_allowed(operator.global_role, PROJECT_ROLE_APPROVE, Level.VIEW)
    return list(db.scalars(select(AuditProject).order_by(AuditProject.created_at)).all())


# ---------------------------------------------------------------------------
# FR-02-05 团队与工时：人员配置（角色随立项已分配）+ 工时预算/实际对比 + 偏差告警
# ---------------------------------------------------------------------------
def _assert_project_writable(project: AuditProject) -> None:
    if project.status in ("已归档", "终止"):
        raise MemberConflict(f"{CODE_PARAM} 项目 {project.status}，团队工时不可再配置")


def configure_team_hours(
    db: Session, operator: User, project_id: str, *, members: list[dict]
) -> list[ProjectMember]:
    """成员工时配置（upsert 幂等，plan.confirm ≥ OPERATE 即项目经理配置职能）。

    members: [{"user_id", "project_role"(可选), "budget_hours", "actual_hours"}]——
    已存在成员行则更新角色/工时，不存在则新增（角色校验沿用库端枚举约束）；
    实际工时更新即留痕（对比口径可追溯）。
    """
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound("项目不存在")
    _assert_project_writable(project)
    if not members:
        raise MemberConflict(f"{CODE_PARAM} 配置项不可为空")

    result: list[ProjectMember] = []
    for row in members:
        budget, actual = row.get("budget_hours"), row.get("actual_hours")
        if (budget is not None and Decimal(str(budget)) < 0) or (
            actual is not None and Decimal(str(actual)) < 0
        ):
            raise MemberConflict(f"{CODE_PARAM} 工时不可为负：{row}")
        member = db.scalar(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id, ProjectMember.user_id == row["user_id"]
            )
        )
        before = (f"{member.project_role} 预算{member.budget_hours} 实际{member.actual_hours}"
                  if member else None)
        if member is None:
            member = ProjectMember(
                project_id=project_id, user_id=row["user_id"],
                project_role=row.get("project_role") or "编制",
                budget_hours=Decimal(str(budget)) if budget is not None else Decimal("0"),
                actual_hours=Decimal(str(actual)) if actual is not None else Decimal("0"),
            )
            db.add(member)
        else:
            if row.get("project_role"):
                member.project_role = row["project_role"]
            if budget is not None:  # 未提供的字段保留原值（upsert 局部更新）
                member.budget_hours = Decimal(str(budget))
            if actual is not None:
                member.actual_hours = Decimal(str(actual))
        result.append(member)
    db.flush()
    for member in result:
        user = db.get(User, member.user_id)
        append_trail(
            db, tenant_id=operator.tenant_id, entity_type="team_member",
            entity_id=member.id, action="团队工时配置（FR-02-05）",
            operator=operator.name, before={"member": before},
            after={"user": user.name if user else member.user_id,
                   "project_role": member.project_role,
                   "budget_hours": float(member.budget_hours),
                   "actual_hours": float(member.actual_hours)},
        )
    return result


def team_overview(db: Session, operator: User, project_id: str) -> dict[str, Any]:
    """工时对比视图（plan.confirm ≥ VIEW）：预算/实际合计 + 偏差超 10% 告警。"""
    assert_allowed(operator.global_role, "plan.confirm", Level.VIEW)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound("项目不存在")
    rows = list(db.scalars(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    ).all())
    members = [
        {
            "member_id": m.id,
            "user": db.get(User, m.user_id).name if db.get(User, m.user_id) else m.user_id,
            "project_role": m.project_role,
            "budget_hours": float(m.budget_hours), "actual_hours": float(m.actual_hours),
            "deviation_pct": (
                round(abs(float(m.actual_hours) - float(m.budget_hours))
                      / float(m.budget_hours) * 100, 1)
                if float(m.budget_hours) > 0
                else (100.0 if float(m.actual_hours) > 0 else 0.0)
            ),
        }
        for m in rows
    ]
    budget_total = sum(m.budget_hours for m in rows)
    actual_total = sum(m.actual_hours for m in rows)
    if budget_total > 0:
        deviation_pct = round(abs(float(actual_total) - float(budget_total))
                              / float(budget_total) * 100, 1)
    else:
        deviation_pct = 100.0 if actual_total > 0 else 0.0
    return {
        "project_id": project_id, "members": members,
        "budget_total": float(budget_total), "actual_total": float(actual_total),
        "deviation_pct": deviation_pct,
        "over_threshold": deviation_pct > 10.0,  # 偏差超 10% 面板告警（FR-02-05）
    }
