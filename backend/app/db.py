"""数据库会话（SQLAlchemy 2.0 同步引擎；生产 PostgreSQL 16，sqlite 仅承载测试冒烟）。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .core.config import get_settings

engine = create_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级会话（测试经 dependency_overrides 注入 sqlite 会话）。

    请求成功即提交、异常即回滚——服务层只 flush 不 commit（冒烟曾因缺 commit
    静默丢写，pytest 因自管事务未暴露；留痕哈希链同理依赖本提交点）。
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
