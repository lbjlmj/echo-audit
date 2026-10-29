"""报告出稿对象键（FR-05-04 模板保真写回，硬约束 8）。

Revision ID: 0004, Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_reports",
        sa.Column("file_key", sa.String(300), nullable=True,
                  comment="出稿对象键（模板保真写回产物，指纹校验通过后落键）"),
    )


def downgrade() -> None:
    op.drop_column("audit_reports", "file_key")
