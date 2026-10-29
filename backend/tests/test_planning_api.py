"""FR-02 计划域集成测试：R8 重要性水平报批 / 风险评级确认锁定 / R5 程序包三分支。

权限以后端为准（硬约束 5）；R8/R5 只经 engine 单点（硬约束 9）；留痕整链可验证（硬约束 4）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.project import AuditProject, StageGate
from app.models.risk import Materiality
from app.models.user import User
from app.services.auth import hash_password

_EVAL_OK = {
    "independence_ok": True,
    "client_risk_ok": True,
    "competence_ok": True,
    "engagement_letter_ok": True,
}

_MAT = {"benchmark_type": "营业收入", "basis_amount": "100000000"}  # 1 亿营收


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


def _setup(client, db) -> str:
    """五角色 + 客户 + 已批准项目（八门就绪），返回 project_id。"""
    client_id = _mkclient(db)
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    resp = client.post("/api/v1/projects", json={
        "name": "示例公司 2025 年报审计", "client_id": client_id,
        "business_type": "商贸", "period": "2025 年度",
        "evaluation": _EVAL_OK, "members": [],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    resp = client.post(f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner"))
    assert resp.status_code == 200, resp.text
    return project_id


def _plan_gate(db, project_id: str) -> StageGate:
    return db.query(StageGate).filter(
        StageGate.project_id == project_id, StageGate.stage == "计划"
    ).one()


def test_permission_matrix_on_planning_writes(client, db):
    """权限矩阵（PRD §3）：plan.confirm 审计员=无、质控=查看 → 写/批准一律 403。"""
    project_id = _setup(client, db)
    auditor_h, qc_h = _login(client, "auditor"), _login(client, "qc")
    assert client.post(f"/api/v1/projects/{project_id}/materiality", json=_MAT, headers=auditor_h).status_code == 403
    assert client.post(f"/api/v1/projects/{project_id}/materiality", json=_MAT, headers=qc_h).status_code == 403
    assert client.post(f"/api/v1/projects/{project_id}/risks", json={
        "source": "人工", "description": "测试风险", "risk_level": "中",
    }, headers=auditor_h).status_code == 403
    assert db.query(Materiality).count() == 0  # 被拒请求不得产生副作用


def test_materiality_get_view_and_unset_404(client, db):
    """R8 查询（plan.confirm ≥ VIEW）：未设置 404；审计员 403；经理可读且未批准态可见。"""
    project_id = _setup(client, db)
    manager_h, auditor_h, qc_h = (
        _login(client, "manager"), _login(client, "auditor"), _login(client, "qc"),
    )
    # 未设置 → 404 PL-1002
    resp = client.get(f"/api/v1/projects/{project_id}/materiality", headers=manager_h)
    assert resp.status_code == 404 and "PL-1002" in resp.text
    # 审计员 plan.confirm=无 → 403（权限以后端为准）
    assert client.get(
        f"/api/v1/projects/{project_id}/materiality", headers=auditor_h,
    ).status_code == 403
    # 设置后：质控（VIEW）可读，未经批准态如实返回
    assert client.post(
        f"/api/v1/projects/{project_id}/materiality", json=_MAT, headers=manager_h,
    ).status_code == 200
    resp = client.get(f"/api/v1/projects/{project_id}/materiality", headers=qc_h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["benchmark_type"] == "营业收入"
    assert float(body["overall_materiality"]) == 500000.0  # 1 亿 × 0.5%（engine 单点）
    assert body["approved_by"] is None


def test_materiality_r8_flow_save_approve_modify_reapprove(client, db):
    """FR-02-01 闭环：录入（engine 计算）→ 合伙人报批置计划门 → 修改留痕并撤回 → 重新报批。"""
    project_id = _setup(client, db)
    manager_h, partner_h = _login(client, "manager"), _login(client, "partner")

    # 录入：营业收入 1 亿 × 0.5% = 50 万；执行比例默认 60%
    resp = client.post(f"/api/v1/projects/{project_id}/materiality", json=_MAT, headers=manager_h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["overall_materiality"]) == 500000.0
    assert float(body["execution_ratio"]) == 60
    assert body["approved_by"] is None  # 待报批

    # 无记录时批准 → 404
    assert client.post("/api/v1/projects/no-such/materiality/approve", headers=partner_h).status_code == 404

    # 合伙人报批：approved_by 落库 + 计划门条件 materiality_confirmed 置位（R8）
    resp = client.post(f"/api/v1/projects/{project_id}/materiality/approve", headers=partner_h)
    assert resp.status_code == 200
    assert resp.json()["approved_by"] is not None
    assert _plan_gate(db, project_id).conditions_snapshot["materiality_confirmed"] is True

    # 项目经理修改：change_log 全量留痕 + 重置报批 + 计划门标记同步撤回
    resp = client.post(f"/api/v1/projects/{project_id}/materiality", json={
        **_MAT, "basis_amount": "200000000", "exec_ratio_pct": 70,
    }, headers=manager_h)
    assert resp.status_code == 200
    assert float(resp.json()["overall_materiality"]) == 1000000.0
    row = db.query(Materiality).filter(Materiality.project_id == project_id).one()
    assert len(row.change_log) == 1 and row.change_log[0]["by"] == "王经理"
    assert row.approved_by is None
    assert _plan_gate(db, project_id).conditions_snapshot["materiality_confirmed"] is False

    # 质控无权批准（VIEW < APPROVE，硬约束 5）
    assert client.post(
        f"/api/v1/projects/{project_id}/materiality/approve", headers=_login(client, "qc")
    ).status_code == 403

    # 重新报批通过，整链哈希可验证
    assert client.post(f"/api/v1/projects/{project_id}/materiality/approve", headers=partner_h).status_code == 200
    assert verify_chain(db), "R8 全链留痕须可整链验证（硬约束 4）"


def test_materiality_exec_ratio_out_of_range_rejected(client, db):
    """R8 参数边界：实际执行比例须在 50%–75%，越界 422 且不留痕。"""
    project_id = _setup(client, db)
    resp = client.post(f"/api/v1/projects/{project_id}/materiality", json={
        **_MAT, "exec_ratio_pct": 80,
    }, headers=_login(client, "manager"))
    assert resp.status_code == 422
    assert db.query(Materiality).count() == 0


def test_finalized_project_writes_blocked(client, db):
    """终态拦截（§15-17，与 quality 同口径）：已归档项目一切写操作 409 PL-1006。"""
    project_id = _setup(client, db)
    manager_h = _login(client, "manager")
    db.query(AuditProject).filter_by(id=project_id).update({"status": "已归档"})
    db.commit()
    resp = client.post(
        f"/api/v1/projects/{project_id}/materiality", json=_MAT, headers=manager_h,
    )
    assert resp.status_code == 409 and "PL-1006" in resp.text
    assert db.query(Materiality).count() == 0


def test_risk_confirm_lock_and_full_program_package(client, db):
    """FR-02-02/03：登记默认「待确认」（硬约束 3）→ 确认可改评级 → 锁定 → 中风险全程序包。"""
    project_id = _setup(client, db)
    manager_h = _login(client, "manager")

    # AI 线索登记：默认待确认，不得直接生效
    resp = client.post(f"/api/v1/projects/{project_id}/risks", json={
        "source": "ai", "description": "收入确认存在截止风险", "risk_level": "高",
        "assertion_mapping": "营业收入/截止",
    }, headers=manager_h)
    assert resp.status_code == 201, resp.text
    risk = resp.json()
    assert risk["status"] == "待确认"
    risk_id = risk["id"]

    # 评级未确认不得生成程序（PL-1006）
    resp = client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/programs",
                       json=[{"program_code": "P-01"}], headers=manager_h)
    assert resp.status_code == 409 and "PL-1006" in resp.text

    # 确认时修改评级（高→中），再确认即锁定
    resp = client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/confirm",
                       json={"risk_level": "中"}, headers=manager_h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "已确认" and resp.json()["risk_level"] == "中"
    assert client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/confirm",
                       headers=manager_h).status_code == 409

    # 中风险 → 全程序包（R5），程序初始「未执行」
    resp = client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/programs",
                       json=[{"program_code": "P-01"}, {"program_code": "P-02"}], headers=manager_h)
    assert resp.status_code == 200, resp.text
    pkgs = resp.json()
    assert len(pkgs) == 2
    assert all(p["package_type"] == "高风险全程序" for p in pkgs)
    assert all(p["min_standard_note"] is None and p["exec_status"] == "未执行" for p in pkgs)
    assert verify_chain(db)


def test_low_risk_simplified_package_requires_standard_note(client, db):
    """R5 三分支之低风险：缺准则最低底线标注 → 422；带标注 → 简化包且标注落库。"""
    project_id = _setup(client, db)
    manager_h = _login(client, "manager")
    resp = client.post(f"/api/v1/projects/{project_id}/risks", json={
        "source": "人工", "description": "低风险科目常规检查", "risk_level": "低",
    }, headers=manager_h)
    assert resp.status_code == 201
    risk_id = resp.json()["id"]
    assert client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/confirm",
                       headers=manager_h).status_code == 200

    # 缺标注：engine.select_package 拒绝 → 422
    resp = client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/programs",
                       json=[{"program_code": "P-10"}], headers=manager_h)
    assert resp.status_code == 422 and "R5" in resp.text

    # 带标注：简化包成立，标注原样落库
    resp = client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/programs", json=[
        {"program_code": "P-10", "min_standard_note": "函证准则最低要求：重要余额函证不可省略"},
    ], headers=manager_h)
    assert resp.status_code == 200, resp.text
    pkg = resp.json()[0]
    assert pkg["package_type"] == "低风险简化程序包"
    assert "函证准则最低要求" in pkg["min_standard_note"]


def test_paused_project_freezes_planning_writes(client, db):
    """§15-17：项目暂停期间计划域写操作一律 409 PL-1005（测试夹具模拟引擎流转结果）。"""
    project_id = _setup(client, db)
    project = db.get(AuditProject, project_id)
    project.status = "暂停"
    db.commit()

    manager_h = _login(client, "manager")
    resp = client.post(f"/api/v1/projects/{project_id}/materiality", json=_MAT, headers=manager_h)
    assert resp.status_code == 409 and "PL-1005" in resp.text
    resp = client.post(f"/api/v1/projects/{project_id}/risks", json={
        "source": "人工", "description": "暂停期登记", "risk_level": "中",
    }, headers=manager_h)
    assert resp.status_code == 409 and "PL-1005" in resp.text
