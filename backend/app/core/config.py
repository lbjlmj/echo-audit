"""全局配置（Pydantic Settings）。

硬约束映射：
- 大模型通道默认关闭（AI_LLM_ENABLED=False），启用须过三条件门禁（§1.5/§15-15）
- 出站白名单制（EGRESS_HOSTS），默认仅本地组件
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ECHO_", extra="ignore")

    app_name: str = "Echo 审计作业平台"
    environment: str = "dev"  # dev / staging / prod

    # 数据库（PostgreSQL 16 + pgvector，一库两用）
    database_url: str = "postgresql+psycopg://echo:echo@localhost:5432/echo_audit"
    redis_url: str = "redis://localhost:6379/0"
    # 对象存储：本地文件系统（不引入 MinIO，开源版已归档停止维护）。
    # 静态加密靠 OS 层（LUKS/BitLocker 加密盘），归档 WORM 靠 chattr +a（见 storage.py）。
    # 键即相对路径，目录结构按业务域分层（worksheets/、confirmations/、reports/、archives/）。
    storage_dir: str = ""  # 空 = ./data/objects（dev 口径）；生产挂独立加密卷

    # 认证（开发默认值 ≥32 字节避免 HMAC 弱密钥告警；生产必改）
    jwt_secret: str = "change-me-in-production-echo-audit-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 12 * 60

    # 大模型通道（§15-15：默认关闭；启用须 ai.gate.llm 三条件校验）
    llm_enabled: bool = False
    llm_endpoint: str = ""  # vLLM 私有化地址，启用前经出站白名单校验

    # 出站白名单（§4.2.7：默认全关，仅放行已配置适配器；只出不进客户数据）
    egress_hosts: str = ""  # 逗号分隔，如 "api-sf.fadada.com,sf-express.com"
    sf_express_enabled: bool = False  # 顺丰适配器开关（首期唯一外部通道，§15-9）

    # 函证超期（§15-7）
    confirm_overdue_days: int = 30
    confirm_remind_interval_days: int = 15
    confirm_remind_max_times: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
