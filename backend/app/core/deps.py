"""请求级依赖：JWT 认证（纵深防御第一层，§4.2.1）+ DB 会话注入。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..db import get_db
from ..models.user import User
from .security import decode_token

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """解析 Bearer 令牌并加载用户；停用账号一律拒绝（权限以后端为准，硬约束 5）。"""
    if cred is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "AU-1000 未携带访问令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "AU-1003 令牌无效或已过期")
    user = db.get(User, str(payload.get("sub", "")))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "AU-1004 用户不存在")
    if user.status != "启用":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "AU-1002 账号已停用")
    return user
