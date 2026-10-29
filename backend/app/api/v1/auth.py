"""认证域路由（§2.3 /auth）：登录 / 当前用户 / 登出。路由层薄，规则在 services/auth。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...core.deps import get_current_user
from ...db import get_db
from ...models.user import User
from ...schemas.auth import LoginRequest, MeOut, TokenOut, UserOut
from ...services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

# AU-1xxx 认证域错误码（错误码按域分段）
_LOGIN_ERRORS = {
    "AU-1001": (401, "登录名或密码错误"),
    "AU-1002": (403, "账号已停用，请联系管理员"),
}


@router.post("/login", response_model=TokenOut, summary="登录签发 JWT")
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenOut:
    user, error = auth_service.authenticate(db, body.login_name, body.password)
    if user is None:
        status_code, detail = _LOGIN_ERRORS[error or "AU-1001"]
        raise HTTPException(status_code, detail)
    return TokenOut(access_token=auth_service.issue_token(user))


@router.get("/me", response_model=MeOut, summary="当前用户与权限矩阵视图")
def me(user: User = Depends(get_current_user)) -> MeOut:
    return MeOut(
        user=UserOut.model_validate(user),
        permissions=auth_service.permissions_view(user.global_role),
    )


@router.post("/logout", summary="登出（JWT 无状态：服务端不存会话，客户端清除令牌）")
def logout(user: User = Depends(get_current_user)) -> dict:
    return {"code": "OK", "message": f"用户 {user.login_name} 已登出"}


@router.get("/users", response_model=list[UserOut],
            summary="用户列表（立项拟任团队/工时配置选择用；需登录）")
def list_users(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[UserOut]:
    from sqlalchemy import select

    rows = db.scalars(select(User).order_by(User.name)).all()
    return [UserOut.model_validate(u) for u in rows]
