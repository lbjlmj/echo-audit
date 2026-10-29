"""计划域业务逻辑（FR-02-01 重要性水平 R8 / FR-02-03 风险评级确认；PRD §5 FR-02）。

规则落点：
- 重要性计算一律经 engine.compute_materiality（R8 单点，硬约束 9）；项目经理可改，
  修改全量留痕（change_log）并重置待审批，随计划报合伙人审批确认；
- 风险确认后锁定（FR-02-03），程序包类型由 engine.select_package 裁决（R5 单点）；
- 项目「暂停」期间拒绝一切写操作（§15-17）；留痕一律经 core.append_trail 续链（硬约束 4/9）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...ai import pipeline
from ...core.audit_middleware import append_trail
from ...core.permissions import Level, assert_allowed
from ...core.storage import get_storage
from ...engine.industry_matrix import domain_template_key
from ...engine.materiality import MaterialityError, compute_materiality
from ...engine.risk_programs import select_package
from ...models.audit import AIRun as AIRunORM
from ...models.knowledge import RuleAnchor
from ...models.material import VoucherLine
from ...models.project import AuditProject, Client, StageGate
from ...models.risk import AuditProgram, Materiality, RiskItem
from ...models.user import User
from . import render as plan_render

# PL-1xxx 计划域错误码
CODE_NOT_FOUND = "PL-1002"
CODE_LOCKED = "PL-1006"  # 已锁定对象不可再变更
CODE_FROZEN = "PL-1005"  # 项目暂停期间禁止写操作
CODE_MAT_CONFLICT = "PL-1007"  # 重要性参数非法 / 风险 AI 前置缺失（engine 拒绝）


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
        raise ProjectFrozen(CODE_FROZEN)  # 阶段门冻结，engine 拒绝非只读事务（§15-17）
    if project.status in ("已归档", "终止"):  # 终结态：禁止一切写操作（§15-17，与 quality 同口径）
        raise Locked(f"{CODE_LOCKED} 项目已终结（{project.status}），禁止写操作")


def get_materiality(db: Session, operator: User, project_id: str) -> Materiality:
    """当前项目重要性水平（plan.confirm ≥ VIEW；未设置 → NotFound PL-1002）。"""
    assert_allowed(operator.global_role, "plan.confirm", Level.VIEW)
    if db.get(AuditProject, project_id) is None:
        raise NotFound(CODE_NOT_FOUND)
    row = db.scalar(select(Materiality).where(Materiality.project_id == project_id))
    if row is None:
        raise NotFound("该项目尚未设置重要性水平（R8）")
    return row


def save_materiality(
    db: Session,
    operator: User,
    project_id: str,
    *,
    benchmark_type: str,
    basis_amount: Decimal,
    exec_ratio_pct: Decimal,
    trivial_ratio_pct: Decimal,
) -> Materiality:
    """录入/修改重要性水平（plan.confirm ≥ OPERATE）：计算经 engine，修改全量留痕并重置报批。"""
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(project)

    try:  # R8 单点计算（比例入参为百分比，换算为小数）；参数非法由 engine 拒绝（ValueError → 422）
        result = compute_materiality(
            benchmark_type, float(basis_amount),
            exec_ratio=float(exec_ratio_pct) / 100, trivial_ratio=float(trivial_ratio_pct) / 100,
        )
    except MaterialityError as exc:
        raise ValueError(f"{CODE_MAT_CONFLICT} {exc}") from exc

    existing = db.scalar(select(Materiality).where(Materiality.project_id == project_id))
    snapshot = {
        "benchmark_type": benchmark_type, "basis_amount": float(basis_amount),
        "overall": result["overall_materiality"], "exec_pct": float(exec_ratio_pct),
        "trivial_pct": float(trivial_ratio_pct),
    }
    if existing is None:
        row = Materiality(
            project_id=project_id, benchmark_type=benchmark_type, benchmark_amount=basis_amount,
            overall_materiality=Decimal(str(result["overall_materiality"])),
            execution_ratio=exec_ratio_pct, trivial_ratio=trivial_ratio_pct,
            change_log=[],
        )
        db.add(row)
    else:
        row = existing
        log = list(row.change_log or [])
        log.append({"at": _now().isoformat(), "by": operator.name, "from": snapshot})
        row.change_log = log  # JSONB 整体赋值触发更新
        row.benchmark_type = benchmark_type
        row.benchmark_amount = basis_amount
        row.overall_materiality = Decimal(str(result["overall_materiality"]))
        row.execution_ratio = exec_ratio_pct
        row.trivial_ratio = trivial_ratio_pct
        row.approved_by = None  # 修改后必须重新报批（R8）
        row.approved_at = None
        gate = db.scalar(  # 同步撤回计划门「已确认」标记，随修改重新报批
            select(StageGate).where(StageGate.project_id == project_id, StageGate.stage == "计划")
        )
        if gate is not None:
            snap = dict(gate.conditions_snapshot or {})
            snap["materiality_confirmed"] = False
            gate.conditions_snapshot = snap
    row.ratio = (row.overall_materiality / row.benchmark_amount).quantize(Decimal("0.0001"))
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="materiality", entity_id=row.id,
        action="重要性水平登记" if existing is None else "重要性水平修改",
        operator=operator.name, after=snapshot,
    )
    return row


def approve_materiality(db: Session, operator: User, project_id: str) -> Materiality:
    """重要性水平报批确认（plan.confirm=APPROVE）→ 计划门条件 materiality_confirmed 置位。"""
    assert_allowed(operator.global_role, "plan.confirm", Level.APPROVE)
    row = db.scalar(select(Materiality).where(Materiality.project_id == project_id))
    if row is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(db.get(AuditProject, project_id))

    before = {"approved_by": row.approved_by}
    row.approved_by = operator.id
    row.approved_at = _now()
    db.flush()

    gate = db.scalar(select(StageGate).where(StageGate.project_id == project_id, StageGate.stage == "计划"))
    if gate is not None:
        snap = dict(gate.conditions_snapshot or {})
        snap["materiality_confirmed"] = True
        gate.conditions_snapshot = snap

    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="materiality", entity_id=row.id,
        action="重要性水平批准", operator=operator.name, before=before,
        after={"approved_by": operator.id, "materiality_confirmed": True},
    )
    return row


def add_risk(
    db: Session, operator: User, project_id: str, *,
    source: str, description: str, risk_level: str, assertion_mapping: str | None = None,
) -> RiskItem:
    """登记风险（plan.confirm ≥ OPERATE）：AI 归集产出默认「待确认」（硬约束 3：线索须经确认）。"""
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(project)

    risk = RiskItem(
        project_id=project_id, source=source, description=description,
        risk_level=risk_level, assertion_mapping=assertion_mapping, status="待确认",
    )
    db.add(risk)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="risk_item", entity_id=risk.id,
        action="风险登记", operator=operator.name,
        after={"level": risk_level, "source": source, "status": "待确认"},
    )
    return risk


def confirm_risk(
    db: Session, operator: User, risk_id: str, *, risk_level: str | None = None
) -> RiskItem:
    """逐项确认评级（FR-02-03，plan.confirm ≥ OPERATE）：可修改评级，确认后锁定。"""
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    risk = db.get(RiskItem, risk_id)
    if risk is None:
        raise NotFound(CODE_NOT_FOUND)
    if risk.status == "已确认":
        raise Locked(CODE_LOCKED)  # 评级确认后锁定
    project = db.get(AuditProject, risk.project_id)
    _assert_writable(project)

    before = {"level": risk.risk_level}
    if risk_level is not None:
        risk.risk_level = risk_level
    risk.status = "已确认"
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="risk_item", entity_id=risk.id,
        action="风险评级确认", operator=operator.name, before=before,
        after={"level": risk.risk_level, "status": "已确认"},
    )
    return risk


def generate_programs(
    db: Session, operator: User, risk_id: str, *, items: list[dict]
) -> list[AuditProgram]:
    """按确认后的评级触发程序包（R5 经 engine.select_package 裁决，FR-02-03）。"""
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    risk = db.get(RiskItem, risk_id)
    if risk is None:
        raise NotFound(CODE_NOT_FOUND)
    if risk.status != "已确认":
        raise Locked(CODE_LOCKED)  # 评级未确认不得生成程序
    _assert_writable(db.get(AuditProject, risk.project_id))

    created: list[AuditProgram] = []
    for item in items:  # R5 裁决失败（如低风险缺标注）直接 ValueError → 422
        package_type, note = select_package(
            risk.risk_level, min_standard_note=item.get("min_standard_note")
        )
        program = AuditProgram(
            risk_id=risk_id, program_code=item["program_code"],
            package_type=package_type, min_standard_note=note, exec_status="未执行",
        )
        db.add(program)
        created.append(program)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="risk_item", entity_id=risk_id,
        action="程序包生成", operator=operator.name,
        after={"count": len(created), "level": risk.risk_level},
    )
    return created


# ---------------------------------------------------------------------------
# FR-02-02 风险评估 AI：读入财报与明细账 → 纯规则生成风险清单（线索态，每项附准则锚点）
# ---------------------------------------------------------------------------
RISK_AI_VERSION = "risk-assess@1.0.0"  # Skill 版本占位（纯规则识别，零依赖，大模型通道默认关闭）
RISK_AI_MODEL_VERSION = "risk-rules@1.0.0"

LARGE_CASH_THRESHOLD = Decimal("50000")     # 大额现金收支单笔阈值（元）
LARGE_REVENUE_THRESHOLD = Decimal("100000")  # 大额收入单笔阈值（元）
MISSING_SUMMARY_RATIO = 0.2                  # 摘要缺失行占比阈值

# 规则集（纯 Python）：命中 → 风险项草稿；标准号在库内无锚点 → 该项不出（硬约束 6 无引用不出项）
_RISK_RULES = (
    {
        "name": "大额现金收支",
        "标准号": "1301",
        "风险描述": "存在大额现金收支，货币资金存在性与截止需重点核查",
        "认定层次": "存在、截止", "建议等级": "高",
        "推荐程序": ["现金监盘", "银行函证", "大额收支凭证抽查"],
        "match": lambda rows: [r for r in rows if r.account_code.startswith("1001") and (
            (r.debit or Decimal("0")) >= LARGE_CASH_THRESHOLD
            or (r.credit or Decimal("0")) >= LARGE_CASH_THRESHOLD
        )],
    },
    {
        "name": "大额收入确认",
        "标准号": "1312",
        "风险描述": "存在大额收入确认，收入发生与截止认定存在舞弊推定风险",
        "认定层次": "发生、截止", "建议等级": "高",
        "推荐程序": ["收入截止测试", "主要客户函证", "营业收入分析程序"],
        "match": lambda rows: [r for r in rows if r.account_code.startswith("6001")
                               and (r.credit or Decimal("0")) >= LARGE_REVENUE_THRESHOLD],
    },
    {
        "name": "凭证摘要缺失",
        "标准号": "1301",
        "风险描述": "凭证摘要缺失比例偏高，内控执行有效性存疑",
        "认定层次": "完整性", "建议等级": "中",
        "推荐程序": ["凭证摘要补充核查", "内控穿行测试"],
        "match": None,  # 占比规则单独判定（见 _detect_risks）
    },
)


def _detect_risks(db: Session, project_id: str, tenant_id: str) -> tuple[list[dict], list[dict], int]:
    """纯规则风险识别：返回（风险项草稿, 无锚点跳过项, 明细账行数）。每项必带准则锚点。"""
    rows = db.scalars(select(VoucherLine).where(VoucherLine.project_id == project_id)).all()
    drafts: list[dict] = []
    skipped: list[dict] = []
    for rule in _RISK_RULES:
        if rule["name"] == "凭证摘要缺失":
            if not rows:
                continue
            missing = [r for r in rows if not (r.summary or "").strip()]
            hits, detail = missing, f"摘要缺失 {len(missing)}/{len(rows)} 行"
        else:
            hits = rule["match"](rows)  # type: ignore[misc]
            detail = f"命中 {len(hits)} 笔"
        if not hits:
            continue
        anchor = db.scalar(
            select(RuleAnchor).where(
                RuleAnchor.standard_no == rule["标准号"], RuleAnchor.tenant_id == tenant_id
            )
        )
        if anchor is None:  # 硬约束 6：无准则锚点引用不出该项
            skipped.append({"规则": rule["name"], "命中": detail, "缺锚点标准号": rule["标准号"]})
            continue
        drafts.append({
            "规则": rule["name"], "命中": detail,
            "风险描述": rule["风险描述"], "认定层次": rule["认定层次"],
            "建议等级": rule["建议等级"], "标准号": rule["标准号"],
            "锚点引用": f"准则第 {anchor.standard_no} 号 {anchor.clause or ''}".strip(),
            "锚点id": anchor.id,
            "推荐程序": rule["推荐程序"],
        })
    return drafts, skipped, len(rows)


def generate_risk_list(
    db: Session, operator: User, project_id: str
) -> tuple[list[RiskItem], AIRunORM, list[dict], int]:
    """风险评估 AI（FR-02-02，plan.confirm ≥ OPERATE）：输出风险清单草稿（线索态）+ AI 留痕。

    行为链：输入（明细账凭证行，财报材料解析状态随资料导入保证）→ 处理（纯规则识别 →
    映射认定层次 → 推荐程序组合）→ 输出（带准则锚点的风险清单草稿 + AIRun 留痕）→
    人工（项目经理逐项确认经 FR-02-03 端点）。存在待确认 AI 项时拒绝重复发起。
    """
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(project)

    pending = db.scalar(
        select(RiskItem.id).where(
            RiskItem.project_id == project_id, RiskItem.source == "ai", RiskItem.status == "待确认"
        )
    )
    if pending is not None:
        raise Locked(f"{CODE_LOCKED} 存在待确认的 AI 风险项，先逐项确认后再发起（FR-02-02/03）")

    drafts, skipped, line_count = _detect_risks(db, project_id, operator.tenant_id)
    if line_count == 0:
        raise ValueError(f"{CODE_MAT_CONFLICT} 明细账数据为空，先完成资料导入（FR-03-01，FR-02-02 前置）")

    run = pipeline.AIRun(
        project_id=project_id, module="ai_risk",
        skill_version=RISK_AI_VERSION, model_version=RISK_AI_MODEL_VERSION,
        triggered_by=operator.id, input_material_ids=[],
    )
    pipeline.record_input(
        run, f"voucher_lines:{project_id}:{line_count}", str(line_count).encode("utf-8")
    )
    output = {"risks": drafts, "skipped_no_anchor": skipped, "line_count": line_count}
    output_key = f"projects/{project_id}/risk-list-{run.run_no}.json"
    pipeline.seal_output(run, output, output_key)

    risks = [
        RiskItem(
            project_id=project_id, source="ai", description=d["风险描述"],
            assertion_mapping=d["认定层次"], risk_level=d["建议等级"],
            status="待确认",  # AI 线索态：逐项确认经 FR-02-03（硬约束 3）
            anchor_id=d["锚点id"], recommended_programs=d["推荐程序"],
        )
        for d in drafts
    ]
    db.add_all(risks)
    db.flush()

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
        action="风险评估 AI（线索态）", operator=operator.name,
        after={"risks": len(drafts), "skipped_no_anchor": len(skipped),
               "line_count": line_count, "run_no": run.run_no},
    )
    return risks, orm, skipped, line_count


def apply_risk_approved(db: Session, operator: User, run_row: AIRunORM) -> None:
    """采信回调（worksheets.mark_ai_review 分派）：风险清单第三段标记，逐项确认走 FR-02-03 端点。"""
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="ai_run", entity_id=run_row.id,
        action="风险清单采信（第三段标记）", operator=operator.name,
        after={"run_no": run_row.run_no, "output_digest": run_row.output_digest},
    )


def export_plan(db: Session, operator: User, project_id: str) -> tuple[str, str]:
    """审计计划出稿（FR-02-01，plan.confirm ≥ OPERATE）：2600 模板保真写回（硬约束 8）。

    仅填 2600 主表表头要素（被审计单位/编制/截止日/日期）；2610 重要性表头为跨表
    公式自动联动，公式区与下拉框零触碰，重要性数值行级取数留二期。
    模板键按项目业务类型准则路由（小企业准则 → plan-sme 独立键，engine 单点）。
    PL-1010 指纹/登记类拒绝（ValueError 子类）向上 → 路由 422，落盘对象已删，天然回滚。
    """
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(project)
    client = db.get(Client, project.client_id)
    if client is None:
        raise NotFound(CODE_NOT_FOUND)

    today = _now().date().isoformat()
    fields = plan_render.build_plan_fields(
        auditee=client.name, preparer=operator.name, period=project.period,
        prepared_at=today, exported_at=today,
    )
    template_key = domain_template_key("plan", project.business_type)
    object_key = f"planning/{project_id}/plan.xlsx"
    plan_render.render_plan(
        fields, template_key, storage=get_storage(), object_key=object_key,
    )
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="audit_project", entity_id=project_id,
        action="审计计划出稿", operator=operator.name,
        after={"template_key": template_key, "file_key": object_key},
    )
    return template_key, object_key
