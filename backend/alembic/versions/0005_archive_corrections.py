"""差错归档单（§15-16 归档后差错 v(n+1) 处理链承载表）。

Revision ID: 0005, Revises: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels = None
depends_on = None

JSONB = pg.JSONB


def upgrade() -> None:
    op.create_table(
        "archive_corrections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, server_default="default"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False),
        sa.Column("code", sa.String(40), nullable=False, comment="差错单号（{项目编号}-CORnnn，系统生成）"),
        sa.Column("description", sa.String(1000), nullable=False, comment="差错描述"),
        sa.Column("affected_worksheet_ids", JSONB, nullable=False, comment="受影响底稿 id 清单"),
        sa.Column("status", sa.String(10), nullable=False, server_default="待批准", comment="六状态"),
        sa.Column("registered_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False, comment="登记人（质控专员）"),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id"), comment="批准人（合伙人）"),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.String, comment="驳回原因（驳回必附）"),
        sa.Column("revision_baseline", JSONB, comment="批准时点版本计数基线 {worksheet_id: n}"),
        sa.Column("qc_reviewed_by", sa.String(36), sa.ForeignKey("users.id"), comment="质控复核人"),
        sa.Column("qc_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("qc_opinion", sa.String, comment="质控复核意见（退回必附）"),
        sa.Column("previous_package_hash", sa.String(64), comment="v(n) 归档包哈希"),
        sa.Column("rearchive_package_hash", sa.String(64), comment="v(n+1) 归档包哈希"),
        sa.Column("rearchived_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('待批准','已驳回','修订中','待质控复核','复核通过','已重新归档')",
            name="ck_correction_status",
        ),
    )
    op.create_index("ix_archive_corrections_project_id", "archive_corrections", ["project_id"])
    op.create_index("ix_archive_corrections_tenant_id", "archive_corrections", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_archive_corrections_tenant_id", table_name="archive_corrections")
    op.drop_index("ix_archive_corrections_project_id", table_name="archive_corrections")
    op.drop_table("archive_corrections")
