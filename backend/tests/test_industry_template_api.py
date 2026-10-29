"""FR-02-04 行业模板选择集成测试：矩阵概览 + 建档圈定 + 计划阶段变更留痕。

模板选择决定资料清单与底稿模板范围（scope 快照）；专项/经责非高频矩阵行业
scope 为空注记不阻断；变更同步 business_type 并 before/after 全量留痕。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.project import Client
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


def _setup(client, db, business_type: str = "商贸") -> str:
    """建客户 + 立项（含行业类型）+ 审批建档，返回 project_id。"""
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    db.add(Client(
        name="商洛鸿瑞怡园", uscc="91610100MA0000000X",
        industry="商贸", data_level="重要",
    ))
    db.commit()
    manager_h = _login(client, "manager")
    project_id = client.post("/api/v1/projects", json={
        "name": "鸿瑞怡园 2024 年报审计", "client_id": db.query(Client).first().id,
        "business_type": business_type, "period": "2024 年度",
        "evaluation": {
            "independence_ok": True, "client_risk_ok": True,
            "competence_ok": True, "engagement_letter_ok": True,
        },
    }, headers=manager_h).json()["id"]
    resp = client.post(f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner"))
    assert resp.status_code == 200, resp.text
    return project_id


def test_industry_template_matrix_and_selection(client, db):
    """矩阵八类；建档自动圈定 scope；计划阶段经理变更重算 + 留痕；权限矩阵。"""
    project_id = _setup(client, db)
    manager_h, auditor_h, qc_h = (
        _login(client, "manager"), _login(client, "auditor"), _login(client, "qc"),
    )

    # 矩阵概览：八类高频行业；商贸含进销存台账与存货底稿
    resp = client.get("/api/v1/projects/industry-templates", headers=manager_h)
    assert resp.status_code == 200, resp.text
    matrix = resp.json()
    assert len(matrix) == 8 and "商贸" in matrix and "房地产" in matrix
    assert "进销存台账" in matrix["商贸"]["materials"]
    assert matrix["商贸"]["worksheet_templates"][0]["key"] == "ws-general-plan"
    assert client.get("/api/v1/projects/industry-templates", headers=auditor_h).status_code == 403

    # 建档自动圈定：商贸项目 scope 随审批落 approval_summary（FR-02-04 决定资料/底稿范围）
    body = client.get("/api/v1/projects", headers=manager_h).json()
    scope = body[0]["approval_summary"]["template_scope"]
    assert scope["in_matrix"] is True and scope["industry_template"] == "商贸"
    assert "库存盘点表" in scope["materials"] and scope["worksheet_templates"]

    # 计划阶段变更：经理（plan.confirm=OPERATE）改判房地产 → business_type 与 scope 重算
    resp = client.post(f"/api/v1/projects/{project_id}/industry-template",
                       json={"industry_template": "房地产"}, headers=manager_h)
    assert resp.status_code == 200, resp.text
    updated = resp.json()["approval_summary"]["template_scope"]
    assert updated["industry_template"] == "房地产"
    assert "开发资质与预售许可证" in updated["materials"]
    assert any(t["key"] == "ws-dev-cost" for t in updated["worksheet_templates"])

    # 权限：质控（plan.confirm=查看）与审计员（无）不可变更；非法模板 422
    assert client.post(f"/api/v1/projects/{project_id}/industry-template",
                       json={"industry_template": "商贸"}, headers=qc_h).status_code == 403
    assert client.post(f"/api/v1/projects/{project_id}/industry-template",
                       json={"industry_template": "商贸"}, headers=auditor_h).status_code == 403
    assert client.post(f"/api/v1/projects/{project_id}/industry-template",
                       json={"industry_template": "金融"}, headers=manager_h).status_code == 422

    # 非高频矩阵行业（专项）：立项圈定 scope 为空注记，不阻断
    project2 = client.post("/api/v1/projects", json={
        "name": "专项资金审计", "client_id": db.query(Client).first().id,
        "business_type": "专项", "period": "2024 年度",
        "evaluation": {
            "independence_ok": True, "client_risk_ok": True,
            "competence_ok": True, "engagement_letter_ok": True,
        },
    }, headers=manager_h).json()["id"]
    assert client.post(f"/api/v1/projects/{project2}/approve", headers=_login(client, "partner")).status_code == 200
    scope2 = client.get("/api/v1/projects", headers=manager_h).json()
    scope2 = next(p for p in scope2 if p["id"] == project2)["approval_summary"]["template_scope"]
    assert scope2["in_matrix"] is False and scope2["materials"] == []
    assert "人工圈定" in scope2["note"]

    assert verify_chain(db), "模板圈定/变更留痕须可整链验证（硬约束 4）"
