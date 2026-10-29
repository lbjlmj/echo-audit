"""FR-07-01 准则 RAG 问答集成测试：引用门禁（硬约束 6）+ 未覆盖明示 + 问答留痕可追溯。

检索为纯 Python bigram 覆盖率（零新增依赖）；大模型通道默认关闭（§15-15）；
未过入库审批链的知识条目不得作为引用来源（§15-3）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.audit import AIRun
from app.models.knowledge import RuleAnchor
from app.models.project import Client
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
    """客户 + 函证准则锚点 + 已批准项目 + 三角色，返回 project_id。"""
    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(client_row)
    anchor = RuleAnchor(standard_no="1301", clause="第十章", purpose="函证程序底线要求")
    db.add(anchor)
    db.commit()
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
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


def test_qa_citation_gate_and_traceability(client, db):
    """引用门禁（硬约束 6）：命中必带锚点引用；未覆盖明示不强行作答；问答留痕可追溯。"""
    project_id = _setup(client, db)
    auditor_h = _login(client, "auditor")  # 任一角色可发起（PRD §5.7）

    # 命中：答案强制带准则锚点引用
    resp = client.post("/api/v1/knowledge/qa", json={
        "project_id": project_id, "question": "函证程序有什么底线要求",
    }, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["covered"] is True and body["citations"], "无引用不出答案（硬约束 6）"
    assert body["citations"][0]["standard_no"] == "1301"
    assert body["citations"][0]["clause"] == "第十章"
    assert "1301" in body["answer"] and "依据" in body["answer"]
    assert body["run_no"].startswith("AI-")

    # 未覆盖：citations 恒空 + answer 明示，不强行作答
    resp = client.post("/api/v1/knowledge/qa", json={
        "project_id": project_id, "question": "增值税怎么申报",
    }, headers=auditor_h)
    assert resp.status_code == 201
    body = resp.json()
    assert body["covered"] is False and body["citations"] == []
    assert "未覆盖" in body["answer"]

    # 项目不存在 → 404 KN-1002
    resp = client.post("/api/v1/knowledge/qa", json={
        "project_id": "NOPE", "question": "函证未回函怎么办",
    }, headers=auditor_h)
    assert resp.status_code == 404 and "KN-1002" in resp.text

    # AI 留痕：module=rag 线索态落库（问答即时消费不进业务表，红线 1 同源）
    runs = db.query(AIRun).filter(AIRun.module == "rag").all()
    assert len(runs) == 2 and all(r.status == "线索" for r in runs)
    assert all(r.related_type == "rag_qa" and r.output_digest for r in runs)

    # 问答历史可追溯（PRD §5.7）：append-only 留痕回放两条
    resp = client.get(f"/api/v1/knowledge/qa?project_id={project_id}", headers=auditor_h)
    assert resp.status_code == 200
    history = resp.json()
    assert len(history) == 2
    assert history[0]["question"] == "函证程序有什么底线要求" and history[0]["covered"] is True
    assert history[1]["covered"] is False and history[1]["operator"] == "赵审计员"
    assert verify_chain(db), "问答留痕须可整链验证（硬约束 4）"


def test_qa_only_published_knowledge_citable(client, db):
    """§15-3 入库审批链联动：仅「已发布」知识条目可作引用来源，未过审批的提案不可引用。"""
    from app.models.knowledge import KnowledgeItem, Template

    project_id = _setup(client, db)
    db.add(Template(type="商贸", version="v2025", skeleton_key="templates/trade/v2025.xlsx"))
    _mkuser(db, "qc", "质控专员")
    qc_h, partner_h = _login(client, "qc"), _login(client, "partner")

    # 质控直接写入（免自审 → 待合伙人批准）：尚未发布
    anchor = db.query(RuleAnchor).first()
    resp = client.post("/api/v1/knowledge/items", json={
        "item_type": "本所沉淀经验",
        "content": "货币资金截止测试需取得 12 月最后五天银行对账单",
        "rule_anchor_id": anchor.id,
    }, headers=qc_h)
    assert resp.status_code == 201 and resp.json()["status"] == "待合伙人批准"
    item_id = resp.json()["id"]

    # 未发布：同类问题不命中该条目（无其他语料时即未覆盖）
    resp = client.post("/api/v1/knowledge/qa", json={
        "project_id": project_id, "question": "截止测试要取哪些凭证",
    }, headers=partner_h)
    assert resp.json()["covered"] is False

    # 合伙人批准 → 已发布；同问题命中经验条目并带锚点溯源
    assert client.post(f"/api/v1/knowledge/items/{item_id}/approve", headers=partner_h).status_code == 200
    resp = client.post("/api/v1/knowledge/qa", json={
        "project_id": project_id, "question": "截止测试要取哪些凭证",
    }, headers=partner_h)
    assert resp.status_code == 201
    body = resp.json()
    assert body["covered"] is True
    assert any(c["source"] == "本所沉淀经验" for c in body["citations"])
    assert any(c.get("standard_no") == "1301" for c in body["citations"]), "经验引用须可反查准则锚点"
    assert db.query(KnowledgeItem).count() == 1
