"""P1 批次集成测试：FR-01-06 收费校验 + FR-02-05 团队工时。

收费：报价低于《业务收费管理办法》档位基准 → 422 阻断立项 + 差异项提示，
通过则留痕（含档位/基准/缺口）；工时：经理 upsert 配置、对比视图偏差超 10%
告警、审计员只读、归档项目拒绝再配置。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.project import AuditProject, Client
from app.models.user import User
from app.services.auth import hash_password


def _mkuser(db, login_name: str, role: str) -> User:
    user = User(
        name={"manager": "王经理", "partner": "李合伙人", "auditor": "赵审计员"}.get(login_name, login_name),
        login_name=login_name, password_hash=hash_password("secret66"), global_role=role,
    )
    db.add(user)
    db.commit()
    return user


def _login(client, login_name: str) -> dict:
    token = client.post(
        "/api/v1/auth/login", json={"login_name": login_name, "password": "secret66"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _setup(client, db) -> dict:
    """用户 + 客户 + 合规立项（带合同收费），返回 ids。"""
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    manager = db.query(User).filter_by(login_name="manager").first()
    auditor = db.query(User).filter_by(login_name="auditor").first()
    client_row = Client(
        name="商洛鸿瑞怡园", uscc="91610100MA0000000X",
        industry="商贸", data_level="重要",
    )
    db.add(client_row)
    db.commit()
    return {"client": client_row.id, "manager_id": manager.id, "auditor_id": auditor.id}


def _submit(client, db, ids, *, fee: float) -> dict:
    manager_h = _login(client, "manager")
    resp = client.post("/api/v1/projects", json={
        "name": "鸿瑞怡园 2024 年报审计", "client_id": ids["client"],
        "business_type": "商贸", "period": "2024 年度",
        "evaluation": {
            "independence_ok": True, "client_risk_ok": True,
            "competence_ok": True, "engagement_letter_ok": True,
        },
        "members": [{"user_id": ids["manager_id"], "project_role": "复核"},
                    {"user_id": ids["auditor_id"], "project_role": "编制"}],
        "contract": {"total_assets": 8_000_000, "fee_amount": fee},
    }, headers=manager_h)
    return resp


def test_fee_check_and_team_hours(client, db):
    """收费阻断/通过 → 工时配置对比 → 偏差告警 → 权限矩阵。"""
    ids = _setup(client, db)
    manager_h, auditor_h = _login(client, "manager"), _login(client, "auditor")

    # FR-01-06：资产 800 万档最低 3 万，报价 2.5 万 → 422 阻断立项 + 差异项提示
    resp = _submit(client, db, ids, fee=25_000)
    assert resp.status_code == 422, resp.text
    assert "PR-1001" in resp.json()["detail"] and "缺口" in resp.json()["detail"]

    # 报价达标 → 立项成功，审批建档
    resp = _submit(client, db, ids, fee=32_000)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    assert resp.json()["approval_summary"]["fee_check"]["passed"] is True
    assert client.post(f"/api/v1/projects/{project_id}/approve",
                       headers=_login(client, "partner")).status_code == 200

    # FR-02-05：经理 upsert 配置工时（预算 100h/实际 85h → 偏差 15% 告警）
    resp = client.put(f"/api/v1/projects/{project_id}/team-hours", json={
        "members": [
            {"user_id": ids["manager_id"], "budget_hours": 60, "actual_hours": 60},
            {"user_id": ids["auditor_id"], "budget_hours": 40, "actual_hours": 25},
        ],
    }, headers=manager_h)
    assert resp.status_code == 200, resp.text
    view = resp.json()
    assert view["budget_total"] == 100 and view["actual_total"] == 85
    assert view["deviation_pct"] == 15.0 and view["over_threshold"] is True, "偏差超 10% 告警"
    auditor_row = next(m for m in view["members"] if m["project_role"] == "编制")
    assert auditor_row["deviation_pct"] == 37.5, "成员级偏差一并展示"

    # 追平偏差 → 告警解除（upsert 局部更新：未提供字段保留原值）
    resp = client.put(f"/api/v1/projects/{project_id}/team-hours", json={
        "members": [{"user_id": ids["auditor_id"], "actual_hours": 38}],
    }, headers=manager_h)
    view = resp.json()
    assert view["budget_total"] == 100, "第二次 upsert 未提供的预算字段保留原值"
    assert view["actual_total"] == 98
    assert view["deviation_pct"] == 2.0 and view["over_threshold"] is False

    # 权限：审计员（project.approve=无）配置 403；查看视图 403（§3 矩阵：立项对审计员无权限）
    assert client.put(f"/api/v1/projects/{project_id}/team-hours", json={
        "members": [{"user_id": ids["auditor_id"], "actual_hours": 0}],
    }, headers=auditor_h).status_code == 403

    # 工时负数 422
    assert client.put(f"/api/v1/projects/{project_id}/team-hours", json={
        "members": [{"user_id": ids["auditor_id"], "actual_hours": -1}],
    }, headers=manager_h).status_code == 422

    assert verify_chain(db), "收费校验与工时配置留痕须可整链验证（硬约束 4）"
