"""统一响应与错误码骨架（§2.3：错误码按域分段）。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")

# 错误码段：WS=底稿 CF=函证 FD=问题 PR=项目 MA=资料 KN=知识 QU=质控 AI=AI AD=管理 AU=认证
ERROR_CODE_PREFIX = {
    "AU": "认证", "PR": "项目", "MA": "资料", "WS": "底稿", "RV": "复核",
    "CF": "函证", "FD": "问题", "KN": "知识", "QU": "质控", "AI": "AI", "AD": "管理",
}


class ApiResponse(BaseModel, Generic[T]):
    code: str = "OK"
    message: str = ""
    data: T | None = None


class IdempotencyGuard(BaseModel):
    """状态变更类接口幂等骨架：请求 ID + Redis 锁（§2.3）。"""

    request_id: str

    def lock_key(self, domain: str, action: str) -> str:
        return f"idem:{domain}:{action}:{self.request_id}"
