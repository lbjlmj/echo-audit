"""FR-01 立项行为链集成测试：承接评价拦截 / 审批建档 / 八阶段门 / 团队登记 / 留痕整链。

权限以后端为准（硬约束 5）；状态/阶段门只走 engine（硬约束 2）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.project import AuditProject, StageGate
from app.models.user import ProjectMember, User
from app.services.auth import hash_password

_EVAL_OK = {
    "independence_ok": True,
    "client_risk_ok": True,
    "competence_ok": True,
    "engagement_letter_ok": True,
}


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


def _mkclient(db) -> str:
    from app.models.project import Client

    row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(row)
    db.commit()
    return row.id


def _submit(db, client, headers: dict, client_id: str, *, members: list | None = None, **eval_overrides) -> object:
    payload = {"name": "示例公司 2025 年报审计", "client_id": client_id,
               "business_type": "商贸", "period": "2025 年度",
               "evaluation": {**_EVAL_OK, **eval_overrides},
               "members": members or []}
    return client.post("/api/v1/projects", json=payload, headers=headers)


def test_auditor_cannot_submit(client, db):
    """权限矩阵：立项/终止 审计员=无（PRD §3）。"""
    _mkuser(db, "auditor", "审计员")
    headers = _login(client, "auditor")
    resp = _submit(db, client, headers, _mkclient(db))
    assert resp.status_code == 403


def test_intake_rejected_when_evaluation_fails(client, db):
    """FR-01-01：任一评价项未通过即拦截，且拦截动作留痕。"""
    _mkuser(db, "manager", "项目经理")
    headers = _login(client, "manager")
    resp = _submit(db, client, headers, _mkclient(db), independence_ok=False)
    assert resp.status_code == 422
    assert "PR-1001" in resp.text
    assert verify_chain(db), "拦截留痕也必须续哈希链"


def test_submit_then_approve_full_chain(client, db):
    """FR-01-01→01-02 闭环：项目经理申请 → 合伙人审批 → 自动建档。"""
    client_id = _mkclient(db)
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    manager_h = _login(client, "manager")
    partner_h = _login(client, "partner")

    # 申请
    resp = _submit(db, client, manager_h, client_id)
    assert resp.status_code == 201
    project = resp.json()
    assert project["project_no"].startswith("A") and project["project_no"].endswith("-0001")
    assert project["status"] == "进行中" and project["current_stage"] == "承接"
    assert project["approval_summary"]["approval_status"] == "待审批"
    project_id = project["id"]

    # 项目经理无权审批（PROPOSE < APPROVE）
    assert client.post(f"/api/v1/projects/{project_id}/approve", headers=manager_h).status_code == 403

    # 合伙人审批：初始化八阶段门 + 承接门通过
    resp = client.post(f"/api/v1/projects/{project_id}/approve", headers=partner_h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_summary"]["approved_by"] == "李合伙人"

    gates = db.query(StageGate).filter(StageGate.project_id == project_id).all()
    assert len(gates) == 8
    first = next(g for g in gates if g.stage == "承接")
    assert first.gate_status == "已通过" and first.passed_by is not None
    assert all(g.gate_status == "未开始" for g in gates if g.stage != "承接")

    # 重复审批拒绝（幂等保护）
    assert client.post(f"/api/v1/projects/{project_id}/approve", headers=partner_h).status_code == 409

    # 留痕整链可验证（硬约束 4）
    assert verify_chain(db)


def test_approval_without_engagement_letter_keeps_gate_open(client, db):
    """约定书未核：审批通过但承接门不判「已通过」（engine.pass_gate 裁决）。"""
    client_id = _mkclient(db)
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    resp = _submit(db, client, _login(client, "manager"), client_id, engagement_letter_ok=False)
    project_id = resp.json()["id"]
    client.post(f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner"))
    gate = db.query(StageGate).filter(StageGate.project_id == project_id, StageGate.stage == "承接").one()
    assert gate.gate_status == "未开始"  # engagement_letter 条件未满足


def test_members_initialized_on_approval(client, db):
    """FR-01-02：拟任团队随审批通过自动落库；同一用户重复配置角色在申请时即拦截。"""
    client_id = _mkclient(db)
    manager = _mkuser(db, "manager", "项目经理")
    member = _mkuser(db, "member1", "审计员")
    _mkuser(db, "partner", "合伙人")
    headers = _login(client, "manager")

    # 同一用户两个角色 → 409（R1 入口拦截）
    dup = _submit(db, client, headers, client_id, members=[
        {"user_id": member.id, "project_role": "编制"},
        {"user_id": member.id, "project_role": "复核"},
    ])
    assert dup.status_code == 409

    ok = _submit(db, client, headers, client_id, members=[
        {"user_id": member.id, "project_role": "编制"},
    ])
    assert ok.status_code == 201
    assert db.query(ProjectMember).count() == 0  # 审批前不落库

    project_id = ok.json()["id"]
    client.post(f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner"))
    rows = db.query(ProjectMember).filter(ProjectMember.project_id == project_id).all()
    assert len(rows) == 1 and rows[0].project_role == "编制" and rows[0].user_id == member.id
    assert manager.global_role == "项目经理"  # 申请人与团队成员相互独立


def test_project_list_permission(client, db):
    """列表查看：质控专员=查看（PROPOSE ≥ VIEW）；审计员=无（NONE < VIEW）。"""
    client_id = _mkclient(db)
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    _submit(db, client, _login(client, "manager"), client_id)

    assert client.get("/api/v1/projects", headers=_login(client, "qc")).status_code == 200
    assert client.get("/api/v1/projects", headers=_login(client, "auditor")).status_code == 403
    assert db.query(AuditProject).count() == 1
