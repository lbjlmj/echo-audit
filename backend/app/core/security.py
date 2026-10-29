"""JWT 认证骨架（纵深防御第一层，§4.2.1）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from .config import get_settings


def create_token(user_id: str, global_role: str, tenant_id: str = "default") -> str:
    settings = get_settings()
    payload: dict[str, Any] = {
        "sub": user_id,
        "role": global_role,
        "tid": tenant_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
