"""报告域业务逻辑（FR-05-03/04；PRD §5.5、§7 R1/R2、FR-05-05 分歧阻签）。

规则落点：
- 附注 AI（FR-05-03）恒为线索（module=ai_notes、related=report，硬约束 3）：
  完成门通过后由项目经理发起；底稿数据经纯规则拼装附注草稿（零依赖，大模型通道默认关闭）；
  第三段人工复核采信前 notes_draft_key 不落库——附注永远不进报告稿（§7 R2）；
- 报告审批流（FR-05-04）：模板出稿（经理）→ 质控会签 → 合伙人签发，三步全部留痕；
  报告三状态（草稿/会签/已签发）不在 §4 四状态机范围（硬约束 2 未涵盖），由服务层
  按前置严格流转并逐留痕（同抽凭执行状态模式），签发即报告门（门 7）report_signed 成立；
- FR-05-05 分歧阻签：项目内存在升级未裁决（escalated 且无裁决引用）的问题，不得签发；
- 权限以后端矩阵为准（硬约束 5）：发起/出稿 plan.confirm≥OPERATE（审计员 NONE、质控 VIEW
  天然隔离）、会签 report.sign≥OPERATE（质控复核）、签发 report.sign≥APPROVE（合伙人专属）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...ai import pipeline
from ...core.audit_middleware import append_trail
from ...core.permissions import Level, assert_allowed
from ...core.storage import get_storage
from ...models.audit import AIRun as AIRunORM
from ...models.finding import AuditReport, Finding
from ...models.project import AuditProject, Client, StageGate
from ...models.user import User
from ...models.worksheet import Worksheet
from . import render

# RP-1xxx 报告域错误码
CODE_GATE = "RP-1001"
CODE_NOT_FOUND = "RP-1002"
CODE_LOCKED = "RP-1006"
CODE_FROZEN = "RP-1005"
CODE_PARAM = "RP-1007"

NOTES_VERSION = "notes-draft@1.0.0"  # 附注 Skill 版本占位（纯规则拼装，零依赖）
NOTES_MODEL_VERSION = "notes-rules@1.0.0"


class NotFound(Exception):
    pass


class Locked(Exception):
    pass


class ProjectFrozen(Exception):
    pass


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _assert_writable(project: AuditProject) -> None:
    if project.status == "暂停":
        raise ProjectFrozen(CODE_FROZEN)
    if project.status in ("已归档", "终止"):  # 终结态：报告域禁止一切写操作（§15-17）
        raise Locked(f"{CODE_LOCKED} 项目已终结（{project.status}），禁止写操作")


def _get_project(db: Session, project_id: str) -> AuditProject:
    row = db.get(AuditProject, project_id)
    if row is None:
        raise NotFound(f"{CODE_NOT_FOUND} 项目不存在")
    return row


def _get_report(db: Session, report_id: str) -> AuditReport:
    row = db.get(AuditReport, report_id)
    if row is None:
        raise NotFound(f"{CODE_NOT_FOUND} 报告不存在")
    return row


def _unadjudicated_count(db: Session, project_id: str) -> int:
    """升级未裁决的分歧数（FR-05-05：裁决前不得签发）。"""
    return int(db.scalar(
        select(func.count(Finding.id)).where(
            Finding.project_id == project_id,
            Finding.escalated.is_(True),
            Finding.escalation_ref.is_(None),
        )
    ) or 0)


# ---------------------------------------------------------------------------
# FR-05-03 附注 AI：完成门通过后发起 → 线索态 → 人工复核采信 → 进入报告稿
# ---------------------------------------------------------------------------
def generate_note_draft(
    db: Session, operator: User, project_id: str
) -> tuple[AuditReport, AIRunORM]:
    """发起附注 AI（plan.confirm ≥ OPERATE，审计员/质控不可发起）：产物为线索态。

    触发前置：完成门（门 6）已通过或双批跳过（§5.5 FR-05-03）；存在未复核附注线索时
    拒绝重复发起（一次性，防孤儿线索堆积）。
    """
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    project = _get_project(db, project_id)
    _assert_writable(project)

    gate = db.scalar(
        select(StageGate).where(StageGate.project_id == project_id, StageGate.stage == "完成")
    )
    if gate is None or gate.gate_status not in ("已通过", "跳过"):
        raise ValueError(f"{CODE_GATE} 完成门未通过（或未双批跳过），不可发起附注生成（FR-05-03 触发前置）")
    pending = db.scalar(
        select(AuditReport.id).where(
            AuditReport.project_id == project_id,
            AuditReport.notes_draft_key.is_(None),
            AuditReport.status == "草稿",
        )
    )
    if pending is not None:
        raise Locked(f"{CODE_LOCKED} 存在未复核采信的附注草稿（红线 1/R2），不可重复发起")

    worksheets = db.scalars(
        select(Worksheet).where(Worksheet.project_id == project_id)
    ).all()
    if not worksheets:
        raise ValueError(f"{CODE_PARAM} 项目无底稿，附注生成无数据来源")

    # 纯规则拼装附注草稿摘要（底稿清单 + 复核痕迹计数，同批数据可复现）
    note_sections = [
        {"底稿编号": w.code, "标题": w.title, "版本": w.current_version, "状态": w.status}
        for w in sorted(worksheets, key=lambda x: x.code)
    ]

    report_count = int(db.scalar(
        select(func.count(AuditReport.id)).where(AuditReport.project_id == project_id)
    ) or 0)
    version_no = f"v{report_count + 1}"
    report = AuditReport(project_id=project_id, version=version_no, status="草稿")
    db.add(report)
    db.flush()

    run = pipeline.AIRun(
        project_id=project_id, module="ai_notes",
        skill_version=NOTES_VERSION, model_version=NOTES_MODEL_VERSION,
        triggered_by=operator.id, input_material_ids=[],
    )
    pipeline.record_input(
        run, f"worksheets:{project_id}:{len(worksheets)}", str(len(worksheets)).encode("utf-8")
    )
    summary: dict[str, Any] = {"报告版本": version_no, "附注章节": note_sections}
    output_key = f"projects/{project_id}/notes-{run.run_no}.json"
    pipeline.seal_output(run, summary, output_key)

    orm = AIRunORM(
        run_no=run.run_no, project_id=project_id, module=run.module,
        skill_version=run.skill_version, model_version=run.model_version,
        triggered_by=operator.id,
        input_material_ids=run.input_material_ids, input_hashes=run.input_hashes,
        output_digest=run.output_digest, output_key=run.output_key,
        status="线索", related_type="report", related_id=report.id,
    )
    db.add(orm)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_report", entity_id=report.id,
        action="报告建档并发起附注生成（线索态）", operator=operator.name,
        after={"version": version_no, "run_no": run.run_no, "sections": len(note_sections)},
    )
    return report, orm


def apply_note_approved(db: Session, operator: User, run_row: AIRunORM) -> None:
    """采信回调（worksheets.mark_ai_review 分派）：附注复核通过 → 进入报告稿（FR-05-03 收口）。"""
    report = _get_report(db, run_row.related_id or "")
    report.notes_draft_key = run_row.output_key
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_report", entity_id=report.id,
        action="附注复核通过进入报告稿", operator=operator.name,
        after={"version": report.version, "notes_draft_key": report.notes_draft_key},
    )


def list_reports(db: Session, project_id: str) -> list[AuditReport]:
    """项目报告清单（读操作，登录即可）。"""
    return list(db.scalars(
        select(AuditReport).where(AuditReport.project_id == project_id)
    ).all())


def list_report_ai_runs(db: Session, report_id: str) -> list[AIRunORM]:
    """报告的附注 AI 运行线索（module=ai_notes、related=report；线索态醒目，硬约束 3）。"""
    _get_report(db, report_id)
    return list(db.scalars(
        select(AIRunORM)
        .where(AIRunORM.related_type == "report", AIRunORM.related_id == report_id)
        .order_by(AIRunORM.created_at.desc())
    ).all())


def list_report_trails(db: Session, report_id: str) -> list:
    """单份报告留痕时间线（发起附注/采信/出稿/会签/签发全程可追溯，所见即所查）。"""
    from ...models.audit import AuditTrail

    _get_report(db, report_id)
    return list(db.scalars(
        select(AuditTrail)
        .where(AuditTrail.entity_type == "audit_report", AuditTrail.entity_id == report_id)
        .order_by(AuditTrail.created_at)
    ).all())


# ---------------------------------------------------------------------------
# FR-05-04 报告审批流：模板出稿（经理）→ 质控会签 → 合伙人签发（分歧阻签）
# ---------------------------------------------------------------------------
def finalize_report(
    db: Session, operator: User, report_id: str, *, template_key: str
) -> AuditReport:
    """模板出稿（plan.confirm ≥ OPERATE）：附注已复核采信为前置（FR-05-04）。

    保真写回（硬约束 8，TECH_DESIGN §4.4）：openpyxl 打开行业报告模板 → 仅写 manifest
    白名单数据单元格（公式区/下拉框/样式零触碰）→ 落存对象 → 重开校验结构指纹，
    不一致即删除对象失败回滚。template_key 必须命中 templates/report/manifest.json 登记。
    """
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    report = _get_report(db, report_id)
    project = _get_project(db, report.project_id)
    _assert_writable(project)
    if not (template_key and template_key.strip()):
        raise ValueError(f"{CODE_PARAM} 报告模板对象键必填（FR-05-04 模板出稿）")
    if report.status != "草稿":
        raise ValueError(f"{CODE_GATE} 报告状态「{report.status}」不可出稿")
    if not report.notes_draft_key:
        raise ValueError(f"{CODE_GATE} 附注草稿未复核采信，不可出稿（FR-05-04 触发前置）")
    if report.file_key:
        raise Locked(f"{CODE_LOCKED} 报告已出稿（{report.file_key}），不得重复出稿")

    # 业务要素：仅事实数据，不编造（红线 1）；附注章节按同批规则复现（与发起时同源）
    client = db.get(Client, project.client_id)
    worksheets = db.scalars(
        select(Worksheet).where(Worksheet.project_id == project.id).order_by(Worksheet.code)
    ).all()
    sections = "\n".join(
        f"{w.code} {w.title}（{w.current_version}·{w.status}）" for w in worksheets
    )
    fields = {
        "B3": client.name if client else "—",
        "B4": project.project_no,
        "B5": project.period,
        "B6": report.version,
        "B7": operator.name,
        "B8": _now().date().isoformat(),
        "B10": sections or "（项目无底稿）",
        "B12": f"附注草稿 {report.notes_draft_key}（已采信）",
    }
    object_key = f"reports/{report.project_id}/{report.id}/{report.version}.xlsx"
    try:
        render.render_report(template_key.strip(), fields, storage=get_storage(), object_key=object_key)
    except render.TemplateNotRegistered as exc:
        raise ValueError(str(exc)) from exc
    # render.FingerprintMismatch（ValueError 子类）向上 → 路由映射 422 RP-1010；
    # 此时落盘对象已删、报告字段未写，天然回滚（硬约束 8）

    report.template_key = template_key.strip()
    report.file_key = object_key
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_report", entity_id=report.id,
        action="报告模板出稿", operator=operator.name,
        after={
            "version": report.version, "template_key": report.template_key,
            "file_key": report.file_key, "结构指纹校验": "通过（公式区/验证规则零触碰）",
        },
    )
    return report


def countersign_report(
    db: Session, operator: User, report_id: str, *, opinion: str
) -> AuditReport:
    """质控会签（report.sign ≥ OPERATE，经理 PROPOSE 被拒）：出稿后草稿→会签，会签人落库留痕。"""
    assert_allowed(operator.global_role, "report.sign", Level.OPERATE)
    report = _get_report(db, report_id)
    _assert_writable(_get_project(db, report.project_id))
    if not (opinion and opinion.strip()):
        raise ValueError(f"{CODE_PARAM} 会签意见必填")
    if report.status != "草稿" or not report.notes_draft_key or not report.file_key:
        raise ValueError(f"{CODE_GATE} 报告未模板出稿（附注未采信或出稿产物缺失），不可会签（FR-05-04）")

    report.status = "会签"
    report.countersigned_by = operator.id
    report.countersigned_at = _now()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_report", entity_id=report.id,
        action="报告质控会签", operator=operator.name,
        before={"status": "草稿"},
        after={"status": "会签", "opinion": opinion.strip(), "countersigned_by": operator.name},
    )
    return report


def sign_report(db: Session, operator: User, report_id: str) -> AuditReport:
    """合伙人签发（report.sign ≥ APPROVE）：会签后可签发；存在升级未裁决分歧时阻签（FR-05-05）。

    签发即报告门（门 7）准入条件 report_signed 成立，报告门由质控域 pass_stage_gate 串联。
    """
    assert_allowed(operator.global_role, "report.sign", Level.APPROVE)
    report = _get_report(db, report_id)
    project = _get_project(db, report.project_id)
    _assert_writable(project)
    if report.status != "会签":
        raise ValueError(f"{CODE_GATE} 报告状态「{report.status}」不可签发（须先经质控会签，FR-05-04）")
    pending = _unadjudicated_count(db, report.project_id)
    if pending:
        raise ValueError(
            f"{CODE_GATE} 存在 {pending} 条升级未裁决的意见分歧，裁决前不得签发（FR-05-05）"
        )

    report.status = "已签发"
    report.signed_by = operator.id
    report.signed_at = _now()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_report", entity_id=report.id,
        action="报告签发（合伙人）", operator=operator.name,
        before={"status": "会签"},
        after={"status": "已签发", "version": report.version, "project": project.project_no},
    )
    return report


# ---------------------------------------------------------------------------
# 模板矩阵与出稿产物读取（FR-05-04 配套）
# ---------------------------------------------------------------------------
def list_report_templates() -> dict[str, dict]:
    """已登记报告模板清单（templates/report/manifest.json，数据即代码随版本发布）。"""
    return render.load_manifest(render.TEMPLATE_DIR)


def get_report_file(db: Session, report_id: str) -> tuple[AuditReport, bytes]:
    """出稿产物字节（下载）：未出稿 → RP-1001；对象缺失 → RP-1002。"""
    report = _get_report(db, report_id)
    if not report.file_key:
        raise ValueError(f"{CODE_GATE} 报告尚未模板出稿，无产物可下载")
    storage = get_storage()
    if not storage.exists(report.file_key):
        raise NotFound(f"{CODE_NOT_FOUND} 出稿对象缺失：{report.file_key}")
    return report, storage.load_bytes(report.file_key)
