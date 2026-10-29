"""FR-03-01 资料域集成测试：三层材料缺口 / §15-6 最低字段校验 / 解析状态 / 凭证行导入。

缺口标志（必需材料未到位 → gap_flag）由本域维护，底稿出稿拦截（不出半成品）
在 worksheets（WS-1003）单点；映射创建时按类型跑 §15-6 最低字段校验，人工映射一次、同客户复用。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.knowledge import RuleAnchor, Template
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


def _setup(client, db) -> tuple[str, str]:
    """五角色 + 客户 + 已批准项目，返回 (project_id, client_id)。"""
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
        "evaluation": {
            "independence_ok": True, "client_risk_ok": True,
            "competence_ok": True, "engagement_letter_ok": True,
        },
        "members": [],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], client_row.id


def _register(client, project_id: str, level: str, file_key: str, required: bool = False) -> dict:
    resp = client.post("/api/v1/materials", json={
        "project_id": project_id, "level": level, "file_key": file_key, "required": required,
    }, headers=_login(client, "auditor"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_material_register_gap_and_permissions(client, db):
    """三层材料登记与缺口（FR-03-01）：质控不可登记；必需未到位挂缺口；补齐解除；缺层列示。"""
    project_id, _ = _setup(client, db)
    auditor_h, qc_h = _login(client, "auditor"), _login(client, "qc")

    # 质控（worksheet.edit=VIEW < OPERATE）不可登记 → 403
    assert client.post("/api/v1/materials", json={
        "project_id": project_id, "level": "customer", "file_key": "materials/qc.xlsx",
    }, headers=qc_h).status_code == 403

    # 非法层级 → 422；空对象键 → 422
    assert client.post("/api/v1/materials", json={
        "project_id": project_id, "level": "外部", "file_key": "materials/x.xlsx",
    }, headers=auditor_h).status_code == 422
    assert client.post("/api/v1/materials", json={
        "project_id": project_id, "level": "customer", "file_key": " ",
    }, headers=auditor_h).status_code == 422

    # 必需材料未到位 → 缺口登记；另两层登记即到位
    customer = _register(client, project_id, "customer", "materials/明细账.xlsx", required=True)
    assert customer["gap_flag"] is True
    _register(client, project_id, "firm", "templates/firm/函证模板.xlsx")
    _register(client, project_id, "rule", "templates/rule/填表说明.pdf")

    resp = client.get(f"/api/v1/materials/gaps?project_id={project_id}", headers=qc_h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["complete"] is False
    assert [g["file_key"] for g in body["gaps"]] == ["materials/明细账.xlsx"]
    assert body["missing_levels"] == ["customer"]  # customer 层仅缺口材料，视为整层缺失

    # 补齐：缺口解除 → complete=True；重复补齐 → 422 MA-1006
    resp = client.post(f"/api/v1/materials/{customer['id']}/arrive", headers=auditor_h)
    assert resp.status_code == 200 and resp.json()["gap_flag"] is False
    body = client.get(f"/api/v1/materials/gaps?project_id={project_id}", headers=qc_h).json()
    assert body["complete"] is True and body["gaps"] == []
    assert client.post(f"/api/v1/materials/{customer['id']}/arrive", headers=auditor_h).status_code == 422
    assert verify_chain(db), "材料登记/补齐留痕必须续哈希链（硬约束 4）"


def test_mapping_minimum_fields_and_reuse(client, db):
    """§15-6 字段映射：映射目标端缺最低字段拒绝；按类型区分；同客户复用查询。"""
    project_id, client_id = _setup(client, db)
    auditor_h = _login(client, "auditor")

    voucher_full = {
        "记账日期": "日期", "凭单号": "凭证号", "业务说明": "摘要",
        "科目代码": "科目编码", "科目": "科目名称", "借方金额": "借方", "贷方金额": "贷方",
    }
    # 明细账映射缺「科目名称」→ 422 MA-1007（§15-6）
    missing_name = {k: v for k, v in voucher_full.items() if v != "科目名称"}
    resp = client.post("/api/v1/materials/import-mappings", json={
        "client_id": client_id, "source_system": "客户老版财务系统",
        "mapping_type": "明细账", "field_mapping": missing_name,
    }, headers=auditor_h)
    assert resp.status_code == 422 and "科目名称" in resp.text

    # 齐全 → 201；同客户复用查询可见
    resp = client.post("/api/v1/materials/import-mappings", json={
        "client_id": client_id, "source_system": "客户老版财务系统",
        "mapping_type": "明细账", "field_mapping": voucher_full, "reuse_scope": "全所",
    }, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    rows = client.get(f"/api/v1/materials/import-mappings?client_id={client_id}", headers=auditor_h).json()
    assert len(rows) == 1 and rows[0]["reuse_scope"] == "全所"

    # 科目余额表映射缺「级次」→ 422；补齐后 201
    tb = {"科目代码": "科目编码", "科目": "科目名称", "期初": "期初余额",
          "借方": "借方发生额", "贷方": "贷方发生额", "期末": "期末余额"}
    resp = client.post("/api/v1/materials/import-mappings", json={
        "client_id": client_id, "source_system": "客户老版财务系统",
        "mapping_type": "科目余额表", "field_mapping": tb,
    }, headers=auditor_h)
    assert resp.status_code == 422 and "级次" in resp.text
    tb_full = {**tb, "科目级次": "级次"}
    assert client.post("/api/v1/materials/import-mappings", json={
        "client_id": client_id, "source_system": "客户老版财务系统",
        "mapping_type": "科目余额表", "field_mapping": tb_full,
    }, headers=auditor_h).status_code == 201
    assert len(client.get(f"/api/v1/materials/import-mappings?client_id={client_id}",
                          headers=auditor_h).json()) == 2
    assert verify_chain(db)


def test_parse_status_and_voucher_import(client, db):
    """解析状态前置与凭证行导入（FR-03-04 数据池）：缺口不可解析；未解析不可导入；行级校验。"""
    project_id, _ = _setup(client, db)
    auditor_h = _login(client, "auditor")

    required = _register(client, project_id, "customer", "materials/明细账.xlsx", required=True)
    # 缺口登记不可解析 → 422
    assert client.post(f"/api/v1/materials/{required['id']}/parse", json={"ok": True},
                       headers=auditor_h).status_code == 422

    client.post(f"/api/v1/materials/{required['id']}/arrive", headers=auditor_h)
    # 解析失败 → 失败态；再解析成功 → 已解析 + 向量引用落库
    resp = client.post(f"/api/v1/materials/{required['id']}/parse", json={"ok": False}, headers=auditor_h)
    assert resp.status_code == 200 and resp.json()["parse_status"] == "失败"
    resp = client.post(f"/api/v1/materials/{required['id']}/parse",
                       json={"ok": True, "vector_ref": "pgvec/materials/abc"}, headers=auditor_h)
    assert resp.status_code == 200 and resp.json()["parse_status"] == "已解析"
    assert resp.json()["vector_ref"] == "pgvec/materials/abc"

    # 未解析材料不可导入（另建一条仅到位未解析）
    raw = _register(client, project_id, "customer", "materials/序时账.xlsx")
    lines = [
        {"voucher_no": "记-0001", "entry_date": "2025-01-05", "summary": "收到货款",
         "account_code": "1002", "account_name": "银行存款", "debit": "50000.00", "credit": None},
        {"voucher_no": "记-0001", "summary": "收到货款",
         "account_code": "6001", "account_name": "主营业务收入", "debit": None, "credit": "50000.00"},
    ]
    resp = client.post(f"/api/v1/materials/{raw['id']}/voucher-lines", json=lines, headers=auditor_h)
    assert resp.status_code == 422 and "MA-1007" in resp.text

    # 已解析材料导入 → 201 落 VoucherLine；行级最低字段缺失 → 422 MA-1007
    resp = client.post(f"/api/v1/materials/{required['id']}/voucher-lines", json=lines, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert len(body) == 2 and body[0]["voucher_no"] == "记-0001"

    bad_lines = [{"voucher_no": "记-0002", "account_code": " ", "summary": "科目编码空白行"}]
    resp = client.post(f"/api/v1/materials/{required['id']}/voucher-lines", json=bad_lines, headers=auditor_h)
    assert resp.status_code == 422 and "MA-1007" in resp.text

    # 材料清单含 2 条；留痕链完整
    assert len(client.get(f"/api/v1/materials?project_id={project_id}", headers=auditor_h).json()) == 2
    assert verify_chain(db)
