"""认证域业务逻辑（§2.3 /auth 域）：argon2 口令校验、JWT 签发、权限矩阵视图。

路由层薄、服务层厚：认证域全部规则在本模块；权限以后端为准（硬约束 5）。
口令哈希 argon2-cffi（§3.2 User 字段口径）。
"""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.permissions import MATRIX
from ..core.security import create_token
from ..models.user import User

_hasher = PasswordHasher()


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(hashed: str, raw: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except VerifyMismatchError:
        return False


def authenticate(db: Session, login_name: str, password: str) -> tuple[User | None, str | None]:
    """登录校验：返回 (用户, 错误码)。

    错误码：AU-1001 登录名或密码错误；AU-1002 账号已停用。
    统一返回 AU-1001 不区分「用户不存在/密码错误」，防账号枚举。
    """
    user = db.scalar(select(User).where(User.login_name == login_name))
    if user is None or not verify_password(user.password_hash, password):
        return None, "AU-1001"
    if user.status != "启用":
        return None, "AU-1002"
    return user, None


def issue_token(user: User) -> str:
    """签发 JWT（sub=user_id，role=全局角色，tid=租户）。"""
    return create_token(user.id, user.global_role, user.tenant_id)


def permissions_view(role: str) -> dict[str, str]:
    """权限矩阵视图：10 权限码 × 当前角色可达最高等级（前端仅兜底展示）。"""
    return {code: MATRIX[code][role].name for code in MATRIX}
