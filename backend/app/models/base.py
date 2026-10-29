"""ORM 基类与通用混入（TECH_DESIGN §3；tenant_id 全表贯通，§3.3.4）。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# 生产（PostgreSQL 16）为 JSONB（§3.2）；sqlite 仅承载单测冒烟，自动降级为 JSON。
JSONField = JSONB().with_variant(JSON(), "sqlite")


def sql_in(values: tuple[str, ...]) -> str:
    """生成 CHECK IN 约束的值列表（中文 SQL 字面量）。"""
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


class TenantMixin:
    """单所部署为常量；托管形态启用 RLS 按租户隔离（§3.3.4）。"""

    tenant_id: Mapped[str] = mapped_column(String(36), default="default", index=True)


class PKMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: uuid.uuid4().hex)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
