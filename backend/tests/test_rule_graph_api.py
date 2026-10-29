"""FR-07-02 知识图谱集成测试：图谱构建（版本化）+ 条款上下文（固定路径防幻觉）。

邻接边只存显式关联（上下位/引用/关联三类）；单跳分类视图不做传递闭包推断——
无显式边的法规不得出现在结果中（防幻觉负向断言）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.knowledge import RuleAnchor, RuleRelation
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


def _setup(client, db) -> dict[str, str]:
    """三锚点 + 三角色：A=准则总则（上位）、B=应用指南（下位）、C=关联监管规定。"""
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    a = RuleAnchor(standard_no="1101", clause="第一章", purpose="注册会计师总体目标")
    b = RuleAnchor(standard_no="1301", clause="第十章", purpose="函证程序底线要求")
    c = RuleAnchor(standard_no="1502", clause="第三章", purpose="重要性水平确定")
    db.add_all([a, b, c])
    db.commit()
    return {"A": a.id, "B": b.id, "C": c.id}


def test_rule_graph_build_and_clause_context(client, db):
    """构建（质控 OPERATE / 经理 403）→ 条款上下文分类视图 → 防幻觉负向 → 版本化留痕。"""
    ids = _setup(client, db)
    qc_h, manager_h = _login(client, "qc"), _login(client, "manager")

    # 权限：准则库更新构建=质控职能（knowledge.write OPERATE）；经理 PROPOSE 403
    edges = [
        {"rule_from_id": ids["A"], "rule_to_id": ids["B"], "relation_type": "上下位"},
        {"rule_from_id": ids["A"], "rule_to_id": ids["C"], "relation_type": "关联"},
        {"rule_from_id": ids["B"], "rule_to_id": ids["A"], "relation_type": "引用"},
    ]
    assert client.post("/api/v1/knowledge/graph/build",
                       json={"relations": edges}, headers=manager_h).status_code == 403
    resp = client.post("/api/v1/knowledge/graph/build", json={"relations": edges}, headers=qc_h)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["node_count"] == 3 and body["edge_count"] == 3
    assert body["version"].startswith("gv-")

    # 非法关系类型 → 422；边端点锚点不存在 → 404
    bad = [{"rule_from_id": ids["A"], "rule_to_id": ids["B"], "relation_type": "等同"}]
    assert client.post("/api/v1/knowledge/graph/build",
                       json={"relations": bad}, headers=qc_h).status_code == 422
    bad = [{"rule_from_id": "NOPE", "rule_to_id": ids["B"], "relation_type": "关联"}]
    assert client.post("/api/v1/knowledge/graph/build",
                       json={"relations": bad}, headers=qc_h).status_code == 404

    # 条款上下文（A=最上位）：下位=B、关联=C、被 B 引用；无上位
    resp = client.get(f"/api/v1/knowledge/graph/clause/{ids['A']}", headers=manager_h)
    assert resp.status_code == 200, resp.text
    ctx = resp.json()
    assert ctx["version"] == body["version"]
    assert [n["standard_no"] for n in ctx["lower"]] == ["1301"]
    assert [n["standard_no"] for n in ctx["upper"]] == []
    assert [n["standard_no"] for n in ctx["cited_by"]] == ["1301"]
    assert [n["standard_no"] for n in ctx["related"]] == ["1502"]
    assert ctx["note"].startswith("仅显式关联")

    # 防幻觉：C（监管规定）与 B（应用指南）无显式边 → 互不可见，不传递推断
    for anchor_id, forbidden in ((ids["C"], "1301"), (ids["B"], "1502")):
        ctx = client.get(f"/api/v1/knowledge/graph/clause/{anchor_id}", headers=qc_h).json()
        all_visible = [n["standard_no"] for n in
                       ctx["upper"] + ctx["lower"] + ctx["citing"] + ctx["cited_by"] + ctx["related"]]
        assert forbidden not in all_visible, f"无显式边的法规不得出现（防幻觉）"

    # 图谱概览：版本一致 + 边数一致
    overview = client.get("/api/v1/knowledge/graph", headers=qc_h).json()
    assert overview["version"] == body["version"] and len(overview["edges"]) == 3

    # 版本化随准则库更新：边集变化 → 版本变化；构建留痕续链
    edges2 = edges[:2]  # 移除引用边
    body2 = client.post("/api/v1/knowledge/graph/build",
                        json={"relations": edges2}, headers=qc_h).json()
    assert body2["version"] != body["version"] and body2["edge_count"] == 2
    assert db.query(RuleRelation).count() == 2, "全量替换旧版本边集"
    assert verify_chain(db), "图谱构建留痕须可整链验证（硬约束 4）"
