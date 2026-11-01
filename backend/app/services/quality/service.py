"""质控与归档域业务逻辑（FR-06-01/02/03/04；PRD §5.6、§4.2 门表、§7 R2/R3、§4.6）。

规则落点：
- 质控 AI 检查产物恒为线索（module=ai_qc，硬约束 3）：检查报告经 pipeline 三段式，
  人工采信后才作为完成门（门 6）准入依据；检查为纯 Python 规则实现，不经大模型通道；
- 归档前完整性校验（FR-06-02 归档自动执行）：缺人工复核标记的 AI 留痕一律阻断（红线 1/R2）、
  哈希链整链验证（硬约束 4）、底稿复核状态（§4.3 复核通过前不得进入归档包）、报告已签发；
- 归档动作（FR-06-04）：校验通过 → 归档清单快照 sha256 封包 → PROJECT/WORKSHEET 双状态机
  只走 engine（硬约束 2）收口「已归档」，归档门同步「已通过」（archive_package_sealed）；
- 阶段门流转（FR-06-03）：权限卡控走 engine.GATE_APPROVERS 单点，准入条件由服务层
  从 DB 收集、engine.pass_gate 裁决；跳过须合伙人 + 质控双批（R3）；暂停期间门冻结（§15-17）。
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...ai import pipeline
from ...core.audit_middleware import append_trail, verify_chain
from ...core.permissions import QC, Level, PermissionDenied, assert_allowed
from ...core.storage import get_storage
from ...engine.industry_matrix import domain_template_key
from ...engine.stage_gate import GATE_APPROVERS, GATE_CONDITION_KEYS, STAGES, pass_gate
from ...engine.state_machines import CORRECTION, PROJECT, WORKSHEET, IllegalTransition
from ...models.audit import AIRun as AIRunORM
from ...models.confirmation import Confirmation
from ...models.finding import AuditReport, Finding
from ...models.knowledge import RuleAnchor
from ...models.project import AuditProject, Client, StageGate
from ...models.quality import CORRECTION_OPEN_STATUSES, ArchiveCorrection
from ...models.risk import AuditProgram, RiskItem
from ...models.user import User
from ...models.worksheet import Worksheet, WorksheetReview, WorksheetVersion
from . import render as qc_render

# QU-1xxx 质控域错误码
CODE_GATE = "QU-1001"
CODE_NOT_FOUND = "QU-1002"
CODE_LOCKED = "QU-1006"
CODE_FROZEN = "QU-1005"
CODE_PARAM = "QU-1007"
CODE_ARCHIVE_BLOCKED = "QU-1008"
CODE_TAMPER = "QU-1009"  # 归档实包完整性校验失败（防篡改）
CODE_CORRECTION = "QU-1010"  # 差错归档流程违规（§15-16）

QC_CHECK_VERSION = "qc-check@1.0.0"  # Skill 版本占位（零依赖纯规则实现）
QC_MODEL_VERSION = "qc-rules@1.0.0"
PASS_STATUSES = ("复核通过", "终审通过")  # §4.3：复核通过前底稿不得进入归档包
# 归档校验认可口径：已归档底稿必然经过复核（重复校验/已归档项目场景）
ARCHIVABLE_WS_STATUSES = ("复核通过", "终审通过", "已归档")
# 手动过门仅开放已落位域的责任门；承接门随立项审批自动串联，其余门待域落位后串联
# （报告门随 FR-05-04 报告签发串联；归档门随 FR-06-04 归档动作自动通过）
GATE_READY_STAGES = ("实质性", "完成", "报告")
_MAX_ARTIFACT_BYTES = 20 * 1024 * 1024  # 差错修订产物上传上限（底稿 Excel 写回产物量级）


class NotFound(Exception):
    pass


class Locked(Exception):
    pass


class ProjectFrozen(Exception):
    pass


class TransitionRejected(Exception):
    pass


class GateRejected(Exception):
    """门流转被拒（engine 准入条件未满足 / 未开放手动过门）。"""


class ArchiveBlocked(Exception):
    """归档完整性校验未通过（阻断清单随异常携带）。"""

    def __init__(self, msg: str, blockers: list[dict[str, Any]]) -> None:
        super().__init__(msg)
        self.blockers = blockers


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _assert_writable(project: AuditProject) -> None:
    if project.status == "暂停":
        raise ProjectFrozen(CODE_FROZEN)
    if project.status in ("已归档", "终止"):  # 终结态：阶段门冻结、禁止一切写操作（§15-17）
        raise Locked(f"{CODE_LOCKED} 项目已终结（{project.status}），禁止写操作")


def _get_project(db: Session, project_id: str) -> AuditProject:
    row = db.get(AuditProject, project_id)
    if row is None:
        raise NotFound(CODE_NOT_FOUND)
    return row


def _get_gate(db: Session, project_id: str, stage: str) -> StageGate:
    gate = db.scalar(
        select(StageGate).where(StageGate.project_id == project_id, StageGate.stage == stage)
    )
    if gate is None:
        raise NotFound(f"{CODE_NOT_FOUND} 阶段门未初始化：{stage}（项目须经立项审批建档）")
    return gate


def _unadjudicated_count(db: Session, project_id: str) -> int:
    """升级未裁决的分歧数（FR-05-05；与 services/reports 同口径：escalated 且无裁决引用）。"""
    return int(db.scalar(
        select(func.count(Finding.id)).where(
            Finding.project_id == project_id,
            Finding.escalated.is_(True),
            Finding.escalation_ref.is_(None),
        )
    ) or 0)


# ---------------------------------------------------------------------------
# FR-06-01 质控 AI 检查：底稿完整性 / 复核点清单 / 交叉索引（R7）/ 程序执行缺口
# ---------------------------------------------------------------------------
def _collect_qc_items(
    db: Session, project_id: str
) -> tuple[dict[str, bool], list[dict[str, str]], int]:
    """检查项收集与判定（纯 Python 规则，同批数据可复算）。返回（检查结果, 未通过项, 底稿数）。"""
    worksheets = db.scalars(
        select(Worksheet).where(Worksheet.project_id == project_id)
    ).all()
    codes = {w.code for w in worksheets}
    checks: dict[str, bool] = {}
    failed: list[dict[str, str]] = []

    # ① 底稿完整性：全部底稿双签复核通过（复核通过前不得进入归档包，§4.3）
    unreviewed = [w for w in worksheets if w.status not in PASS_STATUSES]
    checks["worksheets_reviewed"] = bool(worksheets) and not unreviewed
    for w in unreviewed:
        failed.append({
            "item": "底稿完整性", "target": w.code,
            "detail": f"状态「{w.status}」未通过复核（§4.3 复核通过前不得进入归档包）",
        })

    # ② 复核点清单：重要底稿必须合伙人终审（§15-11 重要底稿判定清单）
    not_final = [w for w in worksheets if w.is_important and w.status != "终审通过"]
    checks["important_worksheets_finalized"] = not not_final
    for w in not_final:
        failed.append({"item": "复核点清单", "target": w.code, "detail": "重要底稿未终审（§15-11）"})

    # ③ 交叉索引校验（R7）：引用底稿编号必须真实存在（断链清单）
    for w in worksheets:
        for ref in (w.cross_refs or []):
            ref_code = ref.get("code") if isinstance(ref, dict) else str(ref)
            if ref_code not in codes:
                failed.append({
                    "item": "交叉索引校验", "target": w.code,
                    "detail": f"引用底稿编号「{ref_code}」不存在（断链，R7）",
                })
    checks["cross_refs_ok"] = not any(f["item"] == "交叉索引校验" for f in failed)

    # ④ 程序执行缺口：程序包全部已完成（项目无程序包视为无缺口，程序域落位后随门 5 串联）
    program_rows = db.scalars(
        select(AuditProgram)
        .join(RiskItem, AuditProgram.risk_id == RiskItem.id)
        .where(RiskItem.project_id == project_id)
    ).all()
    pending = [p for p in program_rows if p.exec_status != "已完成"]
    checks["programs_executed"] = not pending
    for p in pending:
        failed.append({
            "item": "程序执行缺口", "target": p.program_code,
            "detail": f"程序包「{p.exec_status}」未完成",
        })

    return checks, failed, len(worksheets)


def run_qc_check(db: Session, operator: User, project_id: str) -> tuple[AIRunORM, dict[str, Any]]:
    """发起质控 AI 检查（FR-06-01，archive.access ≥ OPERATE）：产物为线索态（module=ai_qc）。

    检查报告未通过项用于阻断阶段门（门 6）；报告本身经三段式采信后方可作为准入依据。
    """
    assert_allowed(operator.global_role, "archive.access", Level.OPERATE)
    project = _get_project(db, project_id)
    _assert_writable(project)

    checks, failed, ws_total = _collect_qc_items(db, project_id)
    run = pipeline.AIRun(
        project_id=project_id, module="ai_qc",
        skill_version=QC_CHECK_VERSION, model_version=QC_MODEL_VERSION,
        triggered_by=operator.id, input_material_ids=[],
    )
    pipeline.record_input(run, f"worksheets:{project_id}:{ws_total}", str(ws_total).encode("utf-8"))
    output = {"checks": checks, "failed_items": failed, "passed": not failed}
    output_key = f"projects/{project_id}/{run.run_no}.json"
    pipeline.seal_output(run, output, output_key)

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
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="ai_run", entity_id=orm.id,
        action="质控 AI 检查（线索态）", operator=operator.name,
        after={"checks": checks, "failed_count": len(failed), "passed": not failed},
    )
    return orm, output


def apply_qc_approved(db: Session, operator: User, run_row: AIRunORM) -> None:
    """采信回调（worksheets.mark_ai_review 分派）：检查报告冻结为门 6 准入依据（留痕）。"""
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="ai_run", entity_id=run_row.id,
        action="质控检查报告采信", operator=operator.name,
        after={"run_no": run_row.run_no, "output_digest": run_row.output_digest},
    )


def list_qc_checks(db: Session, project_id: str) -> list[AIRunORM]:
    """质控检查留痕清单（线索态可见，复核标记状态透明）。"""
    return list(db.scalars(
        select(AIRunORM)
        .where(AIRunORM.project_id == project_id, AIRunORM.module == "ai_qc")
        .order_by(AIRunORM.triggered_at)
    ).all())


# ---------------------------------------------------------------------------
# FR-06-03 阶段门卡控：条件收集 → engine.pass_gate 单点裁决；跳过须 R3 双批
# ---------------------------------------------------------------------------
def collect_gate_conditions(db: Session, project_id: str, stage: str) -> dict[str, bool]:
    """从 DB 收集门准入条件快照（engine.pass_gate 单点裁决的输入，快照随门落库）。"""
    conditions = {k: False for k in GATE_CONDITION_KEYS[stage]}
    if stage == "实质性":
        cf_rows = db.scalars(
            select(Confirmation.status).where(Confirmation.project_id == project_id)
        ).all()
        conditions["confirmations_closed"] = all(s == "闭环" for s in cf_rows)
        program_rows = db.scalars(
            select(AuditProgram.exec_status)
            .join(RiskItem, AuditProgram.risk_id == RiskItem.id)
            .where(RiskItem.project_id == project_id)
        ).all()
        conditions["programs_executed"] = all(s == "已完成" for s in program_rows)
    elif stage == "完成":
        finding_rows = db.scalars(
            select(Finding).where(Finding.project_id == project_id)
        ).all()
        conditions["findings_closed_or_escalated"] = all(
            f.status in ("已关闭", "已排除") or (f.escalated and bool(f.escalation_ref))
            for f in finding_rows
        )
        ws_rows = db.scalars(
            select(Worksheet.status).where(Worksheet.project_id == project_id)
        ).all()
        conditions["worksheet_review_passed"] = bool(ws_rows) and all(s in PASS_STATUSES for s in ws_rows)
        # qc_check_passed：质控检查已采信（第三段标记）且当前重算无未通过项（FR-06-01）
        qc_marked = db.scalar(
            select(AIRunORM.id).where(
                AIRunORM.project_id == project_id, AIRunORM.module == "ai_qc",
                AIRunORM.reviewed_by.is_not(None),
            )
        )
        _, failed, _ = _collect_qc_items(db, project_id)
        conditions["qc_check_passed"] = qc_marked is not None and not failed
    elif stage == "报告":
        # report_signed：存在已签发报告（FR-05-04）；escrow_opinion_resolved：无升级未裁决分歧（FR-05-05）
        signed = db.scalar(
            select(AuditReport.id).where(
                AuditReport.project_id == project_id, AuditReport.status == "已签发"
            )
        )
        conditions["report_signed"] = signed is not None
        conditions["escrow_opinion_resolved"] = not _unadjudicated_count(db, project_id)
    return conditions


def pass_stage_gate(db: Session, operator: User, project_id: str, stage: str) -> StageGate:
    """阶段门通过（FR-06-03）：engine 门权限卡控 + 准入条件裁决；暂停期间冻结。"""
    if stage not in STAGES:
        raise ValueError(f"{CODE_PARAM} 非法阶段：{stage}（合法值：{STAGES}）")
    permission, level = GATE_APPROVERS[stage]
    assert_allowed(operator.global_role, permission, Level(level))
    project = _get_project(db, project_id)
    _assert_writable(project)
    gate = _get_gate(db, project_id, stage)

    if stage not in GATE_READY_STAGES:
        raise GateRejected(
            f"{CODE_GATE} 阶段门「{stage}」由对应业务域审批流自动串联"
            "（承接=立项审批；归档随 FR-06-04 归档动作；其余待域落位），不开放手动过门"
        )
    conditions = collect_gate_conditions(db, project_id, stage)
    try:
        gate.gate_status = pass_gate(stage, conditions)
    except ValueError as exc:
        raise GateRejected(f"{CODE_GATE} {exc}") from exc
    gate.conditions_snapshot = conditions
    gate.passed_by = operator.id
    gate.passed_at = _now()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="stage_gate", entity_id=gate.id,
        action=f"阶段门通过-{stage}", operator=operator.name,
        before={"gate_status": "未开始"}, after={"gate_status": "已通过", "conditions": conditions},
    )
    return gate


def skip_stage_gate(
    db: Session, operator: User, project_id: str, stage: str, *, qc_reviewer_id: str
) -> StageGate:
    """门跳过（FR-06-03 + §7 R3）：合伙人批准 + 质控专员双批，双批人落库留痕。"""
    assert_allowed(operator.global_role, "project.approve", Level.APPROVE)  # 合伙人专属
    if stage not in STAGES:
        raise ValueError(f"{CODE_PARAM} 非法阶段：{stage}（合法值：{STAGES}）")
    _assert_writable(_get_project(db, project_id))
    qc = db.get(User, qc_reviewer_id or "")
    if qc is None or qc.global_role != "质控专员":
        raise ValueError(f"{CODE_PARAM} 跳过须质控专员会批（R3 双批）：批注人缺失或角色不符")
    gate = _get_gate(db, project_id, stage)
    if gate.gate_status not in ("未开始", "进行中"):
        raise GateRejected(f"{CODE_GATE} 门状态「{gate.gate_status}」不可跳过")
    gate.gate_status = pass_gate(stage, {}, skip=True)  # engine：skip 固定「跳过」
    gate.skip_partner_id = operator.id
    gate.skip_qc_id = qc.id
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="stage_gate", entity_id=gate.id,
        action=f"阶段门跳过-{stage}（双批）", operator=operator.name,
        after={"gate_status": "跳过", "skip_partner": operator.name, "skip_qc": qc.name},
    )
    return gate


def list_gates(db: Session, project_id: str) -> list[StageGate]:
    """八门状态清单（全程可见，FR-06-03 验收口径）。"""
    return list(db.scalars(
        select(StageGate).where(StageGate.project_id == project_id)
    ).all())


def list_archive_projects(db: Session, operator: User) -> list[AuditProject]:
    """质控驾驶舱项目清单（archive.access ≥ VIEW，含已归档/终止——归档调阅场景）。"""
    assert_allowed(operator.global_role, "archive.access", Level.VIEW)
    return list(db.scalars(select(AuditProject).order_by(AuditProject.created_at.desc())).all())


# ---------------------------------------------------------------------------
# FR-06-02 / FR-06-04 归档完整性校验与一键归档
# ---------------------------------------------------------------------------
def precheck_archive(db: Session, project_id: str) -> dict[str, Any]:
    """归档前完整性校验（FR-06-02 归档自动执行 + FR-06-04 前置），只读不转态。

    阻断项：缺人工复核标记的 AI 留痕（红线 1/R2）、哈希链断链（硬约束 4）、
    底稿未复核/重要底稿未终审（§4.3、§15-11）、报告未签发（FR-06-04 前置：报告门通过后）。
    """
    if db.get(AuditProject, project_id) is None:
        raise NotFound(CODE_NOT_FOUND)
    blockers: list[dict[str, Any]] = []

    # ① AI 留痕核验：未过第三段复核标记的输出不得归档（红线 1/R2，§5.6 FR-06-02）
    lead_runs = db.scalars(
        select(AIRunORM).where(
            AIRunORM.project_id == project_id, AIRunORM.reviewed_by.is_(None)
        )
    ).all()
    if lead_runs:
        blockers.append({
            "code": "ai_lead_unreviewed",
            "detail": "存在未人工复核标记的 AI 留痕（红线 1/R2），不得归档",
            "run_nos": [r.run_no for r in lead_runs],
        })

    # ② 留痕哈希链整链验证（硬约束 4：任何篡改断链即阻断）
    if not verify_chain(db):
        blockers.append({"code": "trail_chain_broken", "detail": "留痕哈希链整链验证失败（疑似篡改，出证前置）"})

    # ③ 底稿复核状态：复核通过前不得进入归档包（§4.3）；重要底稿须终审（§15-11）
    ws_rows = db.scalars(select(Worksheet).where(Worksheet.project_id == project_id)).all()
    if not ws_rows:
        blockers.append({"code": "no_worksheets", "detail": "项目无底稿，归档包为空"})
    for w in ws_rows:
        if w.status not in ARCHIVABLE_WS_STATUSES:
            blockers.append({
                "code": "worksheet_not_reviewed",
                "detail": f"底稿 {w.code} 状态「{w.status}」未复核通过（§4.3）",
            })
        elif w.is_important and w.status not in ("终审通过", "已归档"):
            blockers.append({
                "code": "important_not_final",
                "detail": f"重要底稿 {w.code} 未合伙人终审（§15-11）",
            })

    # ④ 报告已签发（FR-06-04 触发前置：报告门通过后质控发起归档）
    signed = db.scalar(
        select(AuditReport.id).where(
            AuditReport.project_id == project_id, AuditReport.status == "已签发"
        )
    )
    if signed is None:
        blockers.append({"code": "report_not_signed", "detail": "审计报告未签发（报告门未通过）"})

    return {"blocked": bool(blockers), "blockers": blockers}


def _build_archive_package(
    project: AuditProject,
    worksheets: list[Worksheet],
    runs: list[AIRunORM],
    signed: AuditReport | None,
    *,
    extra_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """归档实包构建（FR-06-04 首封 / §15-16 重新归档共用，TECH_DESIGN §4.3.4）。

    成员=底稿/报告产物+AI 留痕 JSON+manifest.json（含 package_hash 自证+missing_objects）
    +SHA256SUMS（成员级防篡改验证依据；zip 字节含时间戳不稳定，不作为登记口径）。
    键含封包哈希（WORM 语义：不同封包永不覆盖同键；Phase 1 本地存储，MinIO 接线点）。
    历史登记键/无产物进 missing_objects 留痕，不阻断（缺失可审计）。
    """
    storage = get_storage()
    members: dict[str, bytes] = {}
    missing: list[str] = []
    for w in sorted(worksheets, key=lambda x: x.code):
        if w.file_key and storage.exists(w.file_key):
            members[f"worksheets/{w.code}/{w.current_version}.xlsx"] = storage.load_bytes(w.file_key)
        else:
            missing.append(f"worksheet:{w.code}:{w.file_key or '-'}")
    for r in sorted(runs, key=lambda x: x.run_no):
        if r.output_key and storage.exists(r.output_key):
            members[f"ai_runs/{r.run_no}.json"] = storage.load_bytes(r.output_key)
        else:
            missing.append(f"ai_run:{r.run_no}")
    if signed and signed.file_key and storage.exists(signed.file_key):
        members[f"reports/{signed.version}.xlsx"] = storage.load_bytes(signed.file_key)
    else:
        missing.append(f"report:{signed.version if signed else '-'}:{signed.file_key if signed else '-'}")

    # 归档清单快照（§5.6 FR-06-04：底稿+复核痕迹+AI 留痕+项目质量档案；
    # 清单哈希确定性可复算，作为 archive_package_hash 出证口径）
    manifest: dict[str, Any] = {
        "project_no": project.project_no,
        "worksheets": sorted(f"{w.code}:{w.current_version}:{w.status}" for w in worksheets),
        "ai_runs": sorted(f"{r.run_no}:{r.output_digest or ''}:{r.status}" for r in runs),
    }
    if extra_manifest:  # 差错重新归档：supersedes + 差错单引用（新旧版本留痕可查）
        manifest.update(extra_manifest)
    package_hash = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    manifest["package_hash"] = package_hash  # 包内自证：与库内 archive_package_hash 同源
    manifest["missing_objects"] = missing
    members["manifest.json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    sums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(members.items())
    )
    members["SHA256SUMS"] = sums.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(members):
            zf.writestr(name, members[name])
    zip_bytes = buf.getvalue()
    zip_sha256 = hashlib.sha256(zip_bytes).hexdigest()
    archive_object_key = f"archives/{project.id}/{package_hash}.zip"
    storage.save_bytes(archive_object_key, zip_bytes)
    # 归档包 WORM 锁（TECH_DESIGN §4.3 红线 1/3：归档不可变）
    # Linux chattr +a；非 Linux 降级 chmod 444；差错修订走副本 v(n+1)
    storage.freeze_archive(archive_object_key)
    return {
        "package_hash": package_hash, "archive_object_key": archive_object_key,
        "zip_sha256": zip_sha256, "zip_members": len(members), "missing_objects": len(missing),
    }


def archive_project(db: Session, operator: User, project_id: str) -> dict[str, Any]:
    """一键归档（FR-06-04，archive.access ≥ OPERATE）：校验 → 封包哈希 → 双状态机收口。

    校验通过后：归档清单（底稿+AI 留痕）快照 sha256 → PROJECT 进行中→已归档（requires
    archive_package_hash）→ 全部底稿 复核通过/终审通过→已归档（requires archived_with_project）
    → 归档门「已通过」。项目终止/暂停状态不得归档（§15-17，engine 一并裁决）。
    """
    assert_allowed(operator.global_role, "archive.access", Level.OPERATE)
    project = _get_project(db, project_id)
    _assert_writable(project)

    check = precheck_archive(db, project_id)
    if check["blocked"]:
        raise ArchiveBlocked(
            f"{CODE_ARCHIVE_BLOCKED} 归档完整性校验未通过，阻断清单如下", check["blockers"]
        )

    worksheets = db.scalars(select(Worksheet).where(Worksheet.project_id == project_id)).all()
    runs = db.scalars(select(AIRunORM).where(AIRunORM.project_id == project_id)).all()
    signed = db.scalar(
        select(AuditReport).where(
            AuditReport.project_id == project_id, AuditReport.status == "已签发"
        )
    )

    # 实包 zip：一次性打包 + 整包哈希（WORM 只读语义，键含封包哈希）
    pack = _build_archive_package(project, list(worksheets), list(runs), signed)
    package_hash = pack["package_hash"]
    archive_object_key = pack["archive_object_key"]
    zip_sha256 = pack["zip_sha256"]

    # 项目状态只走 engine（硬约束 2）：进行中→已归档 requires archive_package_hash
    before = {"status": project.status, "stage": project.current_stage}
    try:
        project.status = PROJECT.transit(project.status, "已归档", archive_package_hash=package_hash)
    except IllegalTransition as exc:
        raise TransitionRejected(f"{CODE_GATE} {exc}") from exc
    project.archive_package_hash = package_hash
    project.current_stage = "归档"

    # 全部底稿随项目归档（engine：复核通过/终审通过→已归档 requires archived_with_project）
    for w in worksheets:
        try:
            w.status = WORKSHEET.transit(w.status, "已归档", archived_with_project=True)
        except IllegalTransition as exc:
            raise TransitionRejected(f"{CODE_GATE} {exc}") from exc

    # 归档门自动通过（门 8：archive_package_sealed）
    gate = _get_gate(db, project_id, "归档")
    gate.gate_status = pass_gate("归档", {"archive_package_sealed": True})
    gate.conditions_snapshot = {"archive_package_sealed": True}
    gate.passed_by = operator.id
    gate.passed_at = _now()

    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_project", entity_id=project.id,
        action="项目归档（FR-06-04）", operator=operator.name,
        before=before,
        after={
            "status": "已归档", "stage": "归档",
            "archive_package_hash": package_hash,
            "archive_object_key": archive_object_key, "zip_sha256": zip_sha256,
            "zip_members": pack["zip_members"], "missing_objects": pack["missing_objects"],
            "archived_worksheets": len(worksheets), "ai_runs": len(runs),
        },
    )

    # FR-07-04 经验回流：项目归档通过后自动触发，经验沉淀提案进入入库审批流（提案态）
    from ..knowledge import service as knowledge_service

    proposals = knowledge_service.seed_experience_proposals(db, operator, project)
    return {
        "project": project, "package_hash": package_hash,
        "archive_object_key": archive_object_key, "zip_sha256": zip_sha256,
        "archived_worksheets": len(worksheets), "ai_runs": len(runs),
        "knowledge_proposals": len(proposals),
    }


# ---------------------------------------------------------------------------
# FR-06-04 归档实包下载与防篡改校验（TECH_DESIGN §4.3.4：归档包只读，出证可验）
# ---------------------------------------------------------------------------
def get_archive_package(db: Session, operator: User, project_id: str) -> tuple[str, bytes]:
    """归档实包字节（下载，archive.access ≥ VIEW「归档调阅」）：未归档 → QU-1006；对象缺失 → QU-1002。"""
    assert_allowed(operator.global_role, "archive.access", Level.VIEW)
    project = _get_project(db, project_id)
    if project.status != "已归档" or not project.archive_package_hash:
        raise Locked(f"{CODE_LOCKED} 项目未归档，无归档包可下载")
    object_key = _archive_object_key(project_id, project.archive_package_hash)
    storage = get_storage()
    if not storage.exists(object_key):
        raise NotFound(f"{CODE_NOT_FOUND} 归档包对象缺失：{object_key}")
    return object_key, storage.load_bytes(object_key)


def _archive_object_key(project_id: str, package_hash: str) -> str:
    return f"archives/{project_id}/{package_hash}.zip"


def verify_archive_package(db: Session, operator: User, project_id: str) -> dict[str, Any]:
    """归档包防篡改校验（archive.access ≥ VIEW），三道核验（QU-1009 失败 → 422）：

    ① 成员集合 == SHA256SUMS 登记集合（防增删成员）；② 逐成员 sha256 比对（防篡改内容）；
    ③ 包内 manifest.package_hash == 库内 archive_package_hash（包库自证一致）。
    """
    assert_allowed(operator.global_role, "archive.access", Level.VIEW)
    project = _get_project(db, project_id)
    if project.status != "已归档" or not project.archive_package_hash:
        raise Locked(f"{CODE_LOCKED} 项目未归档，无归档包可校验")
    object_key, zip_bytes = get_archive_package(db, operator, project_id)
    expected_hash = project.archive_package_hash

    def _tampered(detail: str) -> ValueError:
        return ValueError(f"{CODE_TAMPER} 归档包完整性校验失败：{detail}")

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = set(zf.namelist())
            if not {"SHA256SUMS", "manifest.json"} <= names:
                raise _tampered("归档包缺少完整性清单（SHA256SUMS/manifest.json）")
            sums: dict[str, str] = {}
            for line in zf.read("SHA256SUMS").decode("utf-8").splitlines():
                if line.strip():
                    digest, _, name = line.partition("  ")
                    sums[name] = digest
            if names - {"SHA256SUMS"} != set(sums):
                raise _tampered("包内成员与 SHA256SUMS 清单不一致（存在增删成员）")
            bad = sorted(
                name for name, digest in sums.items()
                if hashlib.sha256(zf.read(name)).hexdigest() != digest
            )
            if bad:
                raise _tampered(f"成员 sha256 与清单不符（疑似篡改）：{bad}")
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            if manifest.get("package_hash") != expected_hash:
                raise _tampered("包内封包哈希与库内登记 archive_package_hash 不一致")
    except zipfile.BadZipFile as exc:
        raise _tampered(f"zip 结构损坏：{exc}") from exc
    return {
        "project_id": project_id, "ok": True, "object_key": object_key,
        "package_hash": expected_hash, "members": len(names),
        "zip_sha256": hashlib.sha256(zip_bytes).hexdigest(),
        "verified_at": _now(),
    }


# ---------------------------------------------------------------------------
# §15-16 归档后差错处理链：质控登记 → 合伙人批准（解锁副本修订，原稿锁定保留）
# → 版本链修订 → 质控复核 → 重新归档 v(n+1)（新旧整包哈希均留痕可查）
# ---------------------------------------------------------------------------
def _get_correction(db: Session, correction_id: str) -> ArchiveCorrection:
    row = db.get(ArchiveCorrection, correction_id)
    if row is None:
        raise NotFound(f"{CODE_NOT_FOUND} 差错单不存在")
    return row


def register_correction(
    db: Session, operator: User, project_id: str, *,
    description: str, affected_worksheet_ids: list[str],
) -> ArchiveCorrection:
    """差错登记（§15-16，质控专员专属）：仅适用已归档项目；受影响底稿须已归档。

    同一项目同一时刻仅允许一张在途差错单（防交织修订同一底稿）。
    """
    assert_allowed(operator.global_role, "archive.access", Level.OPERATE)
    if operator.global_role != QC:
        raise PermissionDenied("差错登记仅质控专员（PRD §15-16）")
    project = _get_project(db, project_id)
    if project.status != "已归档" or not project.archive_package_hash:
        raise Locked(f"{CODE_LOCKED} 差错登记仅适用已归档项目（当前：{project.status}）")
    if not description.strip():
        raise ValueError(f"{CODE_PARAM} 差错描述不可为空")
    affected = sorted({w for w in affected_worksheet_ids if w})
    if not affected:
        raise ValueError(f"{CODE_PARAM} 受影响底稿清单不可为空（解锁副本修订范围须明确）")
    open_row = db.scalar(
        select(ArchiveCorrection.id).where(
            ArchiveCorrection.project_id == project_id,
            ArchiveCorrection.status.in_(CORRECTION_OPEN_STATUSES),
        )
    )
    if open_row is not None:
        raise Locked(f"{CODE_CORRECTION} 存在在途差错单，须办结后方可再登记（防交织修订）")
    worksheets: list[Worksheet] = []
    for wid in affected:
        w = db.get(Worksheet, wid)
        if w is None or w.project_id != project_id:
            raise NotFound(f"{CODE_NOT_FOUND} 受影响底稿不存在或不属于本项目：{wid}")
        if w.status != "已归档":
            raise ValueError(
                f"{CODE_PARAM} 底稿 {w.code} 状态「{w.status}」非已归档，不在差错修订范围"
            )
        worksheets.append(w)

    seq = db.scalar(
        select(func.count(ArchiveCorrection.id)).where(ArchiveCorrection.project_id == project_id)
    )
    row = ArchiveCorrection(
        project_id=project_id, code=f"{project.project_no}-COR{(seq or 0) + 1:03d}",
        description=description.strip(), affected_worksheet_ids=affected,
        status="待批准", registered_by=operator.id,
    )
    db.add(row)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="archive_correction", entity_id=row.id,
        action="差错登记（§15-16）", operator=operator.name,
        after={"code": row.code, "status": row.status,
               "affected": [w.code for w in worksheets], "description": row.description},
    )
    return row


def list_corrections(db: Session, operator: User, project_id: str) -> list[ArchiveCorrection]:
    """差错单清单（archive.access ≥ VIEW，新旧包哈希随单留痕可查）。"""
    assert_allowed(operator.global_role, "archive.access", Level.VIEW)
    _get_project(db, project_id)
    return list(db.scalars(
        select(ArchiveCorrection)
        .where(ArchiveCorrection.project_id == project_id)
        .order_by(ArchiveCorrection.created_at)
    ).all())


def approve_correction(
    db: Session, operator: User, correction_id: str, *, approve: bool, reason: str | None = None
) -> ArchiveCorrection:
    """合伙人批准/驳回（§15-16，project.approve ≥ APPROVE 合伙人专属）。

    批准即解锁受影响底稿副本修订（版本链追加，原稿与 v(n) 包锁定保留）；
    批准时点记版本计数基线，作为提交质控复核的逐稿凭据。驳回必附原因。
    """
    assert_allowed(operator.global_role, "project.approve", Level.APPROVE)
    row = _get_correction(db, correction_id)
    try:
        if approve:
            row.status = CORRECTION.transit(row.status, "修订中", approved_by_partner=operator.id)
        else:
            if not (reason or "").strip():
                raise ValueError(f"{CODE_PARAM} 驳回必附原因（§15-16 留痕口径）")
            row.status = CORRECTION.transit(
                row.status, "已驳回", approved_by_partner=operator.id, reason=reason.strip()
            )
    except IllegalTransition as exc:
        raise TransitionRejected(f"{CODE_CORRECTION} {exc}") from exc
    row.approved_by = operator.id
    row.approved_at = _now()
    if approve:
        row.revision_baseline = {
            wid: int(db.scalar(
                select(func.count(WorksheetVersion.id)).where(WorksheetVersion.worksheet_id == wid)
            ) or 0)
            for wid in row.affected_worksheet_ids
        }
        action = "差错批准-解锁受影响底稿副本修订（原稿锁定保留）"
        after: dict[str, Any] = {"status": row.status, "revision_baseline": row.revision_baseline}
    else:
        row.rejection_reason = reason.strip()
        action = "差错驳回"
        after = {"status": row.status, "rejection_reason": row.rejection_reason}
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="archive_correction", entity_id=row.id,
        action=action, operator=operator.name, after=after,
    )
    return row


def upload_correction_artifact(
    db: Session, operator: User, correction_id: str, *,
    worksheet_id: str, filename: str, content: bytes,
) -> dict[str, Any]:
    """修订产物受控上传（§15-16，worksheet.edit ≥ OPERATE）：直传对象存储，凭 file_key 登记版本链。

    权限与登记同源；仅差错单「修订中」且底稿在受影响清单内可传；产物限模板保真写回 xlsx。
    键 = corrections/{差错单号}/{底稿id}/{sha256}.xlsx：内容寻址（同内容同键幂等、
    异内容异键永不覆盖，WORM 语义同归档包），大小上限 20MB（底稿写回产物量级）。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_correction(db, correction_id)
    if row.status != "修订中":
        raise Locked(f"{CODE_CORRECTION} 差错单状态「{row.status}」不可上传修订产物（须合伙人批准解锁后）")
    if worksheet_id not in (row.affected_worksheet_ids or []):
        raise ValueError(f"{CODE_PARAM} 底稿不在差错单受影响清单内：{worksheet_id}")
    ws = db.get(Worksheet, worksheet_id)
    if ws is None:
        raise NotFound(f"{CODE_NOT_FOUND} 底稿不存在：{worksheet_id}")
    if not filename.lower().endswith(".xlsx"):
        raise ValueError(f"{CODE_PARAM} 修订产物须为模板保真写回 xlsx：{filename}")
    if not content:
        raise ValueError(f"{CODE_PARAM} 修订产物内容为空")
    if len(content) > _MAX_ARTIFACT_BYTES:
        raise ValueError(f"{CODE_PARAM} 修订产物超限（>{_MAX_ARTIFACT_BYTES // (1024 * 1024)}MB）")

    digest = hashlib.sha256(content).hexdigest()
    file_key = f"corrections/{row.code}/{worksheet_id}/{digest}.xlsx"
    get_storage().save_bytes(file_key, content)
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="archive_correction", entity_id=row.id,
        action="差错修订产物上传（对象存储落存，内容寻址键）", operator=operator.name,
        after={"worksheet_code": ws.code, "file_key": file_key,
               "sha256": digest, "size": len(content)},
    )
    return {"file_key": file_key, "size": len(content), "sha256": digest}


def register_correction_revision(
    db: Session, operator: User, correction_id: str, *,
    worksheet_id: str, file_key: str, change_summary: str,
) -> WorksheetVersion:
    """副本修订登记（worksheet.edit ≥ OPERATE）：版本链追加 v(n+1)，原稿版本锁定保留。

    修订产物对象须已就位（登记制同材料口径）；仅差错单「修订中」且底稿在受影响清单内可登记。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_correction(db, correction_id)
    if row.status != "修订中":
        raise Locked(f"{CODE_CORRECTION} 差错单状态「{row.status}」不可登记修订（须合伙人批准解锁后）")
    if worksheet_id not in (row.affected_worksheet_ids or []):
        raise ValueError(f"{CODE_PARAM} 底稿不在差错单受影响清单内：{worksheet_id}")
    ws = db.get(Worksheet, worksheet_id)
    if ws is None:
        raise NotFound(f"{CODE_NOT_FOUND} 底稿不存在：{worksheet_id}")
    if not file_key or not get_storage().exists(file_key):
        raise ValueError(f"{CODE_PARAM} 修订产物对象缺失：{file_key or '-'}（先上传再登记）")
    if not change_summary.strip():
        raise ValueError(f"{CODE_PARAM} 变更摘要不可为空（版本链留痕口径）")

    from ..worksheets import service as worksheets_service  # 版本链单点口径复用（硬约束 9）

    ver = worksheets_service.append_version(
        db, operator, worksheet_id,
        file_key=file_key, summary=f"[{row.code} 差错修订] {change_summary.strip()}",
    )
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="archive_correction", entity_id=row.id,
        action="差错副本修订登记（版本链追加，原稿锁定保留）", operator=operator.name,
        after={"worksheet_code": ws.code, "version_no": ver.version_no, "file_key": file_key},
    )
    return ver


def submit_correction_review(
    db: Session, operator: User, correction_id: str
) -> ArchiveCorrection:
    """提交质控复核（archive.access ≥ OPERATE）：受影响底稿逐稿须有批准后的新版本。"""
    assert_allowed(operator.global_role, "archive.access", Level.OPERATE)
    row = _get_correction(db, correction_id)
    baseline = row.revision_baseline or {}
    unrevised = []
    for wid in row.affected_worksheet_ids or []:
        n = int(db.scalar(
            select(func.count(WorksheetVersion.id)).where(WorksheetVersion.worksheet_id == wid)
        ) or 0)
        if n <= int(baseline.get(wid, 0)):
            ws = db.get(Worksheet, wid)
            unrevised.append(ws.code if ws else wid)
    if unrevised:
        raise ValueError(f"{CODE_PARAM} 尚有受影响底稿未登记差错修订版本：{unrevised}")
    try:
        row.status = CORRECTION.transit(
            row.status, "待质控复核", submitted_by=operator.id, revision_registered=True
        )
    except IllegalTransition as exc:
        raise TransitionRejected(f"{CODE_CORRECTION} {exc}") from exc
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="archive_correction", entity_id=row.id,
        action="差错修订提交质控复核", operator=operator.name, after={"status": row.status},
    )
    return row


def qc_review_correction(
    db: Session, operator: User, correction_id: str, *,
    conclusion: str, opinion: str | None = None,
) -> ArchiveCorrection:
    """质控复核（§15-16，质控专员专属，PRD §3 worksheet.review_sign 原文「质控复核」）。

    通过 → 复核通过（可重新归档）；退回 → 修订中（必附意见）。
    """
    assert_allowed(operator.global_role, "worksheet.review_sign", Level.OPERATE)
    if operator.global_role != QC:
        raise PermissionDenied("差错修订质控复核仅质控专员（PRD §15-16）")
    row = _get_correction(db, correction_id)
    try:
        if conclusion == "通过":
            row.status = CORRECTION.transit(row.status, "复核通过", qc_reviewer=operator.id)
        elif conclusion == "退回":
            if not (opinion or "").strip():
                raise ValueError(f"{CODE_PARAM} 质控退回必附意见")
            row.status = CORRECTION.transit(
                row.status, "修订中", qc_reviewer=operator.id, opinion=opinion.strip()
            )
        else:
            raise ValueError(f"{CODE_PARAM} 非法复核结论：{conclusion}（通过/退回）")
    except IllegalTransition as exc:
        raise TransitionRejected(f"{CODE_CORRECTION} {exc}") from exc
    row.qc_reviewed_by = operator.id
    row.qc_reviewed_at = _now()
    row.qc_opinion = (opinion or "").strip() or None
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="archive_correction", entity_id=row.id,
        action=f"差错质控复核-{conclusion}", operator=operator.name,
        after={"status": row.status, "opinion": row.qc_opinion},
    )
    return row


def rearchive_project(db: Session, operator: User, correction_id: str) -> dict[str, Any]:
    """重新归档 v(n+1)（§15-16，archive.access ≥ OPERATE）：复核通过 → 新实包 → 哈希指向新包。

    manifest 增 supersedes（v(n) 包哈希）+ 差错单引用；包库自证同源。旧包键含旧哈希
    不动（WORM），项目 archive_package_hash 指向新包，双哈希随差错单与留痕可查。
    """
    assert_allowed(operator.global_role, "archive.access", Level.OPERATE)
    row = _get_correction(db, correction_id)
    project = _get_project(db, row.project_id)
    if project.status != "已归档" or not project.archive_package_hash:
        raise Locked(f"{CODE_LOCKED} 项目未归档，不可重新归档（当前：{project.status}）")
    if not CORRECTION.can(row.status, "已重新归档"):  # 先裁决后打包：失败不落孤儿对象
        raise TransitionRejected(
            f"{CODE_CORRECTION} [差错单] 不允许 {row.status} -> 已重新归档（须质控复核通过）"
        )

    worksheets = db.scalars(select(Worksheet).where(Worksheet.project_id == project.id)).all()
    runs = db.scalars(select(AIRunORM).where(AIRunORM.project_id == project.id)).all()
    signed = db.scalar(
        select(AuditReport).where(
            AuditReport.project_id == project.id, AuditReport.status == "已签发"
        )
    )
    code_map = {w.id: w.code for w in worksheets}
    previous_hash = project.archive_package_hash
    pack = _build_archive_package(
        project, list(worksheets), list(runs), signed,
        extra_manifest={
            "supersedes": previous_hash,
            "correction": {
                "code": row.code, "description": row.description,
                "affected": [code_map.get(wid, wid) for wid in row.affected_worksheet_ids],
            },
        },
    )
    package_hash = pack["package_hash"]
    row.status = CORRECTION.transit(
        row.status, "已重新归档", rearchive_package_hash=package_hash
    )
    row.previous_package_hash = previous_hash
    row.rearchive_package_hash = package_hash
    row.rearchived_at = _now()
    project.archive_package_hash = package_hash  # 状态不变（已归档），出证哈希指向最新包
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_project", entity_id=project.id,
        action="差错重新归档 v(n+1)（§15-16）", operator=operator.name,
        before={"archive_package_hash": previous_hash},
        after={
            "archive_package_hash": package_hash, "supersedes": previous_hash,
            "correction": row.code, "archive_object_key": pack["archive_object_key"],
            "zip_sha256": pack["zip_sha256"], "zip_members": pack["zip_members"],
            "missing_objects": pack["missing_objects"],
        },
    )
    return {"correction": row, "project": project, "previous_package_hash": previous_hash, **pack}


# ---------------------------------------------------------------------------
# FR-06-05 检查应对：准则锚点反查 + 质控报表 + 应对材料包（质控按需发起，出证可复算）
# ---------------------------------------------------------------------------
def build_inspection_pack(db: Session, operator: User, project_id: str) -> dict[str, Any]:
    """检查应对材料包（FR-06-05，archive.access ≥ OPERATE）：质控按需发起，一键出包。

    ① 锚点反查表（红线 2：底稿必带准则锚点，库端 100% 可反查「这条程序依据哪条准则」）；
    ② 质控报表：四项检查同批重算（可复算出证）+ AI 留痕核对统计（不采信按 §15-16
       原因分类）+ 复核痕迹统计（全部轮次，FR-05-02）+ 八门状态；
    ③ 封包 sha256（与归档包同款口径）：包内容不含时间戳等易变项，同批数据重生成
       digest 恒定（出证完整性）；生成动作 append_trail 续链。
    """
    assert_allowed(operator.global_role, "archive.access", Level.OPERATE)
    project = _get_project(db, project_id)

    # ① 锚点反查表：底稿 → 准则锚点（程序执行以底稿为承载，红线 2 强制非空）
    anchor_map: list[dict[str, str]] = []
    ws_rows = db.scalars(
        select(Worksheet).where(Worksheet.project_id == project_id)
    ).all()
    for w in sorted(ws_rows, key=lambda x: x.code):
        anchor = db.get(RuleAnchor, w.rule_anchor_id)
        anchor_map.append({
            "worksheet_code": w.code, "title": w.title, "status": w.status,
            "standard_no": anchor.standard_no if anchor else "",
            "clause": anchor.clause if anchor else "",
            "purpose": anchor.purpose if anchor else "",
        })

    # ② 质控报表（与 FR-06-01 同一检查器，同批可复算）
    checks, failed, _ = _collect_qc_items(db, project_id)
    runs = db.scalars(
        select(AIRunORM).where(AIRunORM.project_id == project_id)
    ).all()
    rejected = Counter(
        f"{r.module}：{r.rejection_reason}" for r in runs if r.status == "不采信"
    )
    review_rows = db.scalars(
        select(WorksheetReview)
        .join(Worksheet, WorksheetReview.worksheet_id == Worksheet.id)
        .where(Worksheet.project_id == project_id)
    ).all()
    report = {
        "qc_checks": checks,
        "qc_failed_items": failed,
        "ai_runs": {
            "total": len(runs),
            "reviewed": sum(1 for r in runs if r.reviewed_by is not None),
            "unreviewed": [r.run_no for r in runs if r.reviewed_by is None],
            "rejected_by_reason": dict(sorted(rejected.items())),  # §15-16 脱敏口径
        },
        "review_rounds": len(review_rows),
        "review_returns": sum(1 for r in review_rows if r.conclusion == "退回"),
        "gates": {g.stage: g.gate_status for g in list_gates(db, project_id)},
    }

    # ③ 封包：内容确定（无易变项）→ 同批数据重生成 digest 恒定（出证可复算）
    pack = {
        "project_no": project.project_no,
        "anchor_map": anchor_map,
        "report": report,
        "generated_by": operator.name,
    }
    package_digest = hashlib.sha256(
        json.dumps(pack, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="inspection_pack", entity_id=project_id,
        action="检查应对材料包生成（FR-06-05）", operator=operator.name,
        after={"package_digest": package_digest, "anchor_map_rows": len(anchor_map)},
    )
    return {
        "project_id": project_id, "project_no": project.project_no,
        "anchor_map": anchor_map, "report": report,
        "package_digest": package_digest, "generated_at": _now(),
    }


def export_checklist(db: Session, operator: User, project_id: str) -> tuple[str, str]:
    """完成情况核对表出稿（FR-06-01，archive.access ≥ OPERATE）：6600 模板保真写回。

    质控检查报告的归档载体：仅填核对表表头要素（被审计单位/编制=质控专员/日期），
    检查结论勾选区留人工核对。模板键按项目业务类型准则路由（小企业准则 →
    checklist-sme 独立键，engine 单点）。QU-1020 指纹/登记类拒绝（ValueError 子类）
    向上 → 路由 422，落盘对象已删，天然回滚。
    """
    assert_allowed(operator.global_role, "archive.access", Level.OPERATE)
    project = _get_project(db, project_id)
    _assert_writable(project)
    client = db.get(Client, project.client_id)
    if client is None:
        raise NotFound(f"{CODE_NOT_FOUND} 项目客户缺失")

    fields = qc_render.build_checklist_fields(
        auditee=client.name, preparer=operator.name,
        prepared_at=_now().date().isoformat(),
    )
    template_key = domain_template_key("checklist", project.business_type)
    object_key = f"quality/{project_id}/checklist-6600.xlsx"
    qc_render.render_checklist(
        fields, template_key, storage=get_storage(), object_key=object_key,
    )
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_project", entity_id=project_id,
        action="完成情况核对表出稿", operator=operator.name,
        after={"template_key": template_key, "file_key": object_key},
    )
    return template_key, object_key
