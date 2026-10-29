"""出站白名单（§4.2.7、AGENTS.md 硬约束 7）。

平台任何云端通道（Skill 资产库、模板更新、顺丰 API）只出不进客户数据；
默认全关，仅放行已配置适配器。核心级客户禁用一切云端通道（§4.2.4）。
"""
from __future__ import annotations

from functools import lru_cache

from ..core.config import get_settings


class EgressBlocked(Exception):
    pass


@lru_cache(maxsize=1)
def _whitelist() -> frozenset[str]:
    hosts = get_settings().egress_hosts
    return frozenset(h.strip() for h in hosts.split(",") if h.strip())


def assert_egress_allowed(host: str, *, client_data_level: str = "一般") -> None:
    """出站前校验；核心级客户一律拒绝（含 Skill 资产库出站同步）。"""
    if client_data_level == "核心":
        raise EgressBlocked("核心级客户数据禁止一切云端通道出站（§4.2.4）")
    if not get_settings().sf_express_enabled and host not in _whitelist():
        raise EgressBlocked(f"出站目标未在白名单内：{host}（默认全关，仅放行已配置适配器）")
    if host not in _whitelist():
        raise EgressBlocked(f"出站目标未在白名单内：{host}")
