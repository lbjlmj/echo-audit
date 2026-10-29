"""FR-07-04 知识域集成测试：§15-3 入库审批链权限矩阵 / 状态机负向 / 归档自动触发经验回流。

经验沉淀提案（新底稿模式/典型问题/AI 不采信脱敏样例）由归档动作自动触发，恒为「提案」态
（硬约束 3 同源逻辑）；审批链=提案/提交/质控免自审写入 → 质控审核 → 合伙人批准（驳回必附理由）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.audit import AIRun
from app.models.finding import AuditReport
from app.models.knowledge import Template
from app.models.user import User
from app.services.auth import hash_password


def _mkuser(db, login_name: str, role: str) -> User:
    user = User(
        name={"manager": "王经理", "partner": "李合伙人", "auditor": "赵审计员",
              "qc": "钱质控"}.get(login_name, login_name),
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


def _setup(db) -> None:
    """四角色最小建档（知识审批流不依赖项目）。"""
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")


def test_knowledge_approval_chain_permissions(client, db):
    """权限矩阵（PRD §3 知识库写入行）与审批状态机（§15-3）：越段/终态/驳回缺理由负向。"""
    _setup(db)
    auditor_h, manager_h, partner_h, qc_h = (
        _login(client, "auditor"), _login(client, "manager"),
        _login(client, "partner"), _login(client, "qc"),
    )

    # 三种入口按角色落审批起点（§15-3）：审计员提案 / 经理提交 / 质控免自审
    resp = client.post("/api/v1/knowledge/items", json={
        "item_type": "本所沉淀经验", "content": "商贸行业存货监盘常见漏项清单",
    }, headers=auditor_h)
    assert resp.status_code == 201 and resp.json()["status"] == "提案"
    auditor_item = resp.json()["id"]

    resp = client.post("/api/v1/knowledge/items", json={
        "item_type": "本所沉淀经验", "content": "函证回函差异处理口径（2025 版）",
    }, headers=manager_h)
    assert resp.status_code == 201 and resp.json()["status"] == "质控审核中"
    manager_item = resp.json()["id"]

    resp = client.post("/api/v1/knowledge/items", json={
        "item_type": "准则", "content": "货币资金监盘程序所内执行要点（免自审直接写入）",
    }, headers=qc_h)
    assert resp.status_code == 201 and resp.json()["status"] == "待合伙人批准"
    qc_item = resp.json()["id"]

    # 审核/批准权限负向：审计员、经理不可审核；经理、质控不可批准
    assert client.post(f"/api/v1/knowledge/items/{auditor_item}/review", headers=auditor_h).status_code == 403
    assert client.post(f"/api/v1/knowledge/items/{auditor_item}/review", headers=manager_h).status_code == 403
    assert client.post(f"/api/v1/knowledge/items/{qc_item}/approve", headers=manager_h).status_code == 403
    assert client.post(f"/api/v1/knowledge/items/{qc_item}/approve", headers=qc_h).status_code == 403

    # 质控审核拾取提案与提交 → 待合伙人批准；免自审条目无需审核（409）
    for item_id in (auditor_item, manager_item):
        resp = client.post(f"/api/v1/knowledge/items/{item_id}/review", headers=qc_h)
        assert resp.status_code == 200 and resp.json()["status"] == "待合伙人批准"
    resp = client.post(f"/api/v1/knowledge/items/{qc_item}/review", headers=qc_h)
    assert resp.status_code == 409 and "KN-1006" in resp.text

    # 批准走通：合伙人 → 已发布；终态再审核/再批准 → 409
    resp = client.post(f"/api/v1/knowledge/items/{auditor_item}/approve", headers=partner_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已发布"
    assert client.post(f"/api/v1/knowledge/items/{auditor_item}/approve", headers=partner_h).status_code == 409

    # 驳回：缺理由 422（KN-1007）；有理由 → 已驳回终态；驳回后批准 → 409
    assert client.post(f"/api/v1/knowledge/items/{manager_item}/reject", json={"reject_reason": "  "},
                       headers=partner_h).status_code == 422
    resp = client.post(f"/api/v1/knowledge/items/{manager_item}/reject",
                       json={"reject_reason": "口径与现行准则不一致，退回修订"}, headers=partner_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已驳回"
    assert resp.json()["reject_reason"] == "口径与现行准则不一致，退回修订"
    assert client.post(f"/api/v1/knowledge/items/{manager_item}/approve", headers=partner_h).status_code == 409

    # 不存在的条目 → 404；清单过滤（status）
    assert client.post("/api/v1/knowledge/items/不存在/review", headers=qc_h).status_code == 404
    resp = client.get("/api/v1/knowledge/items?status=待合伙人批准", headers=qc_h)
    assert resp.status_code == 200 and len(resp.json()) == 1
    assert verify_chain(db), "知识审批流留痕必须续哈希链（硬约束 4）"


def test_archive_triggers_experience_proposals(client, db):
    """FR-07-04 归档自动触发：归档收口生成 3 类沉淀提案（提案态），不采信样例脱敏（§15-16）。"""
    from app.models.project import AuditProject, Client
    from app.models.worksheet import Worksheet

    from app.models.knowledge import RuleAnchor

    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(client_row)
    tpl = Template(type="商贸", version="v2025", skeleton_key="templates/trade/v2025.xlsx")
    anchor = RuleAnchor(standard_no="1301", clause="第十章", purpose="监盘程序底线要求")
    db.add_all([tpl, anchor])
    db.commit()
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    auditor_h, manager_h, qc_h = _login(client, "auditor"), _login(client, "manager"), _login(client, "qc")

    resp = client.post("/api/v1/projects", json={
        "name": "示例公司 2025 年报审计", "client_id": client_row.id,
        "business_type": "商贸", "period": "2025 年度",
        "evaluation": {
            "independence_ok": True, "client_risk_ok": True,
            "competence_ok": True, "engagement_letter_ok": True,
        },
        "members": [],
    }, headers=manager_h)
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    assert client.post(f"/api/v1/projects/{project_id}/approve",
                       headers=_login(client, "partner")).status_code == 200

    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金底稿", "template_id": tpl.id,
        "rule_anchor_id": anchor.id, "is_important": True,
    }, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    worksheet_id = resp.json()["id"]

    # 典型问题（人工登记）→ 沉淀「典型问题与处理结论」素材
    resp = client.post(f"/api/v1/findings", json={
        "project_id": project_id, "worksheet_id": worksheet_id,
        "items": [{"description": "大额现金收支缺乏审批线索"}], "source": "人工",
    }, headers=auditor_h)
    assert resp.status_code == 201, resp.text

    # AI 不采信样例（§15-16 四分类）：线索 run → 经理不采信（幻觉）
    partner = db.query(User).filter_by(login_name="partner").first()
    qc_id = db.query(User).filter_by(login_name="qc").first().id
    db.add(AIRun(
        run_no="AI-20261028-KN00000001", project_id=project_id, module="ai_finding",
        skill_version="finding@1.0.0", model_version="rules@1.0.0",
        triggered_by=qc_id, input_material_ids=[], input_hashes={}, status="线索",
        related_type="project", related_id=project_id,
    ))
    db.commit()
    resp = client.post("/api/v1/worksheets/ai-runs/AI-20261028-KN00000001/review",
                       json={"conclusion": "不采信", "rejection_reason": "幻觉"}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "不采信"

    # 造齐归档前置：重要底稿终审 + 报告已签发 + 质控检查已采信
    partner_id = partner.id
    db.add(AuditReport(project_id=project_id, version="v1", status="已签发", signed_by=partner_id))
    ws = db.get(Worksheet, worksheet_id)
    ws.status = "终审通过"
    db.commit()
    run_no = client.post("/api/v1/quality/qc-checks", json={"project_id": project_id},
                         headers=qc_h).json()["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 200

    # 归档收口 → 自动触发经验回流：3 类提案（新底稿模式/典型问题/脱敏错误样例）
    resp = client.post("/api/v1/quality/archive", json={"project_id": project_id}, headers=qc_h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["knowledge_proposals"] == 3

    resp = client.get(f"/api/v1/knowledge/items?source_project_id={project_id}", headers=qc_h)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 3
    assert all(i["status"] == "提案" and i["item_type"] == "本所沉淀经验" for i in items)
    by_category = {i["content"]: i for i in items}
    assert any("新底稿模式" in c and "重要底稿" in c for c in by_category)
    assert any("大额现金收支缺乏审批线索" in c for c in by_category)
    rejected_payload = next(c for c in by_category if "AI 错误样例" in c)
    assert "幻觉" in rejected_payload and "ai_finding" in rejected_payload
    # 脱敏断言（§15-16）：不含人名与客户名
    assert "王经理" not in rejected_payload and "示例公司" not in rejected_payload

    # 提案走通审批链：质控审核 → 合伙人批准发布
    target = items[0]["id"]
    assert client.post(f"/api/v1/knowledge/items/{target}/review", headers=qc_h).status_code == 200
    resp = client.post(f"/api/v1/knowledge/items/{target}/approve", headers=_login(client, "partner"))
    assert resp.status_code == 200 and resp.json()["status"] == "已发布"
    assert verify_chain(db)
