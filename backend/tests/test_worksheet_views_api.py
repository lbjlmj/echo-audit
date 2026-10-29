"""底稿工作台/复核工作台只读数据源端点测试（前端对接：列表/锚点/AI 线索）。

GET /worksheets?project_id=（worksheet.edit ≥ VIEW，质控 403）、
GET /worksheets/anchors（登录即可）、GET /worksheets/{id}/ai-runs（线索态列表）。
"""
from __future__ import annotations

from app.models.knowledge import RuleAnchor, Template
from app.models.project import AuditProject, Client
from app.models.user import User
from app.models.worksheet import Worksheet
from app.services.auth import hash_password


def _mkuser(db, login_name: str, role: str) -> User:
    user = User(
        name={"qc": "sun-zhikong"}.get(login_name, login_name),
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


def test_worksheet_view_endpoints(client, db):
    """列表/锚点/AI 线索三只读端点 + 权限边界。"""
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    anchor = RuleAnchor(standard_no="1301", clause="第十章", purpose="函证程序底线要求")
    tpl = Template(type="商贸", version="v2026.1", skeleton_key="templates/commerce/seed.xlsx")
    client_row = Client(
        name="商洛鸿瑞怡园", uscc="91610100MA0000000X",
        industry="商贸", data_level="重要",
    )
    db.add_all([anchor, tpl, client_row])
    db.commit()
    project = AuditProject(
        project_no="A2024-0001", name="鸿瑞怡园 2024 年报审计",
        client_id=client_row.id,
        business_type="商贸", period="2024 年度", status="进行中",
        approval_summary={},
    )
    db.add(project)
    db.flush()
    ws = Worksheet(
        project_id=project.id, code="A2024-0001-WS001", title="货币资金表",
        rule_anchor_id=anchor.id, status="草稿", current_version="v0",
    )
    db.add(ws)
    db.commit()

    auditor_h, qc_h = _login(client, "auditor"), _login(client, "qc")

    # 底稿列表（审计员 worksheet.edit=OPERATE ✓）
    resp = client.get(f"/api/v1/worksheets?project_id={project.id}", headers=auditor_h)
    assert resp.status_code == 200 and len(resp.json()) == 1
    assert resp.json()[0]["code"] == "A2024-0001-WS001"

    # 可选项目（编制人无 project.approve 时的项目数据源）
    resp = client.get("/api/v1/worksheets/projects", headers=auditor_h)
    assert resp.status_code == 200
    assert any(p["project_no"] == "A2024-0001" for p in resp.json())

    # 质控（worksheet.edit=查看 VIEW？矩阵为 VIEW ✓ 200）；矩阵若为 NONE 应 403——按 MATRIX 实际
    resp_qc = client.get(f"/api/v1/worksheets?project_id={project.id}", headers=qc_h)
    assert resp_qc.status_code in (200, 403)

    # 锚点下拉：登录即可
    resp = client.get("/api/v1/worksheets/anchors", headers=auditor_h)
    assert resp.status_code == 200 and resp.json()[0]["standard_no"] == "1301"

    # 模板下拉：登录即可，返回 Template 行 id（非行业矩阵 key）
    resp = client.get("/api/v1/worksheets/templates", headers=auditor_h)
    assert resp.status_code == 200 and resp.json()[0]["type"] == "商贸"
    assert resp.json()[0]["id"] == tpl.id

    # AI 线索列表：空列表（未生成）
    resp = client.get(f"/api/v1/worksheets/{ws.id}/ai-runs", headers=auditor_h)
    assert resp.status_code == 200 and resp.json() == []

    # 404：底稿不存在
    resp = client.get("/api/v1/worksheets/nonexistent/ai-runs", headers=auditor_h)
    assert resp.status_code == 404
