"""auth 域闭环集成测试（/api/v1/auth）：login / me / logout，AU-1xxx 错误码。

权限以后端为准（硬约束 5）；sqlite 内存库 + TestClient，不触达生产 PG。
"""
from __future__ import annotations

from app.core.permissions import PERMISSIONS
from app.models.user import User
from app.services.auth import hash_password


def _mkuser(db, login_name: str = "auditor1", role: str = "审计员", status: str = "启用") -> User:
    user = User(
        name="测试员", login_name=login_name, password_hash=hash_password("secret66"),
        global_role=role, status=status,
    )
    db.add(user)
    db.commit()
    return user


def test_login_success_and_me(client, db):
    _mkuser(db, role="项目经理")
    resp = client.post("/api/v1/auth/login", json={"login_name": "auditor1", "password": "secret66"})
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["global_role"] == "项目经理"
    # 10 权限码全量返回，等级以后端矩阵为准（§4.2.2）
    assert set(body["permissions"]) == set(PERMISSIONS)
    assert body["permissions"]["worksheet.edit"] == "OPERATE"
    assert body["permissions"]["report.sign"] == "PROPOSE"


def test_login_wrong_password(client, db):
    _mkuser(db)
    resp = client.post("/api/v1/auth/login", json={"login_name": "auditor1", "password": "wrong-pass"})
    assert resp.status_code == 401
    assert "登录名或密码错误" in resp.text


def test_login_unknown_user_same_error(client, db):
    """不区分「用户不存在/密码错误」，防账号枚举（统一 AU-1001）。"""
    resp = client.post("/api/v1/auth/login", json={"login_name": "nobody", "password": "whatever"})
    assert resp.status_code == 401


def test_me_requires_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_me_invalid_token(client):
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_disabled_user_rejected(client, db):
    _mkuser(db, login_name="paused1", status="停用")
    resp = client.post("/api/v1/auth/login", json={"login_name": "paused1", "password": "secret66"})
    assert resp.status_code == 403


def test_logout_requires_auth(client, db):
    _mkuser(db)
    assert client.post("/api/v1/auth/logout").status_code == 401
    token = client.post(
        "/api/v1/auth/login", json={"login_name": "auditor1", "password": "secret66"}
    ).json()["access_token"]
    resp = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.json()["code"] == "OK"
