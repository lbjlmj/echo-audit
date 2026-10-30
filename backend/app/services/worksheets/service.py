"""底稿域业务逻辑（FR-03-01/02/03 + FR-05-02 复核流转；PRD §4.3 六状态）。

规则落点：
- 六状态流转一律经 engine.WORKSHEET（硬约束 2），退回必附意见（FR-05-02），
  终审仅限重要底稿（§15-11），编制人=复核人拒绝（R1，core 单点）；
- AI 初稿经 ai/pipeline 三段式（红线 1、§7 R2）：无人工复核标记不得进版本链、不得提交；
- 三层级材料任一层缺失即拦截并列出缺口清单，不出半成品（FR-03-01）；
- 项目「暂停」期间拒绝写操作（§15-17）；留痕一律经 core.append_trail 续链（硬约束 4）；
- 模板结构指纹单点在 engine/fingerprint（硬约束 8），MinIO 文件流接线后于版本落链前后调用。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...ai import pipeline
from ...core.audit_middleware import append_trail
from ...core.permissions import (
    Level,
    assert_allowed,
    check_preparer_reviewer_separation,
)
from ...core.storage import get_storage
from ...engine.industry_matrix import domain_template_key
from ...engine.state_machines import WORKSHEET, IllegalTransition
from ...models.audit import AIRun as AIRunORM
from ...models.knowledge import RuleAnchor, Template
from ...models.material import MATERIAL_LEVELS, Material
from ...models.project import AuditProject, Client
from ...models.risk import Materiality
from ...models.user import User
from ...models.worksheet import Worksheet, WorksheetReview, WorksheetVersion
from . import catalog
from . import render as ws_render
from . import review_render

# WS-1xxx 底稿域错误码
CODE_TRANSITION = "WS-1001"  # 非法状态流转（engine 拒绝）
CODE_NOT_FOUND = "WS-1002"
CODE_GAP = "WS-1003"  # 三层材料缺口（FR-03-01 不出半成品）
CODE_FROZEN = "WS-1005"  # 项目暂停冻结
CODE_LOCKED = "WS-1006"  # 对象锁定（无版本内容/复核标记一次性）
CODE_PARAM = "WS-1007"  # 出稿前置/参数类拒绝

# 大模型通道默认关闭（§15-15/硬约束 6）：Phase 1 AI 初稿走规则模板确定性生成，
# llm_enabled=True 且满足三条件门禁后才切换 vLLM 通道。
_RULE_MODEL_VERSION = "rule-template-v1"

_LEVEL_LABELS = {"customer": "客户层", "firm": "事务所层", "rule": "填表说明层"}


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


def list_worksheets(db: Session, operator: User, project_id: str) -> list[Worksheet]:
    """底稿列表（worksheet.edit ≥ VIEW；按创建时间升序）。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.VIEW)
    return list(
        db.scalars(
            select(Worksheet)
            .where(Worksheet.project_id == project_id)
            .order_by(Worksheet.created_at)
        ).all()
    )


def list_anchors(db: Session) -> list[RuleAnchor]:
    """准则锚点下拉数据源（登录即可读：锚点是全所公共知识，红线 2 库端可反查）。"""
    return list(db.scalars(select(RuleAnchor).order_by(RuleAnchor.standard_no, RuleAnchor.clause)).all())


def list_templates(db: Session) -> list[Template]:
    """底稿模板下拉数据源（登录即可读：模板矩阵为全所公共资产，随版本发布）。"""
    return list(db.scalars(select(Template).order_by(Template.type, Template.version)).all())


def list_project_options(db: Session, operator: User) -> list[AuditProject]:
    """底稿工作台可选项目（worksheet.edit ≥ VIEW；不含已归档）。

    编制人（审计员）无 project.approve 权限，/projects 列表 403——底稿工作台
    以本视图为项目下拉数据源（只读投影，不含审批字段）。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.VIEW)
    return list(
        db.scalars(
            select(AuditProject)
            .where(AuditProject.status != "已归档")
            .order_by(AuditProject.created_at.desc())
        ).all()
    )


def list_ai_runs(db: Session, operator: User, worksheet_id: str) -> list[AIRunORM]:
    """底稿的 AI 运行线索列表（FR-03-02 第三段确认的数据源，线索态须醒目）。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.VIEW)
    _get_ws(db, worksheet_id)
    return list(
        db.scalars(
            select(AIRunORM)
            .where(AIRunORM.related_type == "worksheet", AIRunORM.related_id == worksheet_id)
            .order_by(AIRunORM.created_at.desc())
        ).all()
    )


def _assert_writable(project: AuditProject) -> None:
    if project.status == "暂停":
        raise ProjectFrozen(CODE_FROZEN)
    if project.status in ("已归档", "终止"):  # 终结态：禁止一切写操作（§15-17，与 quality 同口径）
        raise Locked(f"{CODE_LOCKED} 项目已终结（{project.status}），禁止写操作")


def get_worksheet_file(db: Session, worksheet_id: str) -> tuple[Worksheet, bytes, str]:
    """底稿当前版本产物字节（下载）：无版本 → WS-1006；对象缺失 → WS-1002。

    返回 (底稿, 字节, media_type)：xlsx 写回产物与历史 JSON 线索键均按后缀区分。
    """
    ws = _get_ws(db, worksheet_id)
    if not ws.file_key:
        raise Locked(f"{CODE_LOCKED} 底稿版本链为空，无产物可下载")
    storage = get_storage()
    if not storage.exists(ws.file_key):
        raise NotFound(f"{CODE_NOT_FOUND} 版本产物对象缺失：{ws.file_key}")
    media = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if ws.file_key.endswith(".xlsx") else "application/json"
    )
    return ws, storage.load_bytes(ws.file_key), media


def _get_ws(db: Session, worksheet_id: str) -> Worksheet:
    ws = db.get(Worksheet, worksheet_id)
    if ws is None:
        raise NotFound(CODE_NOT_FOUND)
    return ws


def _transit(ws: Worksheet, target: str, **ctx: object) -> str:
    """六状态流转只走 engine（硬约束 2）：裁决通过才赋回状态字段。"""
    try:
        ws.status = WORKSHEET.transit(ws.status, target, **ctx)
    except IllegalTransition as exc:
        raise TransitionRejected(f"{CODE_TRANSITION} {exc}") from exc
    return ws.status


def _latest_version(db: Session, worksheet_id: str) -> WorksheetVersion | None:
    return db.scalar(
        select(WorksheetVersion)
        .where(WorksheetVersion.worksheet_id == worksheet_id)
        .order_by(WorksheetVersion.created_at.desc())
        .limit(1)
    )


def _append_version(
    db: Session, operator: User, worksheet_id: str, *, file_key: str, summary: str
) -> WorksheetVersion:
    """版本链追加（版本链而非覆盖写）；同步推进底稿当前版本与文件键。"""
    ws = db.get(Worksheet, worksheet_id)
    n = db.scalar(
        select(func.count(WorksheetVersion.id)).where(WorksheetVersion.worksheet_id == worksheet_id)
    )
    vno = f"v{(n or 0) + 1}"
    ver = WorksheetVersion(
        worksheet_id=worksheet_id, version_no=vno, file_key=file_key,
        change_summary=summary, prepared_by=operator.id,
    )
    db.add(ver)
    ws.current_version = vno
    ws.file_key = file_key
    db.flush()
    return ver


def append_version(
    db: Session, operator: User, worksheet_id: str, *, file_key: str, summary: str
) -> WorksheetVersion:
    """版本链追加的跨域出口（差错归档 v(n+1) 副本修订等场景复用，版本链单点口径）。"""
    return _append_version(db, operator, worksheet_id, file_key=file_key, summary=summary)


def create_worksheet(
    db: Session, operator: User, project_id: str, *,
    title: str, rule_anchor_id: str, is_important: bool = False,
    template_id: str | None = None, template_key: str | None = None,
) -> Worksheet:
    """创建底稿（worksheet.edit ≥ OPERATE）：准则锚点随创建强制带出（FR-03-03，红线 2）。

    模板来源二选一：template_id（模板矩阵 Template 行 id）优先；template_key
    （科目目录树键，如 templates/cas/4101.xlsx）按 (type, version) 幂等登记
    Template 行（catalog.resolve_template_by_key 单点），键不在种子中即拒绝。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(CODE_NOT_FOUND)
    _assert_writable(project)
    if template_id is not None:
        if db.get(Template, template_id) is None:
            raise NotFound(f"{CODE_NOT_FOUND} 模板不存在")
        tpl_id = template_id
    else:
        tpl_id = catalog.resolve_template_by_key(db, template_key or "").id
    anchor = db.get(RuleAnchor, rule_anchor_id)
    if anchor is None:
        raise NotFound(f"{CODE_NOT_FOUND} 准则锚点不存在（红线 2：锚点不可空）")

    seq = db.scalar(
        select(func.count(Worksheet.id)).where(Worksheet.project_id == project_id)
    )
    ws = Worksheet(
        project_id=project_id, code=f"{project.project_no}-WS{(seq or 0) + 1:03d}",
        title=title, template_id=tpl_id, rule_anchor_id=rule_anchor_id,
        is_important=is_important, status="草稿", current_version="v0",
    )
    db.add(ws)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="worksheet", entity_id=ws.id,
        action="底稿创建", operator=operator.name,
        after={"code": ws.code, "anchor": f"{anchor.standard_no} {anchor.clause}"},
    )
    return ws


def generate_ai_draft(db: Session, operator: User, worksheet_id: str) -> AIRunORM:
    """AI 初稿（FR-03-02，ai_output.confirm ≥ PROPOSE 即审计员可触发）。

    三段式只走前两段：产物恒为「线索」态——未经人工复核标记不得进版本链（红线 1/R2）。
    无法可靠生成的单元格置空标注「AI 未能生成」，不得编造（FR-03-02）。
    """
    assert_allowed(operator.global_role, "ai_output.confirm", Level.PROPOSE)
    ws = _get_ws(db, worksheet_id)
    _assert_writable(db.get(AuditProject, ws.project_id))

    # FR-03-01 三层级材料校验：任一层缺失即拦截并列缺口清单（不出半成品）
    missing = [
        _LEVEL_LABELS[lv] for lv in MATERIAL_LEVELS
        if db.scalar(
            select(Material.id).where(
                Material.project_id == ws.project_id,
                Material.level == lv,
                Material.gap_flag.is_(False),
            )
        )
        is None
    ]
    if missing:
        raise ValueError(f"{CODE_GAP} 三层材料缺口，不出半成品：{missing}")

    run = pipeline.AIRun(
        project_id=ws.project_id, module="ai_notes",
        skill_version="audit-notes@1.0.0", model_version=_RULE_MODEL_VERSION,
        triggered_by=operator.id, input_material_ids=[],
    )
    for m in db.scalars(
        select(Material).where(
            Material.project_id == ws.project_id, Material.level.in_(MATERIAL_LEVELS)
        )
    ):
        # MinIO 未接线的 Phase 1 口径：以对象键字节为输入哈希依据（对象键不可变）
        pipeline.record_input(run, m.id, m.file_key.encode("utf-8"))

    anchor = db.get(RuleAnchor, ws.rule_anchor_id)
    draft = {
        "worksheet_id": ws.id, "template_id": ws.template_id,
        "rule_anchor": {
            "standard_no": anchor.standard_no, "clause": anchor.clause, "purpose": anchor.purpose,
        },
        "cells": {"编制人": operator.name, "项目编号": _project_no(db, ws), "底稿编号": ws.code},
        "unfilled": [{"cell": "数据区", "note": "AI 未能生成，待人工取数"}],
    }
    output_key = f"worksheets/{ws.id}/{run.run_no}.json"
    pipeline.seal_output(run, draft, output_key)

    row = AIRunORM(
        run_no=run.run_no, project_id=run.project_id, module=run.module,
        skill_version=run.skill_version, model_version=run.model_version,
        triggered_by=run.triggered_by,
        input_material_ids=run.input_material_ids, input_hashes=run.input_hashes,
        output_digest=run.output_digest, output_key=run.output_key,
        status="线索", related_type="worksheet", related_id=ws.id,
    )
    db.add(row)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="ai_run", entity_id=row.id,
        action="AI 初稿生成（线索态）", operator=operator.name,
        after={"run_no": run.run_no, "digest": run.output_digest},
    )
    return row


def _project_no(db: Session, ws: Worksheet) -> str:
    return db.get(AuditProject, ws.project_id).project_no


def _render_ai_draft_xlsx(db: Session, row: AIRunORM) -> str:
    """AI 初稿采信 → 按底稿模板保真写回（硬约束 8，FR-03-02 收口）。

    内容同批可复现、不编造（红线 1）：真实模板（manifest 登记 header_map
    标签语义位）按表头语义填充——被审计单位/索引号/项目/截止日/编制/日期，
    逐表命中白名单；准则锚点（红线 2）由 Worksheet.rule_anchor_id 落库带出。
    占位模板（无标签语义登记）沿用要素纵排坐标约定（编制人/项目编号/底稿
    编号/锚点/AI 置空提示/R8 注记）。对象键以 run_no 命名（不可变、版本间
    不冲突）。任一道闸失败即 ValueError → 422，版本链不落（回滚）。
    """
    ws = db.get(Worksheet, row.related_id)
    tpl = db.get(Template, ws.template_id)
    author = db.get(User, row.triggered_by)
    header = ws_render.header_coords(tpl.skeleton_key)
    if header:
        # 真实模板：登记期已识别表头标签语义位（B2=被审计单位、G2=索引号…），
        # 逐表命中 fill_cells 白名单；复核位不填（AI 初稿阶段复核人未定，不编造）
        project = db.get(AuditProject, ws.project_id)
        client = db.get(Client, project.client_id)
        values = {
            "被审计单位": client.name,
            "索引号": ws.code,
            "项目": ws.title,
            "截止日": project.period,
            "编制": author.name if author else "—",
            "日期": _now().date().isoformat(),
        }
        fields = {header[k]: v for k, v in values.items() if k in header}
    else:
        anchor = db.get(RuleAnchor, ws.rule_anchor_id)
        fields = {
            "B3": author.name if author else "—",
            "B4": _project_no(db, ws),
            "B5": ws.code,
            "B6": f"{anchor.standard_no} {anchor.clause}" if anchor else "—",
            "B8": "AI 未能生成，待人工取数（数据区）",
            "B9": _materiality_note(db, ws.project_id),  # R8 引用：编制错报评估的基准
        }
    object_key = f"worksheets/{ws.id}/{row.run_no}.xlsx"
    try:
        return ws_render.render_ai_draft(
            fields, tpl.skeleton_key, storage=get_storage(), object_key=object_key,
        )
    except ws_render.TemplateNotRegistered as exc:
        raise ValueError(str(exc)) from exc


def _materiality_note(db: Session, project_id: str) -> str:
    """底稿上的 R8 引用行：已批准 → 执行/整体值；未批准或未设置 → 占位说明（不编造）。"""
    mat = db.scalar(select(Materiality).where(Materiality.project_id == project_id))
    if mat is None or mat.approved_at is None:
        return "—（重要性水平未经合伙人批准，随计划报批后带出）"
    overall = float(mat.overall_materiality)
    exec_pct = float(mat.execution_ratio)
    performance = overall * exec_pct / 100
    return f"¥{performance:,.2f}（整体 ¥{overall:,.2f} × {exec_pct:g}%）"


def mark_ai_review(
    db: Session, operator: User, run_no: str, *,
    conclusion: str, rejection_reason: str | None = None,
) -> AIRunORM:
    """第三段人工复核标记（ai_output.confirm ≥ OPERATE）：采信才落版本链（红线 1/R2）。"""
    assert_allowed(operator.global_role, "ai_output.confirm", Level.OPERATE)
    row = db.scalar(select(AIRunORM).where(AIRunORM.run_no == run_no))
    if row is None:
        raise NotFound(CODE_NOT_FOUND)
    if row.status != "线索":
        raise Locked(f"{CODE_LOCKED} 复核标记一次性（append-only），禁止重复标记")
    _assert_writable(db.get(AuditProject, row.project_id))

    # 保真写回先行（硬约束 8）：任一道闸失败即 ValueError → 422，run 保持线索态
    # 可重新标记（采信原子性：写回不成不落复核标记、不落版本链）。
    file_key: str | None = None
    if conclusion != "不采信" and row.module == "ai_notes" and row.related_type == "worksheet":
        file_key = _render_ai_draft_xlsx(db, row)

    run = pipeline.AIRun(
        project_id=row.project_id, module=row.module,
        skill_version=row.skill_version, model_version=row.model_version,
        triggered_by=row.triggered_by,
        input_material_ids=list(row.input_material_ids or []),
        input_hashes=dict(row.input_hashes or {}),
        run_no=row.run_no, output_digest=row.output_digest or "",
        output_key=row.output_key or "",
    )
    try:
        pipeline.mark_review(run, operator.name, conclusion, rejection_reason)  # 不采信必附原因（§15-16）
        row.status = run.status
        row.reviewed_by = operator.id
        row.reviewed_at = _now()
        row.rejection_reason = run.rejection_reason  # §15-16：原因分类落库（归档脱敏汇总依据）
        if conclusion != "不采信":
            pipeline.assert_can_enter_business(run, "业务表")  # 双保险：无标记不可进业务表
            if row.module == "ai_anomaly":  # FR-03-04：异常评分采信 → 落抽凭清单（延迟导入避免环）
                from ..anomaly import service as anomaly_service

                anomaly_service.apply_approved(db, operator, row)
            elif row.module == "ai_finding":
                # FR-03-06：问题归集线索标记即完成——逐条确认经 /findings 状态机端点（清单状态机即三段式载体）
                pass
            elif row.module == "ai_qc":
                # FR-06-01：质控检查报告采信 → 冻结为完成门（门 6）准入依据（延迟导入避免环）
                from ..quality import service as quality_service

                quality_service.apply_qc_approved(db, operator, row)
            elif row.module == "ai_notes" and row.related_type == "report":
                # FR-05-03：附注草稿采信 → 进入报告稿（延迟导入避免环）
                from ..reports import service as reports_service

                reports_service.apply_note_approved(db, operator, row)
            elif row.module == "ai_risk":
                # FR-02-02：风险清单采信（第三段标记）→ 逐项确认经 FR-02-03 端点（延迟导入避免环）
                from ..planning import service as planning_service

                planning_service.apply_risk_approved(db, operator, row)
            elif row.module == "rag":
                # FR-07-01 RAG 问答留痕：第三段标记即闭环（答案带准则引用、不进业务表）
                pass
            else:  # ai_notes（底稿初稿）：已先行保真写回（硬约束 8）→ 进版本链
                _append_version(
                    db, operator, row.related_id,
                    file_key=file_key or _render_ai_draft_xlsx(db, row),
                    summary="AI 初稿（经人工复核采信，模板保真写回）",
                )
    except pipeline.PipelineViolation as exc:  # 管道门禁拒绝（如不采信缺原因）→ 422
        raise ValueError(str(exc)) from exc
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="ai_run", entity_id=row.id,
        action="AI 输出复核标记", operator=operator.name, before={"status": "线索"},
        after={"status": conclusion, "reason": rejection_reason},
    )
    return row


def submit_worksheet(db: Session, operator: User, worksheet_id: str) -> Worksheet:
    """提交复核（worksheet.edit ≥ OPERATE）：草稿→待复核，AI 内容须完成复核标记（R2）。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    ws = _get_ws(db, worksheet_id)
    _assert_writable(db.get(AuditProject, ws.project_id))

    has_version = db.scalar(
        select(WorksheetVersion.id).where(WorksheetVersion.worksheet_id == worksheet_id)
    )
    if has_version is None:
        raise Locked(f"{CODE_LOCKED} 底稿无版本内容（AI 初稿未采信或未人工编制），禁止提交")
    runs = db.scalars(
        select(AIRunORM).where(
            AIRunORM.related_type == "worksheet", AIRunORM.related_id == worksheet_id
        )
    ).all()
    ai_marked = all(r.reviewed_by is not None for r in runs)  # 任一线索未标记即拦截
    _transit(ws, "待复核", submitted_by=operator.id, ai_review_marked=ai_marked)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="worksheet", entity_id=ws.id,
        action="底稿提交复核", operator=operator.name,
        before={"status": "草稿"}, after={"status": "待复核", "version": ws.current_version},
    )
    return ws


def take_review(db: Session, operator: User, worksheet_id: str) -> Worksheet:
    """受理复核（worksheet.review_sign ≥ OPERATE）：待复核→复核中；编制人=复核人拒绝（R1）。"""
    assert_allowed(operator.global_role, "worksheet.review_sign", Level.OPERATE)
    ws = _get_ws(db, worksheet_id)
    _assert_writable(db.get(AuditProject, ws.project_id))

    latest = _latest_version(db, worksheet_id)
    check_preparer_reviewer_separation(latest.prepared_by, operator.id)  # R1（→403）
    _transit(ws, "复核中", reviewer=operator.id)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="worksheet", entity_id=ws.id,
        action="复核受理", operator=operator.name,
        before={"status": "待复核"}, after={"status": "复核中"},
    )
    return ws


def conclude_review(
    db: Session, operator: User, worksheet_id: str, *,
    conclusion: str, opinion: str | None = None, revision_requirement: str | None = None,
) -> Worksheet:
    """复核结论（worksheet.review_sign ≥ OPERATE）：通过/退回；退回必附意见（FR-05-02）。"""
    assert_allowed(operator.global_role, "worksheet.review_sign", Level.OPERATE)
    ws = _get_ws(db, worksheet_id)
    _assert_writable(db.get(AuditProject, ws.project_id))
    latest = _latest_version(db, worksheet_id)

    round_no = (
        db.scalar(
            select(func.count(WorksheetReview.id)).where(
                WorksheetReview.worksheet_id == worksheet_id
            )
        )
        or 0
    ) + 1
    if conclusion == "通过":
        _transit(ws, "复核通过", reviewer=operator.id)
        review = WorksheetReview(
            worksheet_id=worksheet_id, version_id=latest.id, round_no=round_no,
            reviewer_id=operator.id, opinion=opinion, conclusion="通过", reviewed_date=date.today(),
            signature={  # 电子签名=复核人+日期+证书号（FR-05-02）
                "复核人": operator.name, "日期": date.today().isoformat(),
                "证书号": f"CERT-{operator.id[:8].upper()}",
            },
        )
    else:  # 退回：engine 强制 opinion 前置，缺失即 IllegalTransition（FR-05-02）
        _transit(ws, "草稿", reviewer=operator.id, opinion=opinion)
        review = WorksheetReview(
            worksheet_id=worksheet_id, version_id=latest.id, round_no=round_no,
            reviewer_id=operator.id, opinion=opinion, revision_requirement=revision_requirement,
            conclusion="退回", reviewed_date=date.today(),
        )
    db.add(review)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="worksheet", entity_id=ws.id,
        action=f"复核结论-{conclusion}", operator=operator.name,
        after={"status": ws.status, "round": round_no, "opinion": opinion},
    )
    return ws


def final_approve(db: Session, operator: User, worksheet_id: str) -> Worksheet:
    """终审（worksheet.review_sign=APPROVE 仅合伙人）：复核通过→终审通过，仅重要底稿（§15-11）。"""
    assert_allowed(operator.global_role, "worksheet.review_sign", Level.APPROVE)
    ws = _get_ws(db, worksheet_id)
    _assert_writable(db.get(AuditProject, ws.project_id))
    _transit(ws, "终审通过", approver=operator.id, is_important=bool(ws.is_important))
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="worksheet", entity_id=ws.id,
        action="底稿终审通过", operator=operator.name,
        before={"status": "复核通过"}, after={"status": "终审通过"},
    )
    return ws


REVIEW_SHEET_STATUSES = ("复核通过", "终审通过")


def export_review_sheet(db: Session, operator: User, worksheet_id: str) -> tuple[str, str, str]:
    """复核核对表出稿（FR-05-02，worksheet.review_sign ≥ OPERATE）：6700 模板保真写回。

    前置：底稿已复核通过（复核通过/终审通过）。仅填「业务复核核对表-准则稿」表头
    要素（被审计单位/编制=底稿编制人/复核=操作人/日期）；意见栏与三级复核勾选留人工。
    WS-1020 指纹/登记类拒绝（ValueError 子类）向上 → 路由 422，落盘对象已删，天然回滚。
    """
    assert_allowed(operator.global_role, "worksheet.review_sign", Level.OPERATE)
    ws = _get_ws(db, worksheet_id)
    if ws.status not in REVIEW_SHEET_STATUSES:
        raise ValueError(f"{CODE_PARAM} 底稿未复核通过（当前「{ws.status}」），核对表无出稿依据")
    project = db.get(AuditProject, ws.project_id)
    client = db.get(Client, project.client_id)
    if client is None:
        raise ValueError(f"{CODE_NOT_FOUND} 项目客户缺失")

    today = date.today().isoformat()
    preparer = "编制人"
    latest = _latest_version(db, worksheet_id)
    if latest is not None and latest.prepared_by:
        creator = db.get(User, latest.prepared_by)
        preparer = creator.name if creator is not None else preparer
    reviewer = operator.name  # 出稿人兜底；取最近一轮复核记录的复核人（FR-05-02）
    last_review = db.scalar(
        select(WorksheetReview)
        .where(WorksheetReview.worksheet_id == worksheet_id)
        .order_by(WorksheetReview.round_no.desc())
    )
    if last_review is not None:
        reviewer_row = db.get(User, last_review.reviewer_id)
        reviewer = reviewer_row.name if reviewer_row is not None else reviewer
    fields = review_render.build_review_sheet_fields(
        auditee=client.name, preparer=preparer, reviewer=reviewer,
        prepared_at=today, reviewed_at=today,
    )
    template_key = domain_template_key("review", project.business_type)
    object_key = f"worksheets/{worksheet_id}/review-sheet.xlsx"
    review_render.render_review_sheet(
        fields, template_key,
        storage=get_storage(), object_key=object_key,
    )
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="worksheet", entity_id=ws.id,
        action="复核核对表出稿", operator=operator.name,
        after={"template_key": template_key, "file_key": object_key},
    )
    return template_key, object_key, ws.status


def update_cross_refs(
    db: Session, operator: User, worksheet_id: str, *, cross_refs: list[dict]
) -> dict[str, object]:
    """交叉索引写入（FR-03-05，worksheet.edit ≥ OPERATE）：保存时自动校验断链（R7）。

    引用规范 [{"code": 底稿编号, "note": 说明}]；断链 = 引用编号在本项目不存在
    （与质控检查器同一口径，FR-06-01 第 2 检查项不重复实现，仅写入时前置提示）；
    复核链上（待复核起）锁定不可改——引用变更须先退回草稿。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    ws = _get_ws(db, worksheet_id)
    _assert_writable(db.get(AuditProject, ws.project_id))
    if ws.status != "草稿":
        raise Locked(f"{CODE_LOCKED} 底稿状态 {ws.status} 不可编辑交叉索引（仅草稿态）")
    codes = {
        w.code for w in db.scalars(
            select(Worksheet).where(Worksheet.project_id == ws.project_id)
        )
    }
    clean: list[dict[str, str]] = []
    broken: list[str] = []
    for ref in cross_refs:
        code = ((ref.get("code") or "").strip() if isinstance(ref, dict)
                else str(ref).strip())
        if not code:
            continue
        note = (ref.get("note") or "") if isinstance(ref, dict) else ""
        clean.append({"code": code, "note": note})
        if code not in codes:
            broken.append(code)
    before = ws.cross_refs
    ws.cross_refs = clean  # JSONB 整体赋值以触发更新
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="worksheet", entity_id=ws.id,
        action="交叉索引更新（FR-03-05）", operator=operator.name,
        before={"cross_refs": before},
        after={"cross_refs": clean, "broken_refs": broken},
    )
    return {
        "worksheet_id": ws.id, "cross_refs": clean,
        "broken_refs": broken, "ok": not broken,  # 断链提示实时返回，不阻断保存
    }
