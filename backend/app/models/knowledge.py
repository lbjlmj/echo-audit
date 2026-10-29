"""模板与知识资产（FR-07；PRD §15-1/3/13 去品牌化、入库审批链、Skill 受控发布）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONField, PKMixin, TenantMixin, TimestampMixin


class Template(PKMixin, TenantMixin, TimestampMixin, Base):
    """行业模板矩阵（数据即代码，随版本发布；类型×版本唯一）。"""

    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("type", "version", name="uq_template_type_version"),)

    type: Mapped[str] = mapped_column(String(50), comment="行业类型")
    version: Mapped[str] = mapped_column(String(20), comment="模板版本")
    skeleton_key: Mapped[str] = mapped_column(String(200), comment="底稿骨架对象键")
    program_mapping: Mapped[dict[str, Any] | None] = mapped_column(
        JSONField, comment="程序包默认映射（JSONB）"
    )


class RuleAnchor(PKMixin, TenantMixin, TimestampMixin, Base):
    """准则锚点（准则映射总表的库化）：准则号+条款+程序目的，Worksheet 不可空外键。"""

    __tablename__ = "rule_anchors"
    __table_args__ = (UniqueConstraint("standard_no", "clause", name="uq_anchor_std_clause"),)

    standard_no: Mapped[str] = mapped_column(String(20), comment="准则号")
    clause: Mapped[str] = mapped_column(String(50), comment="条款")
    purpose: Mapped[str] = mapped_column(String(200), comment="程序目的")


class RuleRelation(PKMixin, TenantMixin, Base):
    """法规关联邻接表（FR-07-02 知识图谱存储；路径固定防幻觉）。"""

    __tablename__ = "rule_relations"
    __table_args__ = (
        CheckConstraint("relation_type IN ('上下位','引用','关联')", name="ck_relation_type"),
    )

    rule_from_id: Mapped[str] = mapped_column(String(36), ForeignKey("rule_anchors.id"), index=True)
    rule_to_id: Mapped[str] = mapped_column(String(36), ForeignKey("rule_anchors.id"), index=True)
    relation_type: Mapped[str] = mapped_column(String(10), comment="关系类型")
    graph_version: Mapped[str] = mapped_column(String(20), comment="图谱版本")


class KnowledgeItem(PKMixin, TenantMixin, TimestampMixin, Base):
    """知识条目：入库链=提案/提交→质控审核→合伙人批准发布（§15-3）。

    质控专员直接写入免自审，合伙人保留驳回权；不内置外部机构受版权内容（§15-1 去品牌化）。
    来源项目非空=本所沉淀经验回流（FR-07-04）。
    """

    __tablename__ = "knowledge_items"
    __table_args__ = (
        CheckConstraint(
            "item_type IN ('准则','应用指南','监管规定','中注协底稿指南','本所沉淀经验')",
            name="ck_kn_type",
        ),
        CheckConstraint(
            "status IN ('提案','质控审核中','待合伙人批准','已发布','已驳回')", name="ck_kn_status"
        ),
    )

    item_type: Mapped[str] = mapped_column(String(20), comment="类型（§3.2 五类）")
    content: Mapped[str] = mapped_column(comment="内容")
    vector_ref: Mapped[str | None] = mapped_column(String(200), comment="向量引用（pgvector）")
    rule_anchor_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("rule_anchors.id"), comment="关联准则锚点"
    )
    source_project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), comment="来源项目（经验回流）"
    )
    status: Mapped[str] = mapped_column(String(20), default="提案", comment="入库审批状态")
    proposed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), comment="提案人")
    reviewed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), comment="审核人（质控）")
    approved_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), comment="批准人（合伙人）")
    reject_reason: Mapped[str | None] = mapped_column(String(200), comment="驳回理由")


class SkillAsset(PKMixin, TenantMixin, TimestampMixin, Base):
    """Skill 资产（受控 zip 发布链 FR-07-03；数量口径 §1.5：存量 16 + 新增 5 = 21）。

    静态扫描禁含 IP/路径/凭证，违规构建期拦截；回滚版本链支持一键回退。
    """

    __tablename__ = "skill_assets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_skill_name_version"),
        CheckConstraint(
            "review_status IN ('待审核','已发布','已下线')", name="ck_skill_status"
        ),
    )

    name: Mapped[str] = mapped_column(String(100), comment="Skill 名称（audit-*/ai-*）")
    version: Mapped[str] = mapped_column(String(20), comment="版本")
    zip_key: Mapped[str] = mapped_column(String(200), comment="zip 对象键")
    review_status: Mapped[str] = mapped_column(String(10), default="待审核", comment="审核状态")
    reviewed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), comment="审核人")
    approved_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), comment="批准人")
    scan_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONField, comment="静态扫描结果（禁含 IP/路径/凭证）"
    )
    rollback_chain: Mapped[list[Any] | None] = mapped_column(JSONField, comment="回滚版本链")
