"""知识库域出入参 DTO（FR-07-04 经验回流、§15-3 入库审批链、FR-07-01 准则问答）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

KnowledgeItemType = Literal["准则", "应用指南", "监管规定", "中注协底稿指南", "本所沉淀经验"]


class KnowledgeIn(BaseModel):
    item_type: KnowledgeItemType
    content: str = Field(min_length=2, description="知识内容（结构化 JSON 或文本）")
    rule_anchor_id: str | None = Field(default=None, max_length=36, description="关联准则锚点")


class RejectIn(BaseModel):
    reject_reason: str = Field(min_length=2, max_length=200, description="驳回理由（合伙人保留驳回权）")


class KnowledgeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    item_type: str
    content: str
    vector_ref: str | None = None
    rule_anchor_id: str | None = None
    source_project_id: str | None = None
    status: str
    proposed_by: str | None = None
    reviewed_by: str | None = None
    approved_by: str | None = None
    reject_reason: str | None = None


# ---------------------------------------------------------------------------
# FR-07-01 准则 RAG 问答（硬约束 6：答案强制带准则锚点引用，未覆盖明示）
# ---------------------------------------------------------------------------
class QaIn(BaseModel):
    project_id: str = Field(min_length=1, description="工作台项目上下文（问答留痕归属）")
    question: str = Field(min_length=2, max_length=500, description="执业问题")


class CitationOut(BaseModel):
    source: str
    ref: str
    score: float
    standard_no: str | None = None
    clause: str | None = None
    purpose: str | None = None
    content: str | None = None


class QaOut(BaseModel):
    run_no: str
    question: str
    answer: str
    citations: list[CitationOut]
    covered: bool


class QaHistoryOut(BaseModel):
    question: str
    answer: str
    citations: list[dict[str, Any]]
    covered: bool
    run_no: str | None = None
    operator: str
    asked_at: datetime | None = None


# ---------------------------------------------------------------------------
# FR-07-03 Skill 统一管理（提案→质控审核→合伙人批准发布→回滚；FR-07-05 静态扫描）
# ---------------------------------------------------------------------------
class SkillIn(BaseModel):
    name: str = Field(min_length=2, max_length=100, description="Skill 名称（audit-*/ai-*）")
    version: str = Field(min_length=1, max_length=20)
    zip_key: str = Field(min_length=1, max_length=200, description="受控 zip 对象键")
    manifest: str = Field(default="", max_length=20000, description="Skill 清单文本（构建期静态扫描对象）")


class SkillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: str
    zip_key: str
    review_status: str
    reviewed_by: str | None = None
    approved_by: str | None = None
    scan_result: dict[str, Any] | None = None
    rollback_chain: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# FR-07-02 知识图谱（固定路径防幻觉；图谱版本化随准则库更新）
# ---------------------------------------------------------------------------
class GraphEdgeIn(BaseModel):
    rule_from_id: str = Field(min_length=1, description="起点锚点（上下位关系中为上位法）")
    rule_to_id: str = Field(min_length=1)
    relation_type: Literal["上下位", "引用", "关联"]


class GraphBuildIn(BaseModel):
    relations: list[GraphEdgeIn] = Field(min_length=1)


class AnchorBriefOut(BaseModel):
    id: str
    standard_no: str
    clause: str
    purpose: str


class GraphBuildOut(BaseModel):
    version: str
    node_count: int
    edge_count: int


class GraphEdgeOut(BaseModel):
    rule_from_id: str
    rule_to_id: str
    relation_type: str


class GraphOverviewOut(BaseModel):
    version: str | None = None
    nodes: list[AnchorBriefOut]
    edges: list[GraphEdgeOut]


class ClauseContextOut(BaseModel):
    anchor: AnchorBriefOut
    version: str | None = None
    upper: list[AnchorBriefOut] = []
    lower: list[AnchorBriefOut] = []
    citing: list[AnchorBriefOut] = []
    cited_by: list[AnchorBriefOut] = []
    related: list[AnchorBriefOut] = []
    note: str
