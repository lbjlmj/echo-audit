"""资料自动分类字段（FR-01-04）：决策档案目录 + 客户原始文件名。

Revision ID: 0002, Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "materials",
        sa.Column("origin_name", sa.String(200), nullable=True,
                  comment="客户原始文件名（R6 重命名归档前保留，审计追溯 + 清单匹配）"),
    )
    op.add_column(
        "materials",
        sa.Column("catalog", sa.String(10), nullable=True,
                  comment="决策档案目录类别简称（R6 枚举，AI 分类采信后回写）"),
    )
    # FR-01-04：AI 模块枚举扩容（ai_classify 资料自动分类）
    op.drop_constraint("ck_airun_module", "ai_runs", type_="check")
    op.create_check_constraint(
        "ck_airun_module", "ai_runs",
        "module IN ('ai_risk','ai_anomaly','ai_confirm','ai_finding','ai_notes','ai_qc','rag','ai_classify')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_airun_module", "ai_runs", type_="check")
    op.create_check_constraint(
        "ck_airun_module", "ai_runs",
        "module IN ('ai_risk','ai_anomaly','ai_confirm','ai_finding','ai_notes','ai_qc','rag')",
    )
    op.drop_column("materials", "catalog")
    op.drop_column("materials", "origin_name")
