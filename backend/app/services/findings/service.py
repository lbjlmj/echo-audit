"""问题域业务逻辑（FR-03-06 归集 + §4.5 六状态 + FR-05-05 分歧裁决）。

规则落点：
- 六状态流转一律经 engine.FINDING（硬约束 2），禁止 service 自创状态；
- 「已归集」即 AI 线索态（红线 1/R2）：确认/排除是项目经理的人工复核标记，
  逐条经状态机流转，AI 归集调用另写 AIRun 留痕（module=ai_finding，线索态）；
- 误报排除必附理由（§4.5）；已排除/已关闭为终态；
- 涉及报告意见类型的问题升级合伙人裁决（FR-05-05）：
  升级登记 finding.handle ≥ PROPOSE（任一环节可反馈），裁决 project.approve ≥ APPROVE
  （项目级审批语义，合伙人专属）；escalation_ref 未落（裁决前）不得关闭（engine 前置）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...ai import pipeline
from ...core.audit_middleware import append_trail
from ...core.permissions import Level, assert_allowed
from ...engine.state_machines import FINDING, IllegalTransition
from ...models.audit import AIRun as AIRunORM
from ...models.finding import Finding
from ...models.project import AuditProject
from ...models.user import User
from ...models.worksheet import Worksheet

# FD-1xxx 问题域错误码
CODE_TRANSITION = "FD-1001"
CODE_NOT_FOUND = "FD-1002"
CODE_FROZEN = "FD-1005"
CODE_PARAM = "FD-1007"

# AI 归集留痕口径（FR-03-06；规则模板通道，大模型通道默认关闭 §15-15）
_SKILL_VERSION = "finding-collect@1.0.0"
_MODEL_VERSION = "rule-template-v1"


class NotFound(Exception):
    pass


class Locked(Exception):
    pass


class ProjectFrozen(Exception):
    pass


class TransitionRejected(Exception):
    pass


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_finding(db: Session, finding_id: str) -> Finding:
    row = db.get(Finding, finding_id)
    if row is None:
        raise NotFound(CODE_NOT_FOUND)
    return row


def _assert_writable(project: AuditProject) -> None:
    if project.status == "暂停":
        raise ProjectFrozen(CODE_FROZEN)
    if project.status in ("已归档", "终止"):  # 终结态：禁止一切写操作（§15-17，与 quality 同口径）
        raise Locked(f"{CODE_LOCKED} 项目已终结（{project.status}），禁止写操作")


def _transit(row: Finding, target: str, **ctx: object) -> str:
    """六状态流转只走 engine（硬约束 2）：裁决通过才赋回状态字段。"""
    try:
        row.status = FINDING.transit(row.status, target, **ctx)
    except IllegalTransition as exc:
        raise TransitionRejected(f"{CODE_TRANSITION} {exc}") from exc
    return row.status


def collect_findings(
    db: Session, operator: User, project_id: str, worksheet_id: str,
    *, items: list[str], source: str = "人工",
) -> tuple[list[Finding], str | None]:
    """问题归集（FR-03-06，finding.handle ≥ PROPOSE）：审计员反馈说明即可登记，进「已归集」待确认。

    source=「AI 归集」时写一条 AIRun 留痕（module=ai_finding，线索态，
    related 挂项目级，不阻塞底稿 R2 门禁——逐条确认经本域状态机端点完成）。
    """
    assert_allowed(operator.global_role, "finding.handle", Level.PROPOSE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(project)
    if db.get(Worksheet, worksheet_id) is None:
        raise NotFound(CODE_NOT_FOUND)
    descriptions = [d.strip() for d in items if d and d.strip()]
    if not descriptions:
        raise ValueError(f"{CODE_PARAM} 问题描述必填")
    if source not in ("人工", "AI 归集"):
        raise ValueError(f"{CODE_PARAM} 来源仅支持「人工/AI 归集」")

    run_no: str | None = None
    if source == "AI 归集":
        run = pipeline.AIRun(
            project_id=project_id, module="ai_finding",
            skill_version=_SKILL_VERSION, model_version=_MODEL_VERSION,
            triggered_by=operator.id, input_material_ids=[],
        )
        pipeline.seal_output(
            run, {"count": len(descriptions), "source": source},
            output_key=f"findings/{project_id}/collect.json",
        )
        orm = AIRunORM(
            run_no=run.run_no, project_id=project_id, module=run.module,
            skill_version=run.skill_version, model_version=run.model_version,
            triggered_by=operator.id,
            input_material_ids=run.input_material_ids, input_hashes=run.input_hashes,
            output_digest=run.output_digest, output_key=run.output_key,
            status="线索", related_type="project", related_id=project_id,
        )
        db.add(orm)
        db.flush()
        run_no = orm.run_no

    rows = [Finding(project_id=project_id, worksheet_id=worksheet_id, description=d, source=source)
            for d in descriptions]
    db.add_all(rows)
    db.flush()
    for row in rows:
        append_trail(
            db, tenant_id=operator.tenant_id, entity_type="finding", entity_id=row.id,
            action="问题归集", operator=operator.name,
            after={"status": "已归集", "source": source, "ai_run_no": run_no},
        )
    return rows, run_no


def list_findings(db: Session, project_id: str) -> list[Finding]:
    """问题清单（finding.handle ≥ VIEW，PRD §3：合伙人查看分歧项/质控抽查）。"""
    return list(db.scalars(
        select(Finding).where(Finding.project_id == project_id).order_by(Finding.created_at)
    ))


def confirm_finding(db: Session, operator: User, finding_id: str) -> Finding:
    """确认成立（已归集→已确认，finding.handle ≥ OPERATE）：项目经理人工复核标记。"""
    assert_allowed(operator.global_role, "finding.handle", Level.OPERATE)
    row = _get_finding(db, finding_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    _transit(row, "已确认", confirmed_by=operator.id)
    row.responsible_manager_id = operator.id
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="finding", entity_id=row.id,
        action="问题确认", operator=operator.name, after={"status": "已确认"},
    )
    return row


def reject_finding(db: Session, operator: User, finding_id: str, *, reason: str) -> Finding:
    """误报排除（已归集→已排除，finding.handle ≥ OPERATE）：必附理由留痕（§4.5）。"""
    assert_allowed(operator.global_role, "finding.handle", Level.OPERATE)
    row = _get_finding(db, finding_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if not (reason and reason.strip()):
        raise ValueError(f"{CODE_PARAM} 排除理由必填（误报标记留痕，§4.5）")
    _transit(row, "已排除", operator=operator.id, reason=reason.strip())
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="finding", entity_id=row.id,
        action="误报排除", operator=operator.name, after={"status": "已排除", "reason": reason.strip()},
    )
    return row


def start_handling(db: Session, operator: User, finding_id: str) -> Finding:
    """启动处理（已确认→处理中，finding.handle ≥ OPERATE）。"""
    assert_allowed(operator.global_role, "finding.handle", Level.OPERATE)
    row = _get_finding(db, finding_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    _transit(row, "处理中", handler=operator.id)
    row.responsible_manager_id = operator.id
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="finding", entity_id=row.id,
        action="启动处理", operator=operator.name, after={"status": "处理中"},
    )
    return row


def resolve_finding(db: Session, operator: User, finding_id: str, *, resolution: str) -> Finding:
    """登记解决（处理中→已解决，finding.handle ≥ OPERATE）：处理结果必填。"""
    assert_allowed(operator.global_role, "finding.handle", Level.OPERATE)
    row = _get_finding(db, finding_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if not (resolution and resolution.strip()):
        raise ValueError(f"{CODE_PARAM} 处理结果必填")
    _transit(row, "已解决", resolution=resolution.strip())
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="finding", entity_id=row.id,
        action="登记解决", operator=operator.name,
        after={"status": "已解决", "resolution": resolution.strip()},
    )
    return row


def escalate_finding(db: Session, operator: User, finding_id: str, *, note: str) -> Finding:
    """分歧升级登记（FR-05-05，finding.handle ≥ PROPOSE）：任一环节可上报，不转态。

    升级后（escalated=True）相关问题不得关闭，直至合伙人裁决落 escalation_ref。
    """
    assert_allowed(operator.global_role, "finding.handle", Level.PROPOSE)
    row = _get_finding(db, finding_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if not (note and note.strip()):
        raise ValueError(f"{CODE_PARAM} 分歧说明必填")
    if row.status in ("已排除", "已关闭"):
        raise Locked(f"{CODE_LOCKED} 终态问题不可升级分歧")
    row.escalated = True
    row.escalation_note = note.strip()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="finding", entity_id=row.id,
        action="分歧升级登记", operator=operator.name,
        after={"escalated": True, "note": note.strip()},
    )
    return row


def adjudicate_finding(db: Session, operator: User, finding_id: str, *, adjudication_ref: str) -> Finding:
    """合伙人裁决（FR-05-05，project.approve ≥ APPROVE）：落裁决引用，解除关闭阻塞。"""
    assert_allowed(operator.global_role, "project.approve", Level.APPROVE)
    row = _get_finding(db, finding_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if not (adjudication_ref and adjudication_ref.strip()):
        raise ValueError(f"{CODE_PARAM} 裁决记录引用必填")
    if not row.escalated:
        raise ValueError(f"{CODE_PARAM} 未升级分歧的问题无需裁决")
    row.escalation_ref = adjudication_ref.strip()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="finding", entity_id=row.id,
        action="合伙人分歧裁决", operator=operator.name,
        after={"escalation_ref": row.escalation_ref},
    )
    return row


def close_finding(db: Session, operator: User, finding_id: str) -> Finding:
    """关闭问题（已解决→已关闭，finding.handle ≥ OPERATE，复核确认）。

    涉及分歧且未裁决的问题：engine 前置 escalation_cleared 不满足 → 409（FR-05-05）。
    """
    assert_allowed(operator.global_role, "finding.handle", Level.OPERATE)
    row = _get_finding(db, finding_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    escalation_cleared = (not row.escalated) or bool(row.escalation_ref)
    _transit(row, "已关闭", closer=operator.id, escalation_cleared=escalation_cleared)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="finding", entity_id=row.id,
        action="问题关闭（复核确认）", operator=operator.name,
        after={"status": "已关闭", "escalation_cleared": escalation_cleared},
    )
    return row
