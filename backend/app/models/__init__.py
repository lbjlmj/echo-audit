"""ORM 实体聚合（TECH_DESIGN §3.2 全量 23 实体，字段口径以该节为准）。

导入本包即完成 Base.metadata 注册——alembic env.py 与测试 create_all 依赖此副作用。
状态流转一律经 engine（硬约束 2）；本层只约束存储，不实现流转。
"""
from .audit import AIRun, AuditTrail
from .base import Base
from .confirmation import Confirmation
from .finding import AuditReport, Finding
from .knowledge import KnowledgeItem, RuleAnchor, RuleRelation, SkillAsset, Template
from .material import ImportFieldMapping, Material, VoucherLine, VoucherSample
from .project import AuditProject, Client, StageGate
from .quality import ArchiveCorrection
from .risk import AuditProgram, Materiality, RiskItem
from .user import ProjectMember, User
from .worksheet import Worksheet, WorksheetReview, WorksheetVersion

__all__ = [
    "AIRun",
    "ArchiveCorrection",
    "AuditProject",
    "AuditReport",
    "AuditTrail",
    "AuditProgram",
    "Base",
    "Client",
    "Confirmation",
    "Finding",
    "ImportFieldMapping",
    "KnowledgeItem",
    "Material",
    "Materiality",
    "ProjectMember",
    "RiskItem",
    "RuleAnchor",
    "RuleRelation",
    "SkillAsset",
    "StageGate",
    "Template",
    "User",
    "VoucherLine",
    "VoucherSample",
    "Worksheet",
    "WorksheetReview",
    "WorksheetVersion",
]
