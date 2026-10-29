"""认证域出入参 DTO（出入参一律 Pydantic Schema，禁止裸 dict 跨层传递）。"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    login_name: str = Field(min_length=2, max_length=50, description="登录名")
    password: str = Field(min_length=6, max_length=128, description="口令")


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    login_name: str
    global_role: str
    status: str


class MeOut(BaseModel):
    user: UserOut
    permissions: dict[str, str] = Field(description="10 权限码 × 当前角色可达最高等级")
