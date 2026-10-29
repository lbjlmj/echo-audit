"""FR-02-02 风险评估 AI 集成测试：权限矩阵 / 数据前置 / 纯规则识别 / 锚点门禁 / FR-02-03 串联。

AI 产物为风险清单草稿（source=ai、待确认，硬约束 3）；每项必附准则锚点（硬约束 6：
无引用不出项，缺锚点规则跳过并留痕）；采信经 /worksheets/ai-runs review 分派（第三段标记）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.knowledge import RuleAnchor, Template
from app.models.user import User
from app.services.auth import hash_password

_VOUCHER_LINES = [
    {"voucher_no": "记-0001", "entry_date": "2025-01-05", "summary": "提现",
     "account_code": "1001", "account_name": "库存现金", "debit": "80000.00", "credit": None},
    {"voucher_no": "记-0002", "entry_date": "2025-01-08", "summary": "转账",
     "account_code": "1002", "account_name": "银行存款", "debit": "5000.00", "credit": None},
    {"voucher_no": "记-0003", "entry_date": "2025-01-15", "summary": "销售收款",
     "account_code": "6001", "account_name": "主营业务收入", "debit": None, "credit": "150000.00"},
    {"voucher_no": "记-0004", "entry_date": "2025-01-20", "summary": None,
     "account_code": "1001", "account_name": "库存现金", "debit": None, "credit": "60000.00"},
    {"voucher_no": "记-0005", "entry_date": "2025-01-25", "summary": None,
     "account_code": "5001", "account_name": "管理费用", "debit": "3000.00", "credit": None},
]


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


def _setup(client, db, *, standards: tuple[str, ...] = ("1301", "1312")) -> str:
    """五角色 + 已批准项目 + 指定标准号锚点（缺省 1301/1312），返回 project_id。"""
    from app.models.project import Client

    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(client_row)
    tpl = Template(type="商贸", version="v2025", skeleton_key="templates/trade/v2025.xlsx")
    anchors = [RuleAnchor(standard_no=no, clause=f"第{no}号准则", purpose="风险评估底线要求")
               for no in standards]
    db.add_all([tpl, *anchors])
    db.commit()
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    resp = client.post("/api/v1/projects", json={
        "name": "示例公司 2025 年报审计", "client_id": client_row.id,
        "business_type": "商贸", "period": "2025 年度",
        "evaluation": {
            "independence_ok": True, "client_risk_ok": True,
            "competence_ok": True, "engagement_letter_ok": True,
        },
        "members": [],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _import_vouchers(client, project_id: str) -> str:
    """资料域全链路：登记→到位→解析→导入明细账行，返回材料 id。"""
    auditor_h = _login(client, "auditor")
    mat = client.post("/api/v1/materials", json={
        "project_id": project_id, "level": "customer",
        "file_key": "materials/明细账.xlsx", "required": True,
    }, headers=auditor_h).json()
    assert client.post(f"/api/v1/materials/{mat['id']}/arrive", headers=auditor_h).status_code == 200
    assert client.post(f"/api/v1/materials/{mat['id']}/parse", json={"ok": True},
                       headers=auditor_h).status_code == 200
    resp = client.post(f"/api/v1/materials/{mat['id']}/voucher-lines",
                       json=_VOUCHER_LINES, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    return mat["id"]


def test_risk_ai_permissions_and_full_pipeline(client, db):
    """权限矩阵 / 明细账前置 / 三规则命中 / 重复发起拦截 / 第三段采信。"""
    project_id = _setup(client, db)
    auditor_h, qc_h, manager_h = _login(client, "auditor"), _login(client, "qc"), _login(client, "manager")

    # 审计员（plan.confirm=NONE）、质控（VIEW）不可发起 → 403
    assert client.post(f"/api/v1/projects/{project_id}/risk-ai", headers=auditor_h).status_code == 403
    assert client.post(f"/api/v1/projects/{project_id}/risk-ai", headers=qc_h).status_code == 403

    # 明细账数据为空 → 422 PL-1007（FR-03-01 资料导入前置）
    resp = client.post(f"/api/v1/projects/{project_id}/risk-ai", headers=manager_h)
    assert resp.status_code == 422 and "PL-1007" in resp.text

    _import_vouchers(client, project_id)

    # 经理发起 → 风险清单草稿（线索态）+ AI 留痕；三规则全命中且各带锚点
    resp = client.post(f"/api/v1/projects/{project_id}/risk-ai", headers=manager_h)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "线索" and body["run_no"] and body["line_count"] == 5
    assert body["skipped_no_anchor"] == []
    rules = [r["assertion_mapping"] for r in body["risks"]]
    assert len(body["risks"]) == 3
    assert all(r["source"] == "ai" and r["status"] == "待确认" and r["anchor_id"]
               and r["recommended_programs"] for r in body["risks"]), body["risks"]
    assert "存在、截止" in rules and "发生、截止" in rules and "完整性" in rules

    # 存在待确认 AI 项：重复发起 → 409 PL-1006
    assert client.post(f"/api/v1/projects/{project_id}/risk-ai", headers=manager_h).status_code == 409

    # 第三段采信：审计员（PROPOSE < OPERATE）403；经理采信 → 200
    review_url = f"/api/v1/worksheets/ai-runs/{body['run_no']}/review"
    assert client.post(review_url, json={"conclusion": "采信"}, headers=auditor_h).status_code == 403
    assert client.post(review_url, json={"conclusion": "采信"}, headers=manager_h).status_code == 200
    assert verify_chain(db), "风险评估 AI 留痕必须续哈希链（硬约束 4）"


def test_no_anchor_skip_and_confirm_chain(client, db):
    """锚点门禁（硬约束 6）：1312 锚点缺位 → 大额收入项不出并列跳过留痕；确认后可再发起。"""
    project_id = _setup(client, db, standards=("1301",))  # 只建 1301 锚点
    manager_h = _login(client, "manager")
    _import_vouchers(client, project_id)

    resp = client.post(f"/api/v1/projects/{project_id}/risk-ai", headers=manager_h)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body["risks"]) == 2  # 大额现金 + 摘要缺失（均锚 1301）
    assert all(r["anchor_id"] for r in body["risks"])
    skipped = {s["缺锚点标准号"]: s["规则"] for s in body["skipped_no_anchor"]}
    assert skipped == {"1312": "大额收入确认"}

    # FR-02-03 串联：逐项确认（可改评级）→ 锁定 → R5 程序包（高风险全程序）
    risk_id = body["risks"][0]["id"]
    resp = client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/confirm",
                       json={"risk_level": "中"}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已确认"
    assert resp.json()["risk_level"] == "中"

    # 采信 + 逐项确认完毕后可再次发起（无待确认 AI 项）
    assert client.post(f"/api/v1/worksheets/ai-runs/{body['run_no']}/review",
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 200
    resp = client.post(f"/api/v1/projects/{project_id}/risks/{risk_id}/programs",
                       json=[{"program_code": "PRG-CASH-01"}, {"program_code": "PRG-BANK-01"}],
                       headers=manager_h)
    assert resp.status_code == 200, resp.text
    programs = resp.json()
    assert {p["package_type"] for p in programs} == {"高风险全程序"}  # 中风险走全程序包（R5）
    assert all(p["min_standard_note"] is None for p in programs)  # 仅简化包才要求底线标注
    assert verify_chain(db)
