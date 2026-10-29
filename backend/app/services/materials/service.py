"""资料域业务逻辑（FR-03-01 资料导入与缺口 / §15-6 字段映射；MA-1xxx）。

规则落点：
- 三层级材料（§15-6）：客户层 / 事务所层 / 填表说明层；必需材料未到位 → gap_flag=True
  缺口登记，补齐后置 False；底稿引擎侧出稿拦截点已在 services/worksheets（WS-1003
  「不出半成品」），本域只维护材料行与缺口状态，不重复实现拦截；
- 解析状态（待解析→已解析/失败）由服务层按前置流转并逐次留痕（非 §4 四状态机范围）；
  解析前置：材料必须已到位（缺口登记不可解析）；
- 字段映射（§15-6）：人工映射一次、同客户复用；创建时按类型跑最低字段校验——
  明细账：日期/凭证号/摘要/科目编码/科目名称/借方/贷方（对方科目可选）；
  科目余额表：科目编码/科目名称/期初/借方/贷方/期末 + 级次（级次≥3）；
- 凭证行导入：仅「已解析」的客户层材料可导入，落 VoucherLine（FR-03-04 全量异常评分数据池）；
- 权限以后端矩阵为准（硬约束 5）：登记/补齐/解析/映射/导入均 worksheet.edit ≥ OPERATE
  （审计员与经理日常操作，质控 VIEW 被拒）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...ai import pipeline
from ...core.audit_middleware import append_trail
from ...core.permissions import Level, assert_allowed
from ...engine.naming import CATEGORY_CODES, build_document_name
from ...models.audit import AIRun as AIRunORM
from ...models.material import MATERIAL_LEVELS, ImportFieldMapping, Material, VoucherLine
from ...models.project import AuditProject, Client
from ...models.user import User

# MA-1xxx 资料域错误码
CODE_NOT_FOUND = "MA-1002"
CODE_LOCKED = "MA-1006"
CODE_FROZEN = "MA-1005"
CODE_PARAM = "MA-1007"

# §15-6 最低字段要求（标准字段名 → 校验键；映射目标端必须全覆盖）
TRIAL_BALANCE_REQUIRED = ("科目编码", "科目名称", "期初余额", "借方发生额", "贷方发生额", "期末余额", "级次")
VOUCHER_REQUIRED = ("日期", "凭证号", "摘要", "科目编码", "科目名称", "借方", "贷方")

MAPPING_TYPES = ("科目余额表", "明细账")


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
    if project.status in ("已归档", "终止"):
        raise Locked(f"{CODE_LOCKED} 项目已终结（{project.status}），禁止写操作")


def _get_material(db: Session, material_id: str) -> Material:
    row = db.get(Material, material_id)
    if row is None:
        raise NotFound(f"{CODE_NOT_FOUND} 材料不存在")
    return row


# ---------------------------------------------------------------------------
# 三层级材料登记 / 补齐 / 解析 / 缺口清单
# ---------------------------------------------------------------------------
def register_material(
    db: Session, operator: User, project_id: str, *,
    level: str, file_key: str, required: bool = False,
) -> Material:
    """登记三层级材料（worksheet.edit ≥ OPERATE）：必需未到位 → 缺口登记（gap_flag=True）。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(f"{CODE_NOT_FOUND} 项目不存在")
    _assert_writable(project)
    if level not in MATERIAL_LEVELS:
        raise ValueError(f"{CODE_PARAM} 层级非法：{level}（合法值：{MATERIAL_LEVELS}）")
    if not (file_key and file_key.strip()):
        raise ValueError(f"{CODE_PARAM} 对象键必填（MinIO 对象键，不入库明文路径）")

    row = Material(
        project_id=project_id, level=level, file_key=file_key.strip(),
        origin_name=file_key.strip(),  # 登记瞬间即客户原始文件名（分类重命名归档前保留）
        parse_status="待解析", gap_flag=required,  # 必需材料登记即挂缺口，补齐后解除
    )
    db.add(row)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="material", entity_id=row.id,
        action="材料登记", operator=operator.name,
        after={"level": level, "file_key": row.file_key, "gap_flag": required},
    )
    return row


def mark_arrived(db: Session, operator: User, material_id: str) -> Material:
    """材料补齐（worksheet.edit ≥ OPERATE）：缺口 → 到位（gap_flag=False），待解析。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_material(db, material_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if not row.gap_flag:
        raise ValueError(f"{CODE_LOCKED} 材料已到位，无需重复补齐")

    row.gap_flag = False
    row.parse_status = "待解析"
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="material", entity_id=row.id,
        action="材料补齐（缺口解除）", operator=operator.name,
        before={"gap_flag": True}, after={"gap_flag": False, "parse_status": "待解析"},
    )
    return row


def parse_material(
    db: Session, operator: User, material_id: str, *, ok: bool, vector_ref: str | None = None
) -> Material:
    """解析结果回写（worksheet.edit ≥ OPERATE）：待解析/失败 → 已解析/失败；缺口登记不可解析。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_material(db, material_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if row.gap_flag:
        raise ValueError(f"{CODE_PARAM} 材料未到位（缺口登记），不可解析（FR-03-01）")
    if row.parse_status == "已解析" and not ok:
        raise ValueError(f"{CODE_LOCKED} 已解析材料不可回退失败（重新登记后重试）")

    row.parse_status = "已解析" if ok else "失败"
    if ok and vector_ref and vector_ref.strip():
        row.vector_ref = vector_ref.strip()
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="material", entity_id=row.id,
        action="材料解析", operator=operator.name,
        after={"parse_status": row.parse_status, "vector_ref": row.vector_ref},
    )
    return row


def list_materials(db: Session, project_id: str) -> list[Material]:
    return list(db.scalars(
        select(Material).where(Material.project_id == project_id)
    ).all())


# ---------------------------------------------------------------------------
# FR-01-05 资料清单前置：立项 scope 快照 × 客户层材料实时匹配，缺口实时可见
# ---------------------------------------------------------------------------
def project_checklist(db: Session, operator: User, project_id: str) -> dict[str, Any]:
    """客户一次性资料清单（FR-01-05，worksheet.edit ≥ VIEW）。

    清单 = 建档圈定的行业模板 scope.materials 快照（FR-02-04 冻结于
    approval_summary，随版本升级不漂移）；到料状态 = 清单项名与客户层已到位
    材料 file_key 子串匹配（R6 命名口径的「类别」即清单项），一一对应、缺口
    实时可见。非高频矩阵行业清单为空注记，待计划阶段人工圈定。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.VIEW)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(f"{CODE_NOT_FOUND} 项目不存在")
    scope = (project.approval_summary or {}).get("template_scope") or {}
    items = list(scope.get("materials") or [])
    # 匹配口径用客户原始文件名（分类采信后 file_key 被重命名为 R6 归档名，原名仍保留）
    arrived_rows = [
        m for m in list_materials(db, project_id)
        if m.level == "customer" and not m.gap_flag
    ]
    rows = [
        {
            "item": it,
            "arrived": any(it in (m.origin_name or m.file_key) for m in arrived_rows),
            "matched_file_keys": [m.file_key for m in arrived_rows
                                  if it in (m.origin_name or m.file_key)],
        }
        for it in items
    ]
    missing = [r["item"] for r in rows if not r["arrived"]]
    return {
        "project_id": project_id,
        "industry_template": scope.get("industry_template"),
        "in_matrix": scope.get("in_matrix", False),
        "items": rows,
        "total": len(rows),
        "arrived_count": len(rows) - len(missing),
        "missing": missing,
        "complete": bool(rows) and not missing,
        "note": None if rows else "非高频矩阵行业，资料清单待计划阶段人工圈定",
    }


def batch_arrive_checklist(
    db: Session, operator: User, project_id: str, *, entries: list[dict[str, str]]
) -> dict[str, Any]:
    """客户按清单一次性补齐（FR-01-05，worksheet.edit ≥ OPERATE）。

    按清单项批量登记客户层材料（file_key 按 R6 命名携带清单项名），逐项留痕
    后整批复核留痕，并复算清单回显到料进度。
    """
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(f"{CODE_NOT_FOUND} 项目不存在")
    _assert_writable(project)
    if not entries:
        raise ValueError(f"{CODE_PARAM} 补齐项不可为空（按清单逐项登记）")
    registered = []
    for entry in entries:
        row = register_material(
            db, operator, project_id, level="customer", file_key=entry["file_key"]
        )
        registered.append({
            "item": entry.get("item"), "material_id": row.id, "file_key": row.file_key,
        })
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="checklist", entity_id=project_id,
        action="资料清单批量补齐（FR-01-05）", operator=operator.name,
        after={"items": len(registered)},
    )
    return {
        "registered": registered,
        "checklist": project_checklist(db, operator, project_id),  # 到料进度实时回显
    }


def list_gaps(db: Session, project_id: str) -> dict[str, Any]:
    """缺口清单（FR-03-01 边界场景：资料不齐 → 明确列出缺口待补）。"""
    rows = list_materials(db, project_id)
    gaps = [r for r in rows if r.gap_flag]
    missing_levels = [lv for lv in MATERIAL_LEVELS
                      if not any(r.level == lv and not r.gap_flag for r in rows)]
    return {
        "gaps": [
            {"id": r.id, "level": r.level, "file_key": r.file_key, "parse_status": r.parse_status}
            for r in gaps
        ],
        "missing_levels": missing_levels,  # 三层级中完全缺层的层级
        "complete": not gaps and not missing_levels,
    }


# ---------------------------------------------------------------------------
# 字段映射（§15-6：人工映射一次、同客户复用；创建时最低字段校验）
# ---------------------------------------------------------------------------
def create_mapping(
    db: Session, operator: User, *,
    client_id: str, source_system: str, mapping_type: str,
    field_mapping: dict[str, str], reuse_scope: str = "本客户",
) -> ImportFieldMapping:
    """创建字段映射（worksheet.edit ≥ OPERATE）：映射目标端必须覆盖最低字段（§15-6）。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    if mapping_type not in MAPPING_TYPES:
        raise ValueError(f"{CODE_PARAM} 映射类型非法：{mapping_type}（合法值：{MAPPING_TYPES}）")
    if not (source_system and source_system.strip()):
        raise ValueError(f"{CODE_PARAM} 来源系统必填")
    if reuse_scope not in ("本客户", "全所"):
        raise ValueError(f"{CODE_PARAM} 复用范围非法（本客户/全所）")
    if not field_mapping:
        raise ValueError(f"{CODE_PARAM} 字段映射不能为空")

    required = TRIAL_BALANCE_REQUIRED if mapping_type == "科目余额表" else VOUCHER_REQUIRED
    targets = set(field_mapping.values())
    missing = [f for f in required if f not in targets]
    if missing:
        raise ValueError(f"{CODE_PARAM} {mapping_type}映射目标端缺最低字段：{missing}（§15-6）")

    row = ImportFieldMapping(
        client_id=client_id, source_system=source_system.strip(), mapping_type=mapping_type,
        field_mapping=field_mapping, mapped_by=operator.id, reuse_scope=reuse_scope,
    )
    db.add(row)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="import_field_mapping", entity_id=row.id,
        action="创建字段映射", operator=operator.name,
        after={"client_id": client_id, "mapping_type": mapping_type,
               "fields": len(field_mapping), "reuse_scope": reuse_scope},
    )
    return row


def list_mappings(db: Session, client_id: str) -> list[ImportFieldMapping]:
    """同客户复用查询（§15-6：人工映射一次、同客户复用）。"""
    return list(db.scalars(
        select(ImportFieldMapping).where(ImportFieldMapping.client_id == client_id)
    ).all())


# ---------------------------------------------------------------------------
# FR-01-04 资料自动分类（决策档案目录 + 审计员确认；顺带接线 FR-01-03 R6 重命名）
# ---------------------------------------------------------------------------
# Phase 1 规则口径（大模型通道默认关闭，§15-15/硬约束 6）：关键词 → R6 档案目录，
# 顺序敏感（先命中先归）；无法识别 → QT（其他，待人工改判）。
_KEYWORD_CATALOG: tuple[tuple[tuple[str, ...], str], ...] = (
    (("约定书", "承接", "评价"), "CJ"),   # 业绩承接类
    (("报表", "附注", "总账", "明细账", "凭证"), "DG"),  # 底稿资料
    (("函证", "回函"), "HZ"),
    (("计划", "清单", "盘点"), "JH"),
    (("风险", "评估"), "FX"),
    (("内控", "制度"), "NK"),
    (("申报", "纳税", "税务"), "ZK"),
    (("报告", "意见书"), "BG"),
    (("归档", "档案"), "GD"),
)
_RULE_MODEL_VERSION = "rule-keyword-v1"


def _guess_catalog(name: str) -> str:
    for words, catalog in _KEYWORD_CATALOG:
        if any(w in name for w in words):
            return catalog
    return "QT"


def _detail_digest(detail: dict[str, Any]) -> str:
    """与 pipeline.seal_output 同口径（json.dumps sort_keys），确认时防篡改重算。"""
    return hashlib.sha256(
        json.dumps(detail, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def classify_materials(db: Session, operator: User, project_id: str) -> dict[str, Any]:
    """资料自动分类（FR-01-04，ai_output.confirm ≥ PROPOSE 即审计员可触发）。

    三段式前两段（红线 1/R2）：产物恒为「线索」态——按客户原始文件名关键词
    归入决策档案目录（R6 枚举），未经审计员确认不得重命名归档；
    10 字段留痕（AIRun）+ 线索明细 digest 封印，确认时防篡改校验。
    pgvector 预解析（vector_ref）待 Phase 2 接线，本域 parse_material 已留接口。
    """
    assert_allowed(operator.global_role, "ai_output.confirm", Level.PROPOSE)
    project = db.get(AuditProject, project_id)
    if project is None:
        raise NotFound(f"{CODE_NOT_FOUND} 项目不存在")
    _assert_writable(project)
    pending = [
        m for m in list_materials(db, project_id)
        if m.catalog is None and not m.gap_flag
    ]
    if not pending:
        raise ValueError(f"{CODE_PARAM} 无待分类材料（已分类或缺口登记均不参与）")

    run = pipeline.AIRun(
        project_id=project_id, module="ai_classify",
        skill_version="material-classify@1.0.0", model_version=_RULE_MODEL_VERSION,
        triggered_by=operator.id, input_material_ids=[],
    )
    entries = []
    for m in pending:
        name = m.origin_name or m.file_key
        catalog = _guess_catalog(name)
        reason = (f"关键词命中归入 {CATEGORY_CODES[catalog]}" if catalog != "QT"
                  else "无命中，待人工改判")
        pipeline.record_input(run, m.id, name.encode("utf-8"))
        entries.append({"material_id": m.id, "catalog": catalog, "reason": reason})
    detail = {"project_id": project_id, "entries": entries}
    pipeline.seal_output(run, detail, f"materials/{project_id}/classify-{run.run_no}.json")

    row = AIRunORM(
        run_no=run.run_no, project_id=project_id, module=run.module,
        skill_version=run.skill_version, model_version=run.model_version,
        triggered_by=run.triggered_by,
        input_material_ids=run.input_material_ids, input_hashes=run.input_hashes,
        output_digest=run.output_digest, output_key=run.output_key,
        status="线索", related_type="project", related_id=project_id,
    )
    db.add(row)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="ai_run", entity_id=row.id,
        action="资料自动分类（线索态）", operator=operator.name,
        after={"run_no": run.run_no, "items": len(entries), "digest": run.output_digest},
    )
    return {"run_no": run.run_no, "entries": entries, "detail": detail}


def confirm_classification(
    db: Session, operator: User, run_no: str, *,
    entries: list[dict[str, str]], conclusion: str,
    rejection_reason: str | None = None,
) -> AIRunORM:
    """分类确认（第三段人工标记，ai_output.confirm ≥ OPERATE）。

    采信：逐材料回写目录类别并按 R6 重命名归档（FR-01-03 命名引擎接线，
    系统生成人工不可改写）；不采信必附原因（§15-16）。
    digest 防篡改：确认明细与线索封印不一致即拒绝（确认一次性 append-only）。
    """
    assert_allowed(operator.global_role, "ai_output.confirm", Level.OPERATE)
    row = db.scalar(select(AIRunORM).where(AIRunORM.run_no == run_no))
    if row is None or row.module != "ai_classify":
        raise NotFound(f"{CODE_NOT_FOUND} 分类线索不存在")
    if row.status != "线索":
        raise Locked(f"{CODE_LOCKED} 确认一次性（append-only），禁止重复确认")
    project = db.get(AuditProject, row.project_id)
    _assert_writable(project)

    clean = [
        {"material_id": e["material_id"], "catalog": e["catalog"], "reason": e.get("reason", "")}
        for e in entries
    ]
    if _detail_digest(
        {"project_id": row.project_id, "entries": clean}
    ) != row.output_digest:
        raise Locked(f"{CODE_LOCKED} 确认明细与线索封印不一致（防篡改校验失败）")
    if conclusion == "不采信" and not (rejection_reason and rejection_reason.strip()):
        raise ValueError(f"{CODE_PARAM} 不采信必附原因（§15-16）")

    run = pipeline.AIRun(
        project_id=row.project_id, module=row.module,
        skill_version=row.skill_version, model_version=row.model_version,
        triggered_by=row.triggered_by,
        input_material_ids=list(row.input_material_ids or []),
        input_hashes=dict(row.input_hashes or {}),
        run_no=row.run_no, output_digest=row.output_digest or "",
        output_key=row.output_key or "",
    )
    pipeline.mark_review(run, operator.name, conclusion, rejection_reason)
    if conclusion != "不采信":
        pipeline.assert_can_enter_business(run, "决策档案目录")
        client = db.get(Client, project.client_id)
        year_match = re.search(r"\d{4}", project.period or "")
        year = int(year_match.group()) if year_match else _now().year
        for e in clean:
            if e["catalog"] not in CATEGORY_CODES:
                raise ValueError(f"{CODE_PARAM} 非法目录类别：{e['catalog']}")
            m = db.get(Material, e["material_id"])
            if m is None or m.project_id != row.project_id:
                raise NotFound(f"{CODE_NOT_FOUND} 材料不属于本项目：{e['material_id']}")
            old_key = m.file_key
            ext = os.path.splitext(m.origin_name or old_key)[1]
            m.catalog = e["catalog"]
            # FR-01-03 命名引擎接线：系统生成人工不可改写（R6），扩展名保留
            m.file_key = build_document_name(
                project.project_no, client.name, year, e["catalog"]
            ) + ext
            append_trail(
                db, tenant_id=operator.tenant_id, entity_type="material", entity_id=m.id,
                action="分类采信归档（R6 重命名）", operator=operator.name,
                before={"file_key": old_key},
                after={"catalog": e["catalog"], "file_key": m.file_key,
                       "origin_name": m.origin_name},
            )
    row.status = run.status
    row.reviewed_by = operator.id
    row.reviewed_at = _now()
    row.rejection_reason = run.rejection_reason
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="ai_run", entity_id=row.id,
        action=f"分类确认（{conclusion}）", operator=operator.name,
        before={"status": "线索"},
        after={"status": row.status, "rejection_reason": row.rejection_reason},
    )
    return row


# ---------------------------------------------------------------------------
# 凭证行导入（FR-03-04 全量异常评分数据池）
# ---------------------------------------------------------------------------
def import_voucher_lines(
    db: Session, operator: User, material_id: str, *, lines: list[dict[str, Any]]
) -> list[VoucherLine]:
    """导入明细账凭证行（worksheet.edit ≥ OPERATE）：材料须已解析；行级最低字段校验后落库。"""
    assert_allowed(operator.global_role, "worksheet.edit", Level.OPERATE)
    row = _get_material(db, material_id)
    _assert_writable(db.get(AuditProject, row.project_id))
    if row.gap_flag:
        raise ValueError(f"{CODE_PARAM} 材料未到位（缺口登记），不可导入")
    if row.parse_status != "已解析":
        raise ValueError(f"{CODE_PARAM} 材料状态「{row.parse_status}」未解析，不可导入（§15-6）")
    if not lines:
        raise ValueError(f"{CODE_PARAM} 凭证行不能为空")

    rows: list[VoucherLine] = []
    for idx, line in enumerate(lines, start=1):
        voucher_no = (line.get("voucher_no") or "").strip()
        account_code = (line.get("account_code") or "").strip()
        if not voucher_no or not account_code:
            raise ValueError(f"{CODE_PARAM} 第 {idx} 行缺最低字段：凭证号/科目编码必填（§15-6）")
        rows.append(VoucherLine(
            project_id=row.project_id, voucher_no=voucher_no,
            entry_date=line.get("entry_date"), summary=line.get("summary"),
            account_code=account_code, account_name=line.get("account_name"),
            debit=Decimal(str(line["debit"])) if line.get("debit") is not None else None,
            credit=Decimal(str(line["credit"])) if line.get("credit") is not None else None,
        ))
    db.add_all(rows)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="material", entity_id=row.id,
        action="凭证行导入", operator=operator.name,
        after={"file_key": row.file_key, "lines": len(rows),
               "vouchers": len({r.voucher_no for r in rows})},
    )
    return rows
