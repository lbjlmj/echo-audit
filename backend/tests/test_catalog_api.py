"""科目目录树对接测试：种子 → catalog 端点 → template_key 创建底稿幂等登记。

种子为数据即代码资产（backend/data/ 两准则目录）；catalog 端点只读无副作用；
创建底稿传 template_key 时按 (type, version) 幂等登记 Template 行——
未知键拒绝（不允自造模板键绕过发布矩阵）。
"""
from __future__ import annotations

from app.models.knowledge import Template
from app.services.auth import hash_password

_EVAL_OK = {
    "independence_ok": True,
    "client_risk_ok": True,
    "competence_ok": True,
    "engagement_letter_ok": True,
}


def _mkuser(db, login_name: str, role: str) -> None:
    from app.models.user import User

    db.add(User(
        name={"manager": "王经理", "partner": "李合伙人", "auditor": "赵审计员"}.get(
            login_name, login_name
        ),
        login_name=login_name, password_hash=hash_password("secret66"), global_role=role,
    ))
    db.commit()


def _login(client, login_name: str) -> dict:
    token = client.post(
        "/api/v1/auth/login", json={"login_name": login_name, "password": "secret66"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _setup_project(client, db, business_type: str = "商贸") -> tuple[str, str]:
    """角色 + 客户 + 已批准项目 + 准则锚点，返回 (project_id, anchor_id)。"""
    from app.models.knowledge import RuleAnchor
    from app.models.project import Client

    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    anchor = RuleAnchor(standard_no="1301", clause="第十章", purpose="函证程序底线要求")
    db.add_all([client_row, anchor])
    db.commit()
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    resp = client.post("/api/v1/projects", json={
        "name": "示例公司 2025 年报审计", "client_id": client_row.id,
        "business_type": business_type, "period": "2025 年度",
        "evaluation": _EVAL_OK, "members": [],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    assert client.post(
        f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner")
    ).status_code == 200
    return project_id, anchor.id


def _mounted_count(tree: dict) -> int:
    """递归统计挂 template_key 的科目条目数。"""
    def _walk(groups: list[dict]) -> int:
        return sum(
            sum(1 for it in g.get("items", []) if it.get("template_key")) + _walk(g.get("subgroups", []))
            for g in groups
        )

    return _walk(tree["groups"])


def test_catalog_returns_both_standards(client, db):
    """缺省全量：两准则树、组数与挂载数与种子一致（企业 5 组挂 77 / 小企业 6 组挂 49）。"""
    _mkuser(db, "auditor", "审计员")
    h = _login(client, "auditor")
    resp = client.get("/api/v1/worksheets/catalog", headers=h)
    assert resp.status_code == 200, resp.text
    trees = resp.json()
    assert [t["standard"] for t in trees] == ["企业会计准则", "小企业会计准则"]
    cas, sme = trees
    assert len(cas["groups"]) == 6 and len(sme["groups"]) == 6  # 两准则同构（实质性程序独立顶级组）
    assert _mounted_count(cas) == 77 and _mounted_count(sme) == 49
    # 4101 科目挂载键指向模板矩阵（可发起 AI 保真写回）
    def _find_4101(tree):
        for g in tree["groups"]:
            for it in g.get("items", []):
                if it["code"] == "4101":
                    return it
            for sg in g.get("subgroups", []):
                for it in sg.get("items", []):
                    if it["code"] == "4101":
                        return it
    assert _find_4101(cas)["template_key"] == "templates/cas/4101.xlsx"
    assert _find_4101(sme)["template_key"] == "templates/sme/4101.xlsx"


def test_catalog_filter_and_unknown_standard(client, db):
    """?standard= 过滤单树；未知准则 422（宁可拒绝不给错树）。"""
    _mkuser(db, "auditor", "审计员")
    h = _login(client, "auditor")
    resp = client.get("/api/v1/worksheets/catalog", params={"standard": "小企业会计准则"}, headers=h)
    assert resp.status_code == 200
    trees = resp.json()
    assert len(trees) == 1 and trees[0]["standard"] == "小企业会计准则"
    assert trees[0]["version"] == "sme2025"

    resp = client.get("/api/v1/worksheets/catalog", params={"standard": "政府会计准则"}, headers=h)
    assert resp.status_code == 422 and "未知会计准则" in resp.text


def test_create_worksheet_by_template_key_idempotent(client, db):
    """template_key 创建底稿：Template 行按 (type, version) 幂等登记并复用。"""
    project_id, anchor_id = _setup_project(client, db)
    h = _login(client, "auditor")

    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金", "template_key": "templates/sme/4101.xlsx",
        "rule_anchor_id": anchor_id, "is_important": True,
    }, headers=h)
    assert resp.status_code == 201, resp.text
    ws = resp.json()
    tpl = db.get(Template, ws["template_id"])
    assert tpl.type == "4101 货币资金" and tpl.version == "sme2025"
    assert tpl.skeleton_key == "templates/sme/4101.xlsx"

    # 同键再建一张底稿（不同标题）：复用同一 Template 行（type, version 唯一约束不破）
    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金（主表）", "template_key": "templates/sme/4101.xlsx",
        "rule_anchor_id": anchor_id,
    }, headers=h)
    assert resp.status_code == 201, resp.text
    assert db.query(Template).filter_by(version="sme2025").count() == 1


def test_create_worksheet_rejects_unknown_key_or_missing_both(client, db):
    """负向：未知模板键 422；template_id/template_key 都缺 422（Pydantic 校验）。"""
    project_id, anchor_id = _setup_project(client, db)
    h = _login(client, "auditor")

    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "自造模板底稿", "template_key": "templates/cas/9999.xlsx",
        "rule_anchor_id": anchor_id,
    }, headers=h)
    assert resp.status_code == 422 and "不在科目目录种子中" in resp.text

    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "无模板底稿", "rule_anchor_id": anchor_id,
    }, headers=h)
    assert resp.status_code == 422 and "必须提供其一" in resp.text
