"""知识库域业务逻辑（FR-07-04 经验回流 + §15-3 入库审批链；PRD §5.7、§15-16）。

规则落点：
- 经验回流（FR-07-04）：项目归档通过后自动触发（quality.archive_project 收口时调用），
  纯规则汇总生成经验沉淀提案——新底稿模式 / 典型问题与处理结论 / AI 不采信脱敏错误样例
  （§15-16：脱敏后随提案入知识库，质控按季度分析）；产物恒为「提案」态（硬约束 3 同源逻辑）；
- 入库审批链（§15-3）：审计员提案 / 项目经理提交 / 质控直接写入（免自审）→ 质控审核 →
  合伙人批准发布；合伙人保留驳回权（驳回必附理由）；权限卡控走 knowledge.write 配置化矩阵；
- 沉淀为纯 Python 汇总，不经大模型通道（§15-15 默认关闭）；留痕一律 append_trail 续链（硬约束 4）。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.audit_middleware import append_trail
from ...core.permissions import Level, assert_allowed
from ...models.audit import AIRun
from ...models.finding import Finding
from ...models.knowledge import KnowledgeItem, RuleAnchor, RuleRelation, SkillAsset
from ...models.project import AuditProject
from ...models.user import User
from ...models.worksheet import Worksheet

# KN-1xxx 知识域错误码
CODE_NOT_FOUND = "KN-1002"
CODE_LOCKED = "KN-1006"  # 审批状态流转非法（终态/越段）
CODE_PARAM = "KN-1007"  # 参数非法（如驳回缺理由）

EXPERIENCE_ITEM_TYPE = "本所沉淀经验"

# 入库审批状态机（§15-3；服务层单点实现——知识审批流不在 engine 四状态机清单内）
_KN_TRANSITIONS: dict[str, dict[str, str]] = {
    "review": {"提案": "待合伙人批准", "质控审核中": "待合伙人批准"},
    "approve": {"待合伙人批准": "已发布"},
    "reject": {"待合伙人批准": "已驳回"},
}


class NotFound(Exception):
    pass


class Locked(Exception):
    pass


def _transit(db: Session, operator: User, item: KnowledgeItem, action: str, *, reviewer_field: str) -> KnowledgeItem:
    """审批状态流转单点：越段/终态一律 Locked（KN-1006），流转后留痕续链。"""
    allowed = _KN_TRANSITIONS[action]
    if item.status not in allowed:
        raise Locked(f"{CODE_LOCKED} 状态「{item.status}」不可执行「{action}」（合法来源：{tuple(allowed)}）")
    before = {"status": item.status}
    item.status = allowed[item.status]
    setattr(item, reviewer_field, operator.id)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="knowledge_item", entity_id=item.id,
        action=f"知识条目{action}", operator=operator.name, before=before,
        after={"status": item.status},
    )
    return item


# ---------------------------------------------------------------------------
# FR-07-04 经验回流：归档通过后自动触发的经验沉淀提案（纯规则汇总，脱敏）
# ---------------------------------------------------------------------------
def _collect_experience_payloads(db: Session, project: AuditProject) -> list[dict]:
    """归档数据纯规则汇总：新底稿模式 / 典型问题与处理结论 / AI 不采信脱敏样例。

    脱敏口径（§15-16）：错误样例仅保留模块、原因分类与次数，不含人名、客户名、金额。
    """
    payloads: list[dict] = []

    ws_rows = db.scalars(select(Worksheet).where(Worksheet.project_id == project.id)).all()
    if ws_rows:
        payloads.append({
            "沉淀类别": "新底稿模式",
            "行业": project.business_type,
            "底稿数": len(ws_rows),
            "重要底稿": sorted(w.code for w in ws_rows if w.is_important),
        })

    finding_rows = db.scalars(select(Finding).where(Finding.project_id == project.id)).all()
    if finding_rows:
        payloads.append({
            "沉淀类别": "典型问题与处理结论",
            "问题清单": [
                {"description": f.description, "处理状态": f.status} for f in finding_rows
            ],
        })

    rejected = db.scalars(
        select(AIRun).where(AIRun.project_id == project.id, AIRun.status == "不采信")
    ).all()
    if rejected:
        counter = Counter((r.module, r.rejection_reason or "其他") for r in rejected)
        payloads.append({
            "沉淀类别": "AI 错误样例（脱敏）",
            "样例": [
                {"module": module, "不采信原因": reason, "次数": count}
                for (module, reason), count in sorted(counter.items())
            ],
        })
    return payloads


def seed_experience_proposals(
    db: Session, operator: User, project: AuditProject
) -> list[KnowledgeItem]:
    """归档通过后自动触发（FR-07-04）：生成经验沉淀提案（「提案」态，进入入库审批流）。

    由 quality.archive_project 在双状态机收口后调用（项目归档通过才沉淀）；
    有内容才建条目，无沉淀数据不产生空提案。
    """
    items: list[KnowledgeItem] = []
    for payload in _collect_experience_payloads(db, project):
        item = KnowledgeItem(
            item_type=EXPERIENCE_ITEM_TYPE,
            content=json.dumps(payload, ensure_ascii=False),
            source_project_id=project.id,
            status="提案", proposed_by=operator.id,
        )
        db.add(item)
        items.append(item)
    db.flush()
    for item in items:
        append_trail(
            db, tenant_id=operator.tenant_id, entity_type="knowledge_item", entity_id=item.id,
            action="经验沉淀提案（FR-07-04）", operator=operator.name,
            after={"status": "提案", "source_project": project.project_no},
        )
    return items


# ---------------------------------------------------------------------------
# §15-3 知识入库审批链：提案/提交/直接写入 → 质控审核 → 合伙人批准发布（驳回必附理由）
# ---------------------------------------------------------------------------
def propose_item(
    db: Session, operator: User, *, item_type: str, content: str,
    rule_anchor_id: str | None = None,
) -> KnowledgeItem:
    """知识写入（knowledge.write ≥ PROPOSE）：按角色落不同审批起点（§15-3）。

    审计员提案→「提案」；项目经理提交→「质控审核中」；
    质控专员直接写入（免自审）/合伙人→「待合伙人批准」。
    """
    assert_allowed(operator.global_role, "knowledge.write", Level.PROPOSE)
    if operator.global_role == "审计员":
        status = "提案"
    elif operator.global_role == "项目经理":
        status = "质控审核中"  # 提交即入审核队列（经审核）
    else:  # 质控免自审直接写入；合伙人自撰同样径直待批（保留驳回权在批准环节行使）
        status = "待合伙人批准"
    item = KnowledgeItem(
        item_type=item_type, content=content, rule_anchor_id=rule_anchor_id,
        status=status, proposed_by=operator.id,
    )
    db.add(item)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="knowledge_item", entity_id=item.id,
        action="知识条目写入", operator=operator.name,
        after={"status": status, "item_type": item_type},
    )
    return item


def review_item(db: Session, operator: User, item_id: str) -> KnowledgeItem:
    """质控审核（knowledge.write ≥ OPERATE，审核他人提交）：→「待合伙人批准」。"""
    assert_allowed(operator.global_role, "knowledge.write", Level.OPERATE)
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise NotFound(CODE_NOT_FOUND)
    return _transit(db, operator, item, "review", reviewer_field="reviewed_by")


def approve_item(db: Session, operator: User, item_id: str) -> KnowledgeItem:
    """合伙人批准发布（knowledge.write=APPROVE，保留驳回权行使点）：→「已发布」。"""
    assert_allowed(operator.global_role, "knowledge.write", Level.APPROVE)
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise NotFound(CODE_NOT_FOUND)
    item.approved_by = operator.id
    return _transit(db, operator, item, "approve", reviewer_field="approved_by")


def reject_item(db: Session, operator: User, item_id: str, *, reject_reason: str) -> KnowledgeItem:
    """合伙人驳回（knowledge.write=APPROVE）：必附理由（KN-1007），→「已驳回」终态。"""
    assert_allowed(operator.global_role, "knowledge.write", Level.APPROVE)
    if not reject_reason or not reject_reason.strip():
        raise ValueError(f"{CODE_PARAM} 驳回必附理由（合伙人保留驳回权，理由随条目留痕）")
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise NotFound(CODE_NOT_FOUND)
    item.reject_reason = reject_reason
    return _transit(db, operator, item, "reject", reviewer_field="approved_by")


def list_items(
    db: Session, *, status: str | None = None, item_type: str | None = None,
    source_project_id: str | None = None,
) -> list[KnowledgeItem]:
    """知识条目清单（knowledge.write ≥ VIEW：全角色可见，审批状态透明）。"""
    stmt = select(KnowledgeItem)
    if status:
        stmt = stmt.where(KnowledgeItem.status == status)
    if item_type:
        stmt = stmt.where(KnowledgeItem.item_type == item_type)
    if source_project_id:
        stmt = stmt.where(KnowledgeItem.source_project_id == source_project_id)
    return list(db.scalars(stmt.order_by(KnowledgeItem.created_at)).all())


# ---------------------------------------------------------------------------
# FR-07-01 准则 RAG 问答（ai/rag.py 检索单点；本层只做项目存在性与参数校验）
# ---------------------------------------------------------------------------
def ask_rule_question(
    db: Session, operator: User, *, project_id: str, question: str
) -> dict:
    """准则问答（任一角色，PRD §5.7）：项目上下文必填（问答留痕归属项目）。

    引用门禁与留痕单点在 ai/rag.answer_question（硬约束 6：无引用不出答案）。
    """
    from ...ai import rag  # 延迟导入避免 ai↔services 环

    if db.get(AuditProject, project_id) is None:
        raise NotFound(CODE_NOT_FOUND)
    if not question or not question.strip():
        raise ValueError(f"{CODE_PARAM} 问题不可为空")
    return rag.answer_question(db, operator, project_id=project_id, question=question.strip())


def qa_history(db: Session, project_id: str) -> list[dict]:
    """问答历史回放（问答记录可追溯，PRD §5.7）：项目存在性校验后按留痕反查。"""
    from ...ai import rag

    if db.get(AuditProject, project_id) is None:
        raise NotFound(CODE_NOT_FOUND)
    return rag.qa_history(db, project_id)


# ---------------------------------------------------------------------------
# FR-07-03 Skill 统一管理（skill.publish 权限码：批准|提案|无|审核）
# 提案（经理 PROPOSE，含 FR-07-05 构建期静态扫描）→ 质控审核（OPERATE，记审核人）
# → 合伙人批准（APPROVE，先审后批转已发布）→ 版本化回滚；未审核版本对无权限者不可见
# ---------------------------------------------------------------------------
# FR-07-05 构建期拦截：Skill 内禁含局域网路径 / IP / 凭证（违规即拒绝提案并留违规清单）
_SCAN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("局域网路径", re.compile(r"\\\\|//[a-z0-9.-]+/(?:share|data|mnt)", re.IGNORECASE)),
    ("盘符路径", re.compile(r"[a-zA-Z]:\\\\")),
    ("IP 地址", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("凭证关键词", re.compile(r"(?:password|passwd|secret|api[_-]?key|token)\s*[:=]", re.IGNORECASE)),
)


def _scan_skill_manifest(manifest: str) -> dict[str, Any]:
    """静态扫描（构建期拦截单点，FR-07-05）：返回违规清单；空清单=通过。"""
    violations = [label for label, pat in _SCAN_PATTERNS if pat.search(manifest or "")]
    return {"violations": violations, "passed": not violations}


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def propose_skill(
    db: Session, operator: User, *, name: str, version: str, zip_key: str,
    manifest: str = "",
) -> SkillAsset:
    """Skill 提案（skill.publish ≥ PROPOSE）：静态扫描通过才入审核流（FR-07-05）。

    违规不清场直接拒绝提案（ValueError，构建期拦截），扫描结果随留痕存档；
    未审核版本对无权限者（审计员 NONE）不可见——清单与详情均挂权限码。
    """
    assert_allowed(operator.global_role, "skill.publish", Level.PROPOSE)
    if db.scalar(select(SkillAsset.id).where(
            SkillAsset.name == name, SkillAsset.version == version)) is not None:
        raise Locked(f"{CODE_LOCKED} Skill 版本已存在（{name}@{version}）")
    scan = _scan_skill_manifest(manifest)
    if not scan["passed"]:
        raise ValueError(f"{CODE_PARAM} 构建期拦截（FR-07-05）：Skill 含违规内容 {scan['violations']}")
    asset = SkillAsset(
        name=name, version=version, zip_key=zip_key,
        review_status="待审核", scan_result=scan, rollback_chain=[],
    )
    db.add(asset)
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="skill_asset", entity_id=asset.id,
        action="Skill 提案", operator=operator.name,
        after={"name": name, "version": version, "scan": scan},
    )
    return asset


def review_skill(db: Session, operator: User, skill_id: str) -> SkillAsset:
    """质控审核（skill.publish ≥ OPERATE）：一次审核全所共用；审核记录落审核人。"""
    assert_allowed(operator.global_role, "skill.publish", Level.OPERATE)
    asset = db.get(SkillAsset, skill_id)
    if asset is None:
        raise NotFound(CODE_NOT_FOUND)
    if asset.review_status != "待审核":
        raise Locked(f"{CODE_LOCKED} 仅待审核状态可审核（当前 {asset.review_status}）")
    asset.reviewed_by = operator.id
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="skill_asset", entity_id=asset.id,
        action="Skill 质控审核通过", operator=operator.name,
        after={"name": asset.name, "version": asset.version},
    )
    return asset


def publish_skill(db: Session, operator: User, skill_id: str) -> SkillAsset:
    """合伙人批准发布（skill.publish ≥ APPROVE）：先审后批（未经质控审核不得发布）。

    发布即写版本发布链（rollback_chain 追加），同批 Skill 一次审核全所共用。
    """
    assert_allowed(operator.global_role, "skill.publish", Level.APPROVE)
    asset = db.get(SkillAsset, skill_id)
    if asset is None:
        raise NotFound(CODE_NOT_FOUND)
    if asset.review_status == "已发布":
        raise Locked(f"{CODE_LOCKED} 已发布，无需重复发布")
    if asset.review_status == "已下线":
        raise Locked(f"{CODE_LOCKED} 已下线版本不可直接发布，请走重新提案")
    if asset.reviewed_by is None:
        raise Locked(f"{CODE_LOCKED} 先审后批：未经质控审核不得发布（FR-07-03）")
    asset.review_status = "已发布"
    asset.approved_by = operator.id
    chain = list(asset.rollback_chain or [])
    chain.append({"version": asset.version, "published_at": _now().isoformat(),
                  "approved_by": operator.name})
    asset.rollback_chain = chain
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="skill_asset", entity_id=asset.id,
        action="Skill 批准发布", operator=operator.name,
        after={"name": asset.name, "version": asset.version, "chain_len": len(chain)},
    )
    return asset


def rollback_skill(db: Session, operator: User, skill_id: str) -> SkillAsset:
    """版本回滚（skill.publish ≥ OPERATE）：当前版本下线，恢复发布链上一版本。"""
    assert_allowed(operator.global_role, "skill.publish", Level.OPERATE)
    asset = db.get(SkillAsset, skill_id)
    if asset is None:
        raise NotFound(CODE_NOT_FOUND)
    if asset.review_status != "已发布":
        raise Locked(f"{CODE_LOCKED} 仅已发布版本可回滚（当前 {asset.review_status}）")
    asset.review_status = "已下线"
    prev = db.scalar(  # 链上一版本：同 name 创建序的最近一版（发布链口径）
        select(SkillAsset)
        .where(SkillAsset.name == asset.name, SkillAsset.id != asset.id)
        .order_by(SkillAsset.created_at.desc())
    )
    if prev is not None and prev.review_status != "待审核":
        # 未发布过的提案不随回滚生效；已下线的恢复发布、已发布的保持生效
        if prev.review_status == "已下线":
            prev.review_status = "已发布"
        chain = list(prev.rollback_chain or [])
        chain.append({"version": prev.version, "published_at": _now().isoformat(),
                      "approved_by": f"回滚恢复（{operator.name}）"})
        prev.rollback_chain = chain
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="skill_asset", entity_id=asset.id,
        action="Skill 版本回滚", operator=operator.name,
        after={"下线": asset.version, "恢复": prev.version if prev else None},
    )
    return asset


def list_skills(db: Session, operator: User) -> list[SkillAsset]:
    """Skill 清单（skill.publish ≥ VIEW）：审计员（NONE）不可见，含未审核版本。"""
    assert_allowed(operator.global_role, "skill.publish", Level.VIEW)
    return list(db.scalars(select(SkillAsset).order_by(SkillAsset.created_at)).all())


# ---------------------------------------------------------------------------
# FR-07-02 知识图谱（法规关联固定路径串联，防幻觉；图谱版本化随准则库更新）
# ---------------------------------------------------------------------------
_RELATION_TYPES = ("上下位", "引用", "关联")


def _graph_version(relations: list[dict[str, str]]) -> str:
    """图谱版本 = 边集指纹（随准则库更新而变化，出证口径同归档包 sha256）。"""
    import hashlib

    fingerprint = json.dumps(
        sorted((r["rule_from_id"], r["rule_to_id"], r["relation_type"]) for r in relations),
        ensure_ascii=False,
    )
    return f"gv-{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:8]}"


def build_rule_graph(
    db: Session, operator: User, *, relations: list[dict[str, str]]
) -> dict[str, Any]:
    """图谱构建（knowledge.write ≥ OPERATE，准则库更新时触发）：全量替换当前版本边集。

    邻接边只存显式关联（RuleRelation 三类：上下位/引用/关联），版本=边集指纹；
    构建动作 append_trail 续链（版本化可追溯，PRD FR-07-03 同款留痕口径）。
    """
    assert_allowed(operator.global_role, "knowledge.write", Level.OPERATE)
    if not relations:
        raise ValueError(f"{CODE_PARAM} 边集不可为空（图谱构建需显式关联）")
    clean: list[dict[str, str]] = []
    for r in relations:
        if r.get("relation_type") not in _RELATION_TYPES:
            raise ValueError(f"{CODE_PARAM} 非法关系类型：{r.get('relation_type')}")
        if db.get(RuleAnchor, r.get("rule_from_id", "")) is None or db.get(
                RuleAnchor, r.get("rule_to_id", "")) is None:
            raise NotFound(f"{CODE_NOT_FOUND} 边端点锚点不存在")
        clean.append({
            "rule_from_id": r["rule_from_id"],
            "rule_to_id": r["rule_to_id"],
            "relation_type": r["relation_type"],
        })
    version = _graph_version(clean)
    # 全量替换（图谱随准则库更新重建；graph_version 随行标注）
    for row in db.scalars(select(RuleRelation)).all():
        db.delete(row)
    for r in clean:
        db.add(RuleRelation(graph_version=version, **r))
    db.flush()
    append_trail(
        db, tenant_id=operator.tenant_id, entity_type="rule_graph", entity_id=version,
        action="知识图谱构建", operator=operator.name,
        after={"nodes": len({r["rule_from_id"] for r in clean} | {r["rule_to_id"] for r in clean}),
               "edges": len(clean)},
    )
    return {
        "version": version,
        "node_count": len({r["rule_from_id"] for r in clean} | {r["rule_to_id"] for r in clean}),
        "edge_count": len(clean),
    }


def _anchor_brief(db: Session, anchor_id: str) -> dict[str, str]:
    a = db.get(RuleAnchor, anchor_id)
    return {
        "id": a.id, "standard_no": a.standard_no, "clause": a.clause, "purpose": a.purpose,
    }


def clause_context(db: Session, operator: User, anchor_id: str) -> dict[str, Any]:
    """条款上下文（任一角色，RAG 同源口径）：从该条款出发的固定路径单跳视图。

    防幻觉（FR-07-02）：仅返回 RuleRelation 显式关联，按关系类型分类，
    不做传递闭包推断——无显式边的法规不会出现在结果中。
    """
    anchor = db.get(RuleAnchor, anchor_id)
    if anchor is None:
        raise NotFound(CODE_NOT_FOUND)
    out_edges = db.scalars(
        select(RuleRelation).where(RuleRelation.rule_from_id == anchor_id)
    ).all()
    in_edges = db.scalars(
        select(RuleRelation).where(RuleRelation.rule_to_id == anchor_id)
    ).all()

    def briefs(edges: list[RuleRelation], side: str) -> list[dict[str, str]]:
        return [_anchor_brief(db, getattr(e, side)) for e in edges]

    return {
        "anchor": _anchor_brief(db, anchor_id),
        "version": next((e.graph_version for e in (*out_edges, *in_edges)), None),
        # 上下位（from=上位法）：我指向的=下位法，指向我的=上位法
        "lower": briefs([e for e in out_edges if e.relation_type == "上下位"], "rule_to_id"),
        "upper": briefs([e for e in in_edges if e.relation_type == "上下位"], "rule_from_id"),
        # 引用：我引用的 / 引用我的；关联：双向（逐边取对端）
        "citing": briefs([e for e in out_edges if e.relation_type == "引用"], "rule_to_id"),
        "cited_by": briefs([e for e in in_edges if e.relation_type == "引用"], "rule_from_id"),
        "related": [
            _anchor_brief(db, e.rule_to_id if e.rule_from_id == anchor_id else e.rule_from_id)
            for e in (*out_edges, *in_edges) if e.relation_type == "关联"
        ],
        "note": "仅显式关联固定路径（防幻觉），不做传递推断",
    }


def graph_overview(db: Session, operator: User) -> dict[str, Any]:
    """当前图谱概览：版本 + 节点摘要 + 边列表（前端图谱视图数据源）。"""
    assert_allowed(operator.global_role, "knowledge.write", Level.VIEW)
    edges = db.scalars(select(RuleRelation)).all()
    anchor_ids = sorted({e.rule_from_id for e in edges} | {e.rule_to_id for e in edges})
    return {
        "version": edges[0].graph_version if edges else None,
        "nodes": [_anchor_brief(db, a) for a in anchor_ids],
        "edges": [
            {"rule_from_id": e.rule_from_id, "rule_to_id": e.rule_to_id,
             "relation_type": e.relation_type}
            for e in edges
        ],
    }
