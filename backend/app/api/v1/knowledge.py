"""知识库域路由（§2.3 /knowledge）：FR-07-04 经验回流、§15-3 入库审批链。

路由层薄：只做鉴权与出入参，规则在 services/knowledge；错误码 KN-1xxx。
经验沉淀提案由 /quality/archive 归档动作自动触发，本域提供审批流与清单。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.knowledge import (
    ClauseContextOut,
    GraphBuildIn,
    GraphBuildOut,
    GraphOverviewOut,
    KnowledgeIn,
    KnowledgeOut,
    QaHistoryOut,
    QaIn,
    QaOut,
    RejectIn,
    SkillIn,
    SkillOut,
)
from ...services import knowledge

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _http_for(exc: Exception) -> HTTPException:
    """服务层领域异常 → KN-1xxx HTTP 映射（错误码按域分段）。"""
    if isinstance(exc, knowledge.NotFound):
        return HTTPException(404, f"KN-1002 {exc}")
    if isinstance(exc, knowledge.Locked):
        return HTTPException(409, str(exc))
    if isinstance(exc, ValueError):  # 参数拒绝（驳回缺理由等）
        return HTTPException(422, str(exc))
    return HTTPException(500, "KN-1999 内部错误")


@router.post("/items", response_model=KnowledgeOut, status_code=201,
             summary="知识写入（§15-3：审计员提案/经理提交/质控直接写入免自审，按角色落审批起点）")
def propose_item(
    body: KnowledgeIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> KnowledgeOut:
    try:
        row = knowledge.propose_item(
            db, user, item_type=body.item_type, content=body.content,
            rule_anchor_id=body.rule_anchor_id,
        )
    except ValueError as exc:
        raise _http_for(exc) from exc
    return KnowledgeOut.model_validate(row)


@router.get("/items", response_model=list[KnowledgeOut],
            summary="知识条目清单（审批状态透明，支持状态/类型/来源项目过滤）")
def list_items(
    status: str | None = None,
    item_type: str | None = None,
    source_project_id: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[KnowledgeOut]:
    return [
        KnowledgeOut.model_validate(r)
        for r in knowledge.list_items(
            db, status=status, item_type=item_type, source_project_id=source_project_id
        )
    ]


@router.post("/items/{item_id}/review", response_model=KnowledgeOut,
             summary="质控审核（§15-3：审核他人提交 → 待合伙人批准；免自审条目无需审核）")
def review_item(
    item_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> KnowledgeOut:
    try:
        row = knowledge.review_item(db, user, item_id)
    except (knowledge.NotFound, knowledge.Locked) as exc:
        raise _http_for(exc) from exc
    return KnowledgeOut.model_validate(row)


@router.post("/items/{item_id}/approve", response_model=KnowledgeOut,
             summary="合伙人批准发布（§15-3：保留驳回权行使点 → 已发布）")
def approve_item(
    item_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> KnowledgeOut:
    try:
        row = knowledge.approve_item(db, user, item_id)
    except (knowledge.NotFound, knowledge.Locked) as exc:
        raise _http_for(exc) from exc
    return KnowledgeOut.model_validate(row)


@router.post("/items/{item_id}/reject", response_model=KnowledgeOut,
             summary="合伙人驳回（必附理由，理由随条目留痕 → 已驳回终态）")
def reject_item(
    item_id: str,
    body: RejectIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> KnowledgeOut:
    try:
        row = knowledge.reject_item(db, user, item_id, reject_reason=body.reject_reason)
    except (knowledge.NotFound, knowledge.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return KnowledgeOut.model_validate(row)


@router.post("/qa", response_model=QaOut, status_code=201,
             summary="准则 RAG 问答（FR-07-01：任一角色；答案强制带准则锚点引用，未覆盖明示）")
def ask_question(
    body: QaIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QaOut:
    try:
        data = knowledge.ask_rule_question(
            db, user, project_id=body.project_id, question=body.question
        )
    except (knowledge.NotFound, ValueError) as exc:
        raise _http_for(exc) from exc
    return QaOut.model_validate(data)


@router.get("/qa", response_model=list[QaHistoryOut],
            summary="问答历史（问答记录可追溯：按留痕回放，append-only）")
def qa_history(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[QaHistoryOut]:
    try:
        rows = knowledge.qa_history(db, project_id)
    except knowledge.NotFound as exc:
        raise _http_for(exc) from exc
    return [QaHistoryOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# FR-07-03 Skill 统一管理（skill.publish：批准|提案|无|审核）
# ---------------------------------------------------------------------------
@router.get("/skills", response_model=list[SkillOut],
            summary="Skill 清单（含未审核版本；无权限者不可见，PRD FR-07-03）")
def list_skills(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[SkillOut]:
    return [SkillOut.model_validate(a) for a in knowledge.list_skills(db, user)]


@router.post("/skills", response_model=SkillOut, status_code=201,
             summary="Skill 提案（FR-07-05 构建期静态扫描：含违规路径/凭证即拒绝）")
def propose_skill(
    body: SkillIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SkillOut:
    try:
        asset = knowledge.propose_skill(
            db, user, name=body.name, version=body.version,
            zip_key=body.zip_key, manifest=body.manifest,
        )
    except (knowledge.NotFound, knowledge.Locked, ValueError) as exc:
        raise _http_for(exc) from exc
    return SkillOut.model_validate(asset)


@router.post("/skills/{skill_id}/review", response_model=SkillOut,
             summary="质控审核（一次审核全所共用，落审核人）")
def review_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SkillOut:
    try:
        asset = knowledge.review_skill(db, user, skill_id)
    except (knowledge.NotFound, knowledge.Locked) as exc:
        raise _http_for(exc) from exc
    return SkillOut.model_validate(asset)


@router.post("/skills/{skill_id}/publish", response_model=SkillOut,
             summary="合伙人批准发布（先审后批；发布即写版本链）")
def publish_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SkillOut:
    try:
        asset = knowledge.publish_skill(db, user, skill_id)
    except (knowledge.NotFound, knowledge.Locked) as exc:
        raise _http_for(exc) from exc
    return SkillOut.model_validate(asset)


@router.post("/skills/{skill_id}/rollback", response_model=SkillOut,
             summary="版本回滚（当前版本下线，恢复发布链上一版本）")
def rollback_skill(
    skill_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SkillOut:
    try:
        asset = knowledge.rollback_skill(db, user, skill_id)
    except (knowledge.NotFound, knowledge.Locked) as exc:
        raise _http_for(exc) from exc
    return SkillOut.model_validate(asset)


# ---------------------------------------------------------------------------
# FR-07-02 知识图谱（固定路径防幻觉；版本化随准则库更新）
# ---------------------------------------------------------------------------
@router.post("/graph/build", response_model=GraphBuildOut, status_code=201,
             summary="图谱构建（准则库更新时：全量替换显式关联边集，版本=边集指纹）")
def build_graph(
    body: GraphBuildIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GraphBuildOut:
    try:
        data = knowledge.build_rule_graph(
            db, user, relations=[r.model_dump() for r in body.relations]
        )
    except (knowledge.NotFound, ValueError) as exc:
        raise _http_for(exc) from exc
    return GraphBuildOut.model_validate(data)


@router.get("/graph", response_model=GraphOverviewOut,
            summary="图谱概览（当前版本节点摘要 + 显式关联边）")
def graph_overview(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GraphOverviewOut:
    return GraphOverviewOut.model_validate(knowledge.graph_overview(db, user))


@router.get("/graph/clause/{anchor_id}", response_model=ClauseContextOut,
            summary="条款上下文（上下位法与关联法规，仅显式关联固定路径防幻觉）")
def clause_context(
    anchor_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ClauseContextOut:
    try:
        data = knowledge.clause_context(db, user, anchor_id)
    except knowledge.NotFound as exc:
        raise _http_for(exc) from exc
    return ClauseContextOut.model_validate(data)
