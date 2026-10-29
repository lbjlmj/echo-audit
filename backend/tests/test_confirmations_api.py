"""FR-04 函证域集成测试：权限矩阵 / 八状态全流 / 差异-替代 / 超期扫描 / 出站白名单。

八状态只走 engine（硬约束 2）；回函录入自动核对（FR-04-03）；
超期 30 天转「超期未回」、15 天提醒×2、两次后建议替代程序只留痕不转态（§15-7/FR-04-04）；
顺丰适配器调用必经出站白名单（硬约束 7），核心级客户一律禁出站（§4.2.4）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.audit_middleware import verify_chain
from app.integrations.egress import EgressBlocked, assert_egress_allowed
from app.integrations.express import WaybillRequest
from app.integrations.sf_express import SFExpressAdapter
from app.models.confirmation import Confirmation
from app.models.project import AuditProject
from app.models.user import User
from app.services.auth import hash_password
from app.services.confirmations import run_overdue_scan

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


def _setup(client, db) -> str:
    """五角色 + 客户 + 已批准项目，返回 project_id（函证域以已批准项目为前置）。"""
    from app.models.project import Client

    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(client_row)
    db.commit()
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    resp = client.post("/api/v1/projects", json={
        "name": "示例公司 2025 年报审计", "client_id": client_row.id,
        "business_type": "商贸", "period": "2025 年度",
        "evaluation": _EVAL_OK, "members": [],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    assert client.post(
        f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner")
    ).status_code == 200
    return project_id


def _mkledger(client, project_id: str, amounts: tuple[str, ...]) -> list[str]:
    """审计员生成函证台账（FR-04-01），返回按序 confirmation_id 列表。"""
    items = [
        {"type": "银行" if i == 0 else "往来", "counterparty": f"往来单位{i}有限公司", "amount": a}
        for i, a in enumerate(amounts)
    ]
    resp = client.post("/api/v1/confirmations", json={"project_id": project_id, "items": items},
                       headers=_login(client, "auditor"))
    assert resp.status_code == 201, resp.text
    return [r["id"] for r in resp.json()]


def test_permission_matrix_and_ledger_generation(client, db):
    """权限矩阵（PRD §3）：质控 worksheet.edit=查看不可编制台账；审计员可生成（FR-04-01）。"""
    project_id = _setup(client, db)
    qc_h, auditor_h = _login(client, "qc"), _login(client, "auditor")

    # 质控（VIEW < OPERATE）→ 403
    resp = client.post("/api/v1/confirmations", json={
        "project_id": project_id,
        "items": [{"type": "银行", "counterparty": "质控越权银行", "amount": "1000"}],
    }, headers=qc_h)
    assert resp.status_code == 403

    # 审计员生成 3 笔台账，全部进入「台账已生成」（§4.4 八状态起点）
    resp = client.post("/api/v1/confirmations", json={
        "project_id": project_id,
        "items": [
            {"type": "银行", "counterparty": "工商银行示例支行", "amount": "1000000.00"},
            {"type": "往来", "counterparty": "甲公司", "amount": "5000.00"},
            {"type": "往来", "counterparty": "乙公司", "amount": "300.50"},
        ],
    }, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    rows = resp.json()
    assert [r["status"] for r in rows] == ["台账已生成"] * 3
    assert Decimal(str(rows[0]["amount"])) == Decimal("1000000.00")

    # 不存在项目 → 404 CF-1002；金额非正 → 422（Schema 拒绝）
    assert client.post("/api/v1/confirmations", json={
        "project_id": "no-such-project",
        "items": [{"type": "银行", "counterparty": "某银行", "amount": "1"}],
    }, headers=auditor_h).status_code == 404
    assert client.post("/api/v1/confirmations", json={
        "project_id": project_id,
        "items": [{"type": "银行", "counterparty": "某银行", "amount": "0"}],
    }, headers=auditor_h).status_code == 422

    # 暂停项目禁止写操作（§15-17）→ 409 CF-1005（夹具模拟引擎流转结果）
    project = db.get(AuditProject, project_id)
    project.status = "暂停"
    db.commit()
    resp = client.post("/api/v1/confirmations", json={
        "project_id": project_id,
        "items": [{"type": "银行", "counterparty": "暂停期银行", "amount": "1"}],
    }, headers=auditor_h)
    assert resp.status_code == 409 and "CF-1005" in resp.text
    assert verify_chain(db), "拦截与台账留痕必须续哈希链（硬约束 4）"


def test_full_flow_issue_reply_matched_close(client, db):
    """八状态正向流（FR-04-02/03）：发出→回函一致自动核对→闭环；非法流转拒绝。"""
    project_id = _setup(client, db)
    h = _login(client, "auditor")
    cf1, cf2 = _mkledger(client, project_id, ("1000.00", "800.00"))

    # 发出前回函：台账已生成→已回函 为非法流转 → 409 CF-1001（engine 拒绝，硬约束 2）
    resp = client.post(f"/api/v1/confirmations/{cf2}/reply",
                       json={"reply_amount": "800", "reply_doc": "replies/x.pdf"}, headers=h)
    assert resp.status_code == 409 and "CF-1001" in resp.text

    # 发出：空白单号（strip 后为空，服务层拒）→ 422 CF-1007；短单号 Schema 拒 → 422
    resp = client.post(f"/api/v1/confirmations/{cf1}/issue", json={"waybill_no": "    "}, headers=h)
    assert resp.status_code == 422 and "CF-1007" in resp.text
    assert client.post(f"/api/v1/confirmations/{cf1}/issue",
                       json={"waybill_no": "12"}, headers=h).status_code == 422

    resp = client.post(f"/api/v1/confirmations/{cf1}/issue",
                       json={"waybill_no": "SF1026000001"}, headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "已发出" and body["waybill_no"] == "SF1026000001"
    assert body["issued_at"] is not None

    # 回函金额一致（容差 0.01 元）→ 一次录入自动转「核对一致」（FR-04-03）
    resp = client.post(f"/api/v1/confirmations/{cf1}/reply",
                       json={"reply_amount": "1000.00", "reply_doc": "replies/bank01.pdf"}, headers=h)
    assert resp.status_code == 200 and resp.json()["status"] == "核对一致"

    resp = client.post(f"/api/v1/confirmations/{cf1}/close", headers=h)
    assert resp.status_code == 200 and resp.json()["status"] == "闭环"
    # 闭环 → 闭环：非法流转 → 409
    assert client.post(f"/api/v1/confirmations/{cf1}/close", headers=h).status_code == 409
    assert verify_chain(db), "全流留痕须可整链验证（硬约束 4）"


def test_difference_flow_alt_test_then_close(client, db):
    """差异流（FR-04-03/04）：回函差异→差异处理（落差异 JSON）→替代程序→闭环。"""
    project_id = _setup(client, db)
    h = _login(client, "auditor")
    (cf,) = _mkledger(client, project_id, ("500.00",))

    assert client.post(f"/api/v1/confirmations/{cf}/issue",
                       json={"waybill_no": "SF1026000002"}, headers=h).status_code == 200
    # 回函金额不等 → 自动转「差异处理」，差异三元组落 JSON
    resp = client.post(f"/api/v1/confirmations/{cf}/reply",
                       json={"reply_amount": "480.00", "reply_doc": "replies/bank02.pdf"}, headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "差异处理"
    assert body["difference"] == {"回函金额": 480.0, "台账金额": 500.0, "差异": -20.0}

    # 替代程序须附记录：缺失 → 422
    assert client.post(f"/api/v1/confirmations/{cf}/alt",
                       json={"alt_test_doc": " "}, headers=h).status_code == 422
    resp = client.post(f"/api/v1/confirmations/{cf}/alt",
                       json={"alt_test_doc": "已执行期后收款核查并取得银行流水（替代测试）"}, headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "替代程序" and "期后收款" in body["alt_test_record"]

    resp = client.post(f"/api/v1/confirmations/{cf}/close", headers=h)
    assert resp.status_code == 200 and resp.json()["status"] == "闭环"
    assert verify_chain(db)


def test_overdue_scan_rules(client, db):
    """超期驱动（§15-7/FR-04-04）：满 30 天转「超期未回」，15 天提醒×2，两次后建议替代只留痕。"""
    project_id = _setup(client, db)
    h = _login(client, "auditor")
    cf_a, cf_b = _mkledger(client, project_id, ("100.00", "200.00"))
    for cf in (cf_a, cf_b):
        assert client.post(f"/api/v1/confirmations/{cf}/issue",
                           json={"waybill_no": f"SF{cf[-8:]}"}, headers=h).status_code == 200

    now = datetime.now(tz=timezone.utc)
    row_a = db.get(Confirmation, cf_a)
    row_b = db.get(Confirmation, cf_b)
    row_a.issued_at = now - timedelta(days=31)  # 夹具模拟时间流逝（§15-7 边界：30–44 天提醒 0 次）
    row_b.issued_at = now - timedelta(days=20)  # 未满 30 天不动作
    db.commit()

    today = datetime.now(tz=timezone.utc).date()
    result = run_overdue_scan(db, today=today)
    db.commit()
    assert result["scanned"] == 2
    assert result["actions"] == [{"confirmation_id": cf_a, "action": "转超期未回"}]
    db.refresh(row_a)
    assert row_a.status == "超期未回" and row_a.remind_count == 0
    db.refresh(row_b)
    assert row_b.status == "已发出"

    # 46 天：第 1 次提醒（45–59 天提醒 1 次）
    row_a.issued_at = now - timedelta(days=46)
    db.commit()
    result = run_overdue_scan(db, today=today)
    db.commit()
    db.refresh(row_a)
    assert result["actions"] == [{"confirmation_id": cf_a, "action": "超期提醒"}]
    assert row_a.remind_count == 1

    # 61 天：第 2 次提醒（60 天起提醒 2 次，达上限）
    row_a.issued_at = now - timedelta(days=61)
    db.commit()
    run_overdue_scan(db, today=today)
    db.commit()
    db.refresh(row_a)
    assert row_a.remind_count == 2

    # 再次扫描：累计两次 → 建议替代程序——只留痕，不自动转态（FR-04-04）
    result = run_overdue_scan(db, today=today)
    db.commit()
    db.refresh(row_a)
    assert result["actions"] == [{"confirmation_id": cf_a, "action": "建议替代程序"}]
    assert row_a.status == "超期未回"

    # 人工执行替代程序 → 闭环
    resp = client.post(f"/api/v1/confirmations/{cf_a}/alt",
                       json={"alt_test_doc": "超期未回，执行期后收款核查（替代测试）"}, headers=h)
    assert resp.status_code == 200 and resp.json()["status"] == "替代程序"
    assert client.post(f"/api/v1/confirmations/{cf_a}/close", headers=h).json()["status"] == "闭环"
    assert verify_chain(db), "Beat 系统留痕（beat-scheduler）也必须续哈希链（硬约束 4）"


def test_ledger_list_and_trail_readonly(client, db):
    """台账列表/留痕时间线只读端点：质控（VIEW）可读；留痕时间正序含 Beat 侧动作可追溯。"""
    project_id = _setup(client, db)
    (cf,) = _mkledger(client, project_id, ("600.00",))

    # 质控 worksheet.edit=查看 → 只读可看台账状态（FR-04 台账全程可见）
    resp = client.get(f"/api/v1/confirmations?project_id={project_id}", headers=_login(client, "qc"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1 and rows[0]["status"] == "台账已生成"

    assert client.post(f"/api/v1/confirmations/{cf}/issue",
                       json={"waybill_no": "SF1026000009"}, headers=_login(client, "auditor")
                       ).status_code == 200
    resp = client.get(f"/api/v1/confirmations/{cf}/trails", headers=_login(client, "auditor"))
    assert resp.status_code == 200, resp.text
    trails = resp.json()
    assert [t["action"] for t in trails] == ["函证台账生成", "函证发出"]
    assert trails[1]["operator"] == "赵审计员"

    # 不存在函证 → 404 CF-1002
    assert client.get("/api/v1/confirmations/no-such-cf/trails",
                      headers=_login(client, "auditor")).status_code == 404
    assert verify_chain(db)


def test_sf_express_egress_whitelist_negative():
    """出站白名单（硬约束 7）：默认顺丰开关关闭，适配器出站一律被拦；核心级客户一律禁出站。"""
    adapter = SFExpressAdapter()
    req = WaybillRequest(
        project_no="P-2025-001", recipient_name="张三",
        recipient_phone="13800000000", recipient_address="北京市朝阳区示例路 1 号",
    )
    with pytest.raises(EgressBlocked):
        adapter.create_waybill(req)
    with pytest.raises(EgressBlocked):
        adapter.query_track("SF1026000001")

    # §4.2.4：核心级客户数据禁止一切云端通道出站（即便未来开通顺丰通道）
    with pytest.raises(EgressBlocked):
        assert_egress_allowed("sf-api.example.invalid", client_data_level="核心")
