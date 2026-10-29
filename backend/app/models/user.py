"""用户与项目成员（TECH_DESIGN §3.2 用户与权限；PRD §3 双层权限）。

双层权限 = 全局角色 ∩ 项目角色，再叠加权限码（core/permissions.py 单点实现，硬约束 9）。
R1 职责分离：一人一项目仅一个项目角色（库端唯一约束），编制与复核不得同一人；
服务层另有 check_preparer_reviewer_separation 双重校验（§4.2.3）。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PKMixin, TenantMixin, TimestampMixin


class User(PKMixin, TenantMixin, TimestampMixin, Base):
    """用户：全局角色决定菜单与功能面（合伙人/项目经理/审计员/质控专员）。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "global_role IN ('合伙人','项目经理','审计员','质控专员')", name="ck_user_role"
        ),
        CheckConstraint("status IN ('启用','停用')", name="ck_user_status"),
    )

    name: Mapped[str] = mapped_column(String(50), comment="姓名")
    login_name: Mapped[str] = mapped_column(String(50), unique=True, comment="登录名")
    password_hash: Mapped[str] = mapped_column(String(200), comment="密码哈希（argon2）")
    global_role: Mapped[str] = mapped_column(String(20), comment="全局角色（PRD §3）")
    license_no: Mapped[str | None] = mapped_column(String(50), comment="执业证书号")
    status: Mapped[str] = mapped_column(String(10), default="启用", comment="账号状态")


class ProjectMember(PKMixin, TenantMixin, TimestampMixin, Base):
    """项目成员：项目角色（编制/复核/查看）。

    一人一项目仅一个角色行（唯一约束）——库端保证编制与复核不得同一人（R1 双签）。
    """

    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_member_project_user"),
        CheckConstraint("project_role IN ('编制','复核','查看')", name="ck_member_role"),
    )

    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("audit_projects.id"), index=True
    )
    budget_hours: Mapped[Decimal] = mapped_column(
        Numeric(8, 1), default=Decimal("0"), comment="工时预算（小时，FR-02-05）"
    )
    actual_hours: Mapped[Decimal] = mapped_column(
        Numeric(8, 1), default=Decimal("0"), comment="实际工时（小时，FR-02-05 对比展示）"
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    project_role: Mapped[str] = mapped_column(String(10), comment="项目角色（编制/复核/查看）")
