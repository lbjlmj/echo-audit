"""首批真实模板接入测试（企业会计准则底稿 4101 + 函证域五模板）。

覆盖三道闸（硬约束 8）：manifest 发布指纹与模板文件一致（数据即代码回归）、
函证件出稿白名单写回 + 写后重开指纹校验、金额不出站（§4.2.7）与越权/未登记拒绝。
"""
from __future__ import annotations

import io
import json

from openpyxl import load_workbook

from app.engine.fingerprint import compute_fingerprint
from app.services import confirmations
from app.services.confirmations import letter as cf_letter

_EVAL_OK = {
    "independence_ok": True,
    "client_risk_ok": True,
    "competence_ok": True,
    "engagement_letter_ok": True,
}


def _mkuser(db, login_name: str, role: str) -> None:
    from app.models.user import User
    from app.services.auth import hash_password

    db.add(User(
        name={"manager": "王经理", "partner": "李合伙人", "auditor": "赵审计员"}.get(login_name, login_name),
        login_name=login_name, password_hash=hash_password("secret66"), global_role=role,
    ))
    db.commit()


def _login(client, login_name: str) -> dict:
    token = client.post(
        "/api/v1/auth/login", json={"login_name": login_name, "password": "secret66"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _setup(client, db) -> str:
    """五角色 + 客户 + 已批准项目，返回 project_id。"""
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


def test_cas_batch_fingerprints_and_catalog_mount():
    """第三批全量回归：cas/ 77 张科目底稿 manifest 指纹与文件一致，目录种子挂载齐全。"""
    from pathlib import Path

    from app.services.worksheets.render import TEMPLATE_DIR as WS_TPL

    manifest = json.loads((WS_TPL / "manifest.json").read_text(encoding="utf-8"))
    cas_keys = {k: v for k, v in manifest.items() if k.startswith("templates/cas/")}
    assert len(cas_keys) == 77, f"cas 登记数异常：{len(cas_keys)}"

    files = {f.name for f in (WS_TPL / "cas").glob("*.xlsx")}
    assert files == {k.rsplit("/", 1)[-1] for k in cas_keys}, "cas 文件与登记键不一一对应"

    for key, entry in sorted(cas_keys.items()):
        wb = load_workbook(WS_TPL / entry["file"])
        assert compute_fingerprint(wb) == entry["structure_fingerprint"], key
        assert entry["fill_cells"], f"{key} fill_cells 为空（扫描异常）"
        wb.close()

    catalog = json.loads(
        (Path(WS_TPL).parents[1] / "data" / "worksheet_catalog.json").read_text(encoding="utf-8")
    )
    mounted = 0

    def _mount(items):
        nonlocal mounted
        mounted += sum(1 for it in items if "template_key" in it)

    for g in catalog["groups"]:
        _mount(g.get("items", []))
        for s in g.get("subgroups", []):
            _mount(s.get("items", []))
            for ss in s.get("subs", []):
                _mount(ss.get("items", []))
    assert mounted == 77, f"目录种子挂载数异常：{mounted}"


def test_sme_batch_fingerprints_and_catalog_mount():
    """小企业准则批量回归：sme/ 49 张底稿 manifest 指纹与文件一致，目录种子挂载齐全。"""
    from pathlib import Path

    from app.services.worksheets.render import TEMPLATE_DIR as WS_TPL

    manifest = json.loads((WS_TPL / "manifest.json").read_text(encoding="utf-8"))
    sme_keys = {k: v for k, v in manifest.items() if k.startswith("templates/sme/")}
    assert len(sme_keys) == 49, f"sme 登记数异常：{len(sme_keys)}"

    files = {f.name for f in (WS_TPL / "sme").glob("*.xlsx")}
    assert files == {k.rsplit("/", 1)[-1] for k in sme_keys}, "sme 文件与登记键不一一对应"

    for key, entry in sorted(sme_keys.items()):
        wb = load_workbook(WS_TPL / entry["file"])
        assert compute_fingerprint(wb) == entry["structure_fingerprint"], key
        assert entry["fill_cells"], f"{key} fill_cells 为空（扫描异常）"
        wb.close()

    catalog = json.loads(
        (Path(WS_TPL).parents[1] / "data" / "worksheet_catalog_sme.json").read_text(encoding="utf-8")
    )
    assert catalog["standard"] == "小企业会计准则"
    mounted = 0

    def _mount(items):
        nonlocal mounted
        mounted += sum(1 for it in items if "template_key" in it)

    for g in catalog["groups"]:
        _mount(g.get("items", []))
        for s in g.get("subgroups", []):
            _mount(s.get("items", []))
    assert mounted == 49, f"小企业目录种子挂载数异常：{mounted}"


def test_manifest_fingerprints_match_template_files():
    """发布指纹回归（数据即代码）：六份首批模板的 manifest 指纹与文件现状一致。"""
    from app.services.worksheets import render as ws_render

    roots = {
        ws_render.TEMPLATE_DIR: [
            ("templates/cas/4101.xlsx", "cas/4101.xlsx"),
        ],
        cf_letter.TEMPLATE_DIR: [
            ("bank/v2025.xlsx", "bank_v2025.xlsx"),
            ("ar/v2025.xlsx", "ar_v2025.xlsx"),
            ("control/v2025.xlsx", "control_v2025.xlsx"),
            ("send_list/v2025.xlsx", "send_list_v2025.xlsx"),
            ("reply_check/v2025.xlsx", "reply_check_v2025.xlsx"),
        ],
    }
    checked = 0
    for root, entries in roots.items():
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for key, rel in entries:
            assert key in manifest, f"{key} 未登记"
            wb = load_workbook(root / rel)
            assert compute_fingerprint(wb) == manifest[key]["structure_fingerprint"], key
            wb.close()
            checked += 1
    assert checked == 6


def test_render_letter_bank_and_ar_fidelity(client, db, isolated_storage):
    """函证件出稿（FR-04-01）：银行/往来默认模板保真写回，白名单落格 + 指纹校验 + 金额不出站。"""
    from app.models.confirmation import Confirmation

    project_id = _setup(client, db)
    h = _login(client, "auditor")
    resp = client.post("/api/v1/confirmations", json={
        "project_id": project_id,
        "items": [
            {"type": "银行", "counterparty": "工商银行示例支行", "amount": "1000000.99"},
            {"type": "往来", "counterparty": "甲往来有限公司", "amount": "5000.00"},
        ],
    }, headers=h)
    assert resp.status_code == 201, resp.text
    bank_id, ar_id = [r["id"] for r in resp.json()]

    # 银行函证：默认模板 bank/v2025，致=L 对方单位、编号格=CF-短码
    resp = client.post(f"/api/v1/confirmations/{bank_id}/letter", json={}, headers=h)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["template_key"] == "bank/v2025.xlsx"
    assert body["letter_no"] == f"CF-{bank_id[:8].upper()}"
    assert body["file_key"] == f"confirmations/{project_id}/{bank_id}/letter.xlsx"

    wb = load_workbook(io.BytesIO((isolated_storage / body["file_key"]).read_bytes()))
    ws = wb["银行询证函"]
    assert ws["B4"].value == "工商银行示例支行"
    assert ws["L4"].value == body["letter_no"]
    manifest = json.loads(
        (cf_letter.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["bank/v2025.xlsx"]["structure_fingerprint"]
    # 金额绝不出站（§4.2.7）：产物全文不含台账金额文本
    for row in ws.iter_rows(values_only=True):
        for v in row:
            assert "1000000.99" not in str(v), "函证金额泄漏进询证函产物"
    wb.close()

    # 往来函证：默认模板 ar/v2025（积极式表）
    resp = client.post(f"/api/v1/confirmations/{ar_id}/letter", json={}, headers=h)
    assert resp.status_code == 201, resp.text
    ar_body = resp.json()
    assert ar_body["template_key"] == "ar/v2025.xlsx"
    wb = load_workbook(io.BytesIO((isolated_storage / ar_body["file_key"]).read_bytes()))
    ws = wb["往来账项询证函（积极式询证函）"]
    assert ws["B4"].value == "甲往来有限公司"
    assert ws["F4"].value == ar_body["letter_no"]
    wb.close()

    # 出稿不改八状态（函证件生成后仍按发出流程走）
    assert db.get(Confirmation, bank_id).status == "台账已生成"


def test_render_letter_permission_and_unregistered_key(client, db):
    """负向：质控（VIEW）越权 403；未登记模板键 422 CF-1010（防任意键注入）。"""
    project_id = _setup(client, db)
    resp = client.post("/api/v1/confirmations", json={
        "project_id": project_id,
        "items": [{"type": "银行", "counterparty": "某银行", "amount": "100"}],
    }, headers=_login(client, "auditor"))
    (cf_id,) = [r["id"] for r in resp.json()]

    # 质控 worksheet.edit=查看 < OPERATE → 403
    resp = client.post(f"/api/v1/confirmations/{cf_id}/letter", json={},
                       headers=_login(client, "qc"))
    assert resp.status_code == 403

    # 未登记键 → 422 CF-1010（TemplateNotRegistered），产物不落盘
    resp = client.post(f"/api/v1/confirmations/{cf_id}/letter",
                       json={"template_key": "../../etc/passwd"}, headers=_login(client, "auditor"))
    assert resp.status_code == 422 and "CF-1010" in resp.text

    # 显式合法键（往来模板用于银行函证）→ 200：模板键允许显式覆盖，白名单照常校验
    resp = client.post(f"/api/v1/confirmations/{cf_id}/letter",
                       json={"template_key": "ar/v2025.xlsx"}, headers=_login(client, "auditor"))
    assert resp.status_code == 201 and resp.json()["template_key"] == "ar/v2025.xlsx"
    assert confirmations.LETTER_TEMPLATE_DIR.name == "confirmation"
