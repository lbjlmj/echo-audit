"""测试夹具：sqlite 内存库承载实体冒烟与 API 集成（生产为 PG 16，sqlite 仅冒烟口径）。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — 副作用：全部实体注册进 Base.metadata
from app.models.base import Base


@pytest.fixture()
def engine():
    # 内存库 + 单连接共享池：TestClient 在工作线程发请求，须放开 sqlite 线程亲和
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine):
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """对象存储隔离：出稿/写回产物写入本用例临时目录，不污染 dev 数据目录。"""
    import app.services.confirmations.service as confirmations_service
    import app.services.planning.service as planning_service
    import app.services.quality.service as quality_service
    import app.services.reports.service as report_service
    import app.services.worksheets.service as worksheet_service
    from app.core.storage import ObjectStorage

    fake = lambda: ObjectStorage(tmp_path / "objects")  # noqa: E731
    monkeypatch.setattr(report_service, "get_storage", fake)
    monkeypatch.setattr(worksheet_service, "get_storage", fake)
    monkeypatch.setattr(quality_service, "get_storage", fake)
    monkeypatch.setattr(confirmations_service, "get_storage", fake)
    monkeypatch.setattr(planning_service, "get_storage", fake)
    yield tmp_path / "objects"


@pytest.fixture()
def client(db: Session):
    """TestClient：get_db 依赖覆盖为测试会话（不触达生产 PG）。"""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)
