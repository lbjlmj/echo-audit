"""团队与工时字段（FR-02-05）+ 合同收费字段（FR-01-06）。

Revision ID: 0003, Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_projects",
        sa.Column("fee_amount", sa.Numeric(14, 2), nullable=True,
                  comment="合同收费（元，FR-01-06 校验入参）"),
    )
    op.add_column(
        "audit_projects",
        sa.Column("total_assets", sa.Numeric(16, 2), nullable=True,
                  comment="客户资产总额（元，收费档位依据，FR-01-06）"),
    )
    op.add_column(
        "project_members",
        sa.Column("budget_hours", sa.Numeric(8, 1), nullable=False,
                  server_default="0", comment="工时预算（小时，FR-02-05）"),
    )
    op.add_column(
        "project_members",
        sa.Column("actual_hours", sa.Numeric(8, 1), nullable=False,
                  server_default="0", comment="实际工时（小时，FR-02-05 对比展示）"),
    )


def downgrade() -> None:
    op.drop_column("project_members", "actual_hours")
    op.drop_column("project_members", "budget_hours")
    op.drop_column("audit_projects", "total_assets")
    op.drop_column("audit_projects", "fee_amount")
