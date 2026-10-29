"""留痕实体（红线 1/3、FR-06-02、PRD §4.6）：结构冻结、append-only，库端无 UPDATE/DELETE 权限。

- AuditTrail：全量操作留痕，哈希链字段与 core/audit_middleware.HashChain 对齐；
- AIRun：AI 使用留痕，字段与 ai/pipeline.AIRun 三段式（输入→推理→人工复核标记）对齐。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, JSONField, PKMixin, TenantMixin, TimestampMixin

AI_MODULES = ("ai_risk", "ai_anomaly", "ai_confirm", "ai_finding", "ai_notes", "ai_qc", "rag", "ai_classify")
AIRUN_STATUS = ("线索", "已复核", "已采信", "部分采信", "不采信")
REJECTION_REASONS = ("事实错误", "口径错误", "幻觉", "其他")  # §15-16


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


class AuditTrail(PKMixin, TenantMixin, Base):
    """全量操作留痕。

    结构冻结（§3.3.3）：业务表可演进，本表不可；与业务表经实体类型+id 弱关联。
    不混入 TimestampMixin——updated_at 的 onupdate 语义与 append-only 冻结相悖；
    哈希链字段与 core/audit_middleware 完全一致，任何历史篡改整链验证即断。
    """

    __tablename__ = "audit_trails"

    entity_type: Mapped[str] = mapped_column(String(50), comment="实体类型")
    entity_id: Mapped[str] = mapped_column(String(36), index=True, comment="实体 id（弱关联）")
    action: Mapped[str] = mapped_column(String(50), comment="动作")
    operator: Mapped[str] = mapped_column(String(50), comment="操作人")
    before_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONField, comment="变更前快照")
    after_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONField, comment="变更后快照")
    prev_hash: Mapped[str] = mapped_column(String(64), comment="前条哈希（哈希链）")
    entry_hash: Mapped[str] = mapped_column(String(64), comment="本条哈希（append-only 封印）")
    ts: Mapped[float] = mapped_column(comment="纪元时间戳（哈希复算依据，append_trail 写入）")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="留痕时间"
    )


class AIRun(PKMixin, TenantMixin, TimestampMixin, Base):
    """AI 使用留痕（字段对齐 PRD §4.6，10 字段三段式）。

    第三段（复核人+日期+采信结论）未落库，输出永远停在线索态，无法进入
    WorksheetVersion 版本链、无法归档（§7 R2）；不采信必附原因分类（§15-16）。
    关联对象仅存（类型,id）索引——物理隔开「AI 说的」与「人认的」（§3.3.2）。
    """

    __tablename__ = "ai_runs"
    __table_args__ = (
        CheckConstraint(f"module IN {_in(AI_MODULES)}", name="ck_airun_module"),
        CheckConstraint(f"status IN {_in(AIRUN_STATUS)}", name="ck_airun_status"),
        CheckConstraint(f"rejection_reason IN {_in(REJECTION_REASONS)} OR rejection_reason IS NULL", name="ck_airun_reject"),
    )

    run_no: Mapped[str] = mapped_column(String(40), unique=True, comment="留痕编号（全局唯一）")
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    module: Mapped[str] = mapped_column(String(20), comment="AI 模块")
    skill_version: Mapped[str] = mapped_column(String(20), comment="Skill 版本")
    model_version: Mapped[str] = mapped_column(String(50), comment="模型版本")
    triggered_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), comment="触发人")
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="触发时间"
    )
    input_material_ids: Mapped[list[Any]] = mapped_column(
        JSONField, default=list, comment="输入材料 id 数组（Material 弱关联数组+输入哈希）"
    )
    input_hashes: Mapped[dict[str, Any]] = mapped_column(
        JSONField, default=dict, comment="输入哈希（material_id → sha256）"
    )
    output_digest: Mapped[str | None] = mapped_column(String(64), comment="输出摘要哈希")
    output_key: Mapped[str | None] = mapped_column(String(200), comment="输出对象键（MinIO）")
    status: Mapped[str] = mapped_column(String(10), default="线索", comment="线索/复核/采信状态")
    reviewed_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), comment="复核人（第三段）")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="复核日期（第三段）")
    rejection_reason: Mapped[str | None] = mapped_column(String(10), comment="不采信原因分类（§15-16）")
    related_type: Mapped[str | None] = mapped_column(String(20), comment="关联对象类型（底稿/函证/问题）")
    related_id: Mapped[str | None] = mapped_column(String(36), comment="关联对象 id")
