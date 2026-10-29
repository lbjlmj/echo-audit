"""RV 复核工作台域集成测试（§5.5/§12 队列+时间线）：只读视图 + R1 前置标注。

状态流转不落 RV 域（硬约束 2/9）：铺链一律走 /worksheets 复核端点；
本域断言待复核队列行（权限门 + can_take R1 标注）与全部轮次意见往返时间线。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.material import Material
from app.models.user import User
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


def _setup(client, db) -> str:
    """客户 + 模板 + 准则锚点 + 已批准项目 + 四角色，返回 project_id。"""
    from app.models.knowledge import RuleAnchor, Template
    from app.models.project import Client

    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(client_row)
    tpl = Template(type="商贸", version="v2025", skeleton_key="templates/trade/v2025.xlsx")
    anchor = RuleAnchor(standard_no="1301", clause="第十章", purpose="函证程序底线要求")
    db.add_all([tpl, anchor])
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


def _pending_worksheet(client, db, project_id: str, *, is_important: bool = False) -> str:
    """铺链至待复核：建底稿（审计员）→ 三层材料 → AI 初稿 → 经理采信落版 → 提交。"""
    from app.models.knowledge import RuleAnchor, Template

    for lv in ("customer", "firm", "rule"):
        db.add(Material(
            project_id=project_id, level=lv, file_key=f"materials/{project_id}/{lv}.xlsx",
            parse_status="已解析", gap_flag=False,
        ))
    db.commit()
    tpl = db.query(Template).first()
    anchor = db.query(RuleAnchor).first()
    auditor_h, manager_h = _login(client, "auditor"), _login(client, "manager")
    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金底稿", "template_id": tpl.id,
        "rule_anchor_id": anchor.id, "is_important": is_important,
    }, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    ws_id = resp.json()["id"]
    run_no = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h).json()["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review", json={
        "conclusion": "采信",
    }, headers=manager_h).status_code == 200
    assert client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=auditor_h).status_code == 200
    return ws_id


def test_pending_queue_permission_and_r1_flag(client, db):
    """待复核队列：审计员无复核签发权限 403；can_take 标注编制人=复核人拒绝（R1 前置）。"""
    project_id = _setup(client, db)
    ws_id = _pending_worksheet(client, db, project_id)
    auditor_h, manager_h, partner_h, qc_h = (
        _login(client, "auditor"), _login(client, "manager"),
        _login(client, "partner"), _login(client, "qc"),
    )

    # 审计员复核签发=无（PRD §3）→ 不进复核工作台队列，403
    assert client.get("/api/v1/reviews/pending", headers=auditor_h).status_code == 403

    # 合伙人视角：队列含底稿，编制人=采信落版人（王经理）→ 合伙人可受理
    body = client.get("/api/v1/reviews/pending", headers=partner_h).json()
    assert len(body) == 1
    row = body[0]
    assert row["worksheet_id"] == ws_id and row["code"].endswith("-WS001")
    assert row["prepared_by_name"] == "王经理" and row["next_round_no"] == 1
    assert row["can_take"] is True and row["is_important"] is False

    # 经理视角：编制人（采信落版人）=本人 → R1 前置标注 False（权威拦截在 take 端点）
    row = client.get("/api/v1/reviews/pending", headers=manager_h).json()[0]
    assert row["can_take"] is False

    # 质控视角：质控复核（OPERATE）→ 可受理
    row = client.get("/api/v1/reviews/pending", headers=qc_h).json()[0]
    assert row["can_take"] is True

    # 经理本人受理 → R1 权威拦截 403（队列标注与后端裁决一致）
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=manager_h).status_code == 403

    # 合伙人受理后行转复核中：仍在队列（供出具结论）但 can_take=False，status 字段区分段
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=partner_h).status_code == 200
    row = client.get("/api/v1/reviews/pending", headers=partner_h).json()[0]
    assert row["status"] == "复核中" and row["can_take"] is False


def test_review_timeline_round_trip(client, db):
    """时间线：退回→重提→通过两轮意见往返按轮次升序；电子签名随轮次落痕（FR-05-02）。"""
    project_id = _setup(client, db)
    ws_id = _pending_worksheet(client, db, project_id)
    partner_h, auditor_h = _login(client, "partner"), _login(client, "auditor")
    url = f"/api/v1/reviews/worksheets/{ws_id}/timeline"

    # 铺链后先看初始时间线：零轮次，状态=待复核
    body = client.get(url, headers=auditor_h).json()
    assert body["status"] == "待复核" and body["records"] == []

    # 不存在底稿 → 404 RV-1002
    resp = client.get("/api/v1/reviews/worksheets/NOPE/timeline", headers=partner_h)
    assert resp.status_code == 404 and "RV-1002" in resp.text

    # 第 1 轮：受理 → 退回（必附意见）
    client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=partner_h)
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/conclude", json={
        "conclusion": "退回", "opinion": "截止测试样本不足", "revision_requirement": "补充 12 月凭证",
    }, headers=partner_h).status_code == 200

    # 编制人（审计员）可读退回意见（时间线不设权限断言，FR-05-02 留痕可追溯）
    body = client.get(url, headers=auditor_h).json()
    assert body["status"] == "草稿" and len(body["records"]) == 1
    first = body["records"][0]
    assert first["round_no"] == 1 and first["conclusion"] == "退回"
    assert first["opinion"] == "截止测试样本不足" and first["revision_requirement"] == "补充 12 月凭证"
    assert first["reviewer_name"] == "李合伙人"

    # 第 2 轮：重提 → 受理 → 通过（电子签名）
    client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=auditor_h)
    client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=partner_h)
    client.post(f"/api/v1/worksheets/{ws_id}/review/conclude", json={
        "conclusion": "通过",
    }, headers=partner_h)

    body = client.get(url, headers=auditor_h).json()
    assert body["status"] == "复核通过" and len(body["records"]) == 2
    second = body["records"][1]
    assert second["round_no"] == 2 and second["conclusion"] == "通过"
    assert second["signature"]["复核人"] == "李合伙人" and second["signature"]["证书号"].startswith("CERT-")
    assert verify_chain(db), "复核动作留痕须可整链验证（硬约束 4）"
