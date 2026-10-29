"""全量异常 AI 服务（FR-03-04）：评分线索 → 人工采信 → 抽凭清单 → 调整/执行，全程留痕。

- 评分经 engine/anomaly 单点（硬约束 10：纯 Python 零依赖、固定种子可复现，§15-8）；
- AI 输出恒为线索，项目经理采信后才落抽凭清单（硬约束 3，复用 worksheets.mark_ai_review 分派）；
- 项目经理调整下限 5% 由 engine.min_sample_size 单点提供；抽凭执行状态不属于 §4 四类
  状态机（PRD 硬约束 2 未涵盖），由服务层管理并逐笔留痕。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...ai import pipeline
from ...core.audit_middleware import append_trail
from ...core.permissions import Level, assert_allowed
from ...engine.anomaly import (
    SCORE_VERSION,
    SRC_MANUAL,
    VoucherRow,
    benford_check,
    build_sampling_list,
    min_sample_size,
    score_vouchers,
)
from ...models.audit import AIRun as AIRunORM
from ...models.material import VoucherLine, VoucherSample
from ...models.project import AuditProject
from ...models.user import User
from ..worksheets.service import Locked, NotFound, ProjectFrozen  # 同域 WS 异常复用

# WS-1xxx（底稿域）补充码：FR-03-04 归属底稿域
CODE_NO_DATA = "WS-1004"  # 无凭证数据
CODE_PARAM = "WS-1007"  # 参数业务拒绝（调整下限/回填必填）


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _assert_writable(project: AuditProject) -> None:
    if project.status == "暂停":
        raise ProjectFrozen("WS-1005")
    if project.status in ("已归档", "终止"):  # 终结态：禁止一切写操作（§15-17，与 quality 同口径）
        raise Locked("WS-1006 项目已终结，禁止写操作")


def _load_rows(db: Session, project_id: str) -> list[VoucherRow]:
    lines = db.scalars(
        select(VoucherLine).where(VoucherLine.project_id == project_id)
    ).all()
    return [
        VoucherRow(
            voucher_id=l.id, account_code=l.account_code,
            amount=float(l.debit or 0) or float(l.credit or 0),
        )
        for l in lines
    ]


def scan_vouchers(db: Session, operator: User, project_id: str) -> AIRunORM:
    """发起全量异常评分（ai_output.confirm ≥ PROPOSE，审计员可发起）：产物为线索态。"""
    assert_allowed(operator.global_role, "ai_output.confirm", Level.PROPOSE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound("WS-1002")
    _assert_writable(project)

    lines = db.scalars(select(VoucherLine).where(VoucherLine.project_id == project_id)).all()
    if not lines:
        raise ValueError(f"{CODE_NO_DATA} 项目无凭证解析数据（明细账未导入），禁止空转评分")
    rows = _load_rows(db, project_id)
    scored = score_vouchers(rows)  # 固定种子，同批数据可复算（§15-8）
    benford = benford_check(rows)
    sampling = build_sampling_list(scored)

    run = pipeline.AIRun(
        project_id=project_id, module="ai_anomaly",
        skill_version="anomaly-scan@1.0.0", model_version=SCORE_VERSION,
        triggered_by=operator.id, input_material_ids=[],
    )
    pipeline.record_input(
        run, f"voucher-lines:{project_id}:{len(lines)}", str(len(lines)).encode("utf-8")
    )
    output = {
        "score_version": SCORE_VERSION, "total": len(lines),
        "scored": [
            {"voucher_id": s.voucher_id, "account_code": s.account_code, "score": s.score}
            for s in scored
        ],
        "benford": benford, "sampling": sampling,
    }
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
        action="异常评分生成（线索态）", operator=operator.name,
        after={"score_version": SCORE_VERSION, "total": len(lines), "sampling": len(sampling)},
    )
    return orm


def apply_approved(db: Session, operator: User, run_row: AIRunORM) -> None:
    """采信回调（worksheets.mark_ai_review 分派）：确定性重算并落抽凭清单。"""
    scored = score_vouchers(_load_rows(db, run_row.project_id))
    sampling = build_sampling_list(scored)
    score_map = {s.voucher_id: s.score for s in scored}

    existing = db.scalars(
        select(VoucherSample).where(VoucherSample.project_id == run_row.project_id)
    ).all()
    keep_executed = {s.voucher_line_id for s in existing if s.exec_status == "已执行"}
    for s in existing:
        if s.exec_status != "已执行":
            db.delete(s)  # 重扫采信：未执行旧行由新清单整体替换
    db.flush()
    for item in sampling:
        if item["voucher_id"] in keep_executed:
            continue  # 已执行笔保留原记录不重建（留痕不灭失）
        db.add(VoucherSample(
            project_id=run_row.project_id,
            voucher_line_id=item["voucher_id"],
            score=score_map.get(item["voucher_id"]),
            source=item["source"],
        ))
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="ai_run", entity_id=run_row.id,
        action="抽凭清单落库（AI 采信）", operator=operator.name,
        after={"count": len(sampling), "kept_executed": len(keep_executed)},
    )


def list_samples(db: Session, project_id: str) -> list[VoucherSample]:
    """抽凭清单（读操作，登录即可）。"""
    return list(db.scalars(
        select(VoucherSample).where(VoucherSample.project_id == project_id)
    ).all())


def adjust_samples(
    db: Session, operator: User, project_id: str, *,
    add_ids: list[str], remove_ids: list[str],
) -> list[VoucherSample]:
    """项目经理调整清单（plan.confirm ≥ OPERATE，审计员=NONE 拒绝）：下限 5%，调整留痕。"""
    assert_allowed(operator.global_role, "plan.confirm", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound("WS-1002")
    _assert_writable(project)

    total = db.scalar(
        select(func.count(VoucherLine.id)).where(VoucherLine.project_id == project_id)
    )
    samples = db.scalars(
        select(VoucherSample).where(VoucherSample.project_id == project_id)
    ).all()
    remove_set = set(remove_ids or [])
    for s in samples:
        if s.id in remove_set and s.exec_status == "已执行":
            raise Locked(f"WS-1006 已执行抽凭记录不可移除（留痕不灭失）")
    kept = [s for s in samples if s.id not in remove_set]
    existing_line_ids = {s.voucher_line_id for s in kept}
    new_ids = [i for i in (add_ids or []) if i not in existing_line_ids]

    floor = min_sample_size(total or 0)
    if len(kept) + len(new_ids) < floor:
        raise ValueError(
            f"{CODE_PARAM} 调整后清单 {len(kept) + len(new_ids)} 笔低于下限 {floor} 笔（5%，FR-03-04）"
        )

    for s in samples:
        if s.id in remove_set:
            db.delete(s)
    for vid in new_ids:
        if db.get(VoucherLine, vid) is None:
            raise NotFound(f"WS-1002 凭证行不存在：{vid}")
        db.add(VoucherSample(
            project_id=project_id, voucher_line_id=vid, score=None, source=SRC_MANUAL,
        ))
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="project", entity_id=project_id,
        action="抽凭清单调整", operator=operator.name,
        after={"added": len(new_ids), "removed": len(remove_set),
               "total": len(kept) + len(new_ids), "floor": floor},
    )
    return list_samples(db, project_id)


def execute_sample(
    db: Session, operator: User, sample_id: str, *, exec_note: str,
) -> VoucherSample:
    """审计员按清单执行并回填结果（worksheet.edit ≥ OPERATE），执行记录留痕。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = db.get(VoucherSample, sample_id)
    if row is None:
        raise NotFound("WS-1002")
    _assert_writable(db.get(AuditProject, row.project_id))
    if row.exec_status == "已执行":
        raise Locked(f"WS-1006 该笔已执行回填，禁止重复执行（留痕不灭失）")
    if not (exec_note and exec_note.strip()):
        raise ValueError(f"{CODE_PARAM} 回填执行说明必填")

    row.exec_status = "已执行"
    row.exec_note = exec_note.strip()
    row.executed_by = operator.id
    row.executed_at = _now()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="voucher_sample", entity_id=row.id,
        action="抽凭执行回填", operator=operator.name,
        before={"exec_status": "未执行"},
        after={"exec_status": "已执行", "note": row.exec_note},
    )
    return row
