"""小企业准则域级模板差异化测试：独立键登记 + 准则路由 + 保真写回。

路由单点在 engine/industry_matrix.domain_template_key（模板矩阵数据即代码）：
小企业准则项目四域出稿（计划/复核/核对表/往来函）走 -sme 独立键；银行询证函
为监管统一版式（两准则模板包文件字节一致）共用 bank 键。企业准则项目沿用
原键由 second_batch 覆盖，此处负向断言路由函数口径与未登记域拒绝。
"""
from __future__ import annotations

import io
import json

import pytest
from openpyxl import load_workbook

from app.engine.fingerprint import compute_fingerprint
from app.engine.industry_matrix import domain_template_key
from app.services.confirmations import letter as cf_letter
from app.services.planning import render as plan_render
from app.services.quality import render as qc_render
from app.services.worksheets import render as ws_render
from app.services.worksheets import review_render

_EVAL_OK = {
    "independence_ok": True,
    "client_risk_ok": True,
    "competence_ok": True,
    "engagement_letter_ok": True,
}

# SME 独立键 → (域模板根, 文件名)
_SME_ENTRIES = {
    "plan-sme/v2025.xlsx": (plan_render.TEMPLATE_DIR, "plan_sme_v2025.xlsx"),
    "review-sme/v2025.xlsx": (review_render.TEMPLATE_DIR, "review_sme_v2025.xlsx"),
    "checklist-sme/v2025.xlsx": (qc_render.TEMPLATE_DIR, "checklist_sme_v2025.xlsx"),
    "ar-sme/v2025.xlsx": (cf_letter.TEMPLATE_DIR, "ar_sme_v2025.xlsx"),
}


def _mkuser(db, login_name: str, role: str) -> None:
    from app.models.user import User
    from app.services.auth import hash_password

    db.add(User(
        name={"manager": "王经理", "partner": "李合伙人", "auditor": "赵审计员",
              "qc": "质控专员"}.get(login_name, login_name),
        login_name=login_name, password_hash=hash_password("secret66"), global_role=role,
    ))
    db.commit()


def _login(client, login_name: str) -> dict:
    token = client.post(
        "/api/v1/auth/login", json={"login_name": login_name, "password": "secret66"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _setup_sme(client, db) -> str:
    """五角色 + 客户 + 已批准小企业准则项目，返回 project_id。"""
    from app.models.project import Client

    client_row = Client(name="示例小企业", uscc="91110000MA01X000SM", industry="商贸", data_level="一般")
    db.add(client_row)
    db.commit()
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    resp = client.post("/api/v1/projects", json={
        "name": "示例小企业 2025 年报审计", "client_id": client_row.id,
        "business_type": "小企业准则", "period": "2025 年度",
        "evaluation": _EVAL_OK, "members": [],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    assert client.post(
        f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner")
    ).status_code == 200
    return project_id


def _merged_value(ws, coord: str):
    """合并区安全取值：坐标落在合并区内时读锚点格（MergedCell.value 恒 None）。"""
    from openpyxl.cell.cell import MergedCell

    cell = ws[coord]
    if isinstance(cell, MergedCell):
        for rng in ws.merged_cells.ranges:
            if coord in rng:
                cell = ws.cell(row=rng.min_row, column=rng.min_col)
                break
    return cell.value


def _mkworksheet_chain(client, db, project_id: str) -> tuple[str, str, str]:
    """SME 项目底稿正向流：材料→AI初稿→采信→提交→受理→复核通过，返回 (worksheet_id, run_no, 底稿编号)。"""
    from app.models.knowledge import RuleAnchor, Template
    from app.models.material import Material

    tpl_row = Template(type="商贸", version="v2025", skeleton_key="templates/sme/4101.xlsx")
    anchor = RuleAnchor(standard_no="1301", clause="第十章", purpose="函证程序底线要求")
    db.add_all([tpl_row, anchor])
    for lv in ("customer", "firm", "rule"):
        db.add(Material(
            project_id=project_id, level=lv, file_key=f"materials/{project_id}/{lv}.xlsx",
            parse_status="已解析", gap_flag=False,
        ))
    db.commit()
    h = _login(client, "auditor")
    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金", "template_id": tpl_row.id,
        "rule_anchor_id": anchor.id, "is_important": True,
    }, headers=h)
    assert resp.status_code == 201, resp.text
    ws_id, ws_code = resp.json()["id"], resp.json()["code"]
    run_no = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=h).json()["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=_login(client, "manager")).status_code == 200
    assert client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=h).status_code == 200
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/take",
                       headers=_login(client, "partner")).status_code == 200
    resp = client.post(f"/api/v1/worksheets/{ws_id}/review/conclude",
                      json={"conclusion": "通过"}, headers=_login(client, "partner"))
    assert resp.status_code == 200, resp.text
    return ws_id, run_no, ws_code


def test_sme_domain_templates_registered():
    """发布指纹回归：四个 SME 域级键登记在 manifest 且指纹与文件现状一致。"""
    for key, (root, rel) in _SME_ENTRIES.items():
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        assert key in manifest, f"{key} 未登记"
        assert manifest[key]["version"] == "sme2025"
        assert manifest[key]["fill_cells"], f"{key} fill_cells 为空"
        wb = load_workbook(root / rel)
        assert compute_fingerprint(wb) == manifest[key]["structure_fingerprint"], key
        wb.close()


def test_domain_template_key_routing():
    """准则路由单点（engine/industry_matrix）：SME 项目四域走独立键、银行函共用；
    企业准则项目走原键；未登记域拒绝（负向）。"""
    for domain, sme_key in (
        ("plan", "plan-sme/v2025.xlsx"),
        ("review", "review-sme/v2025.xlsx"),
        ("checklist", "checklist-sme/v2025.xlsx"),
        ("ar", "ar-sme/v2025.xlsx"),
    ):
        assert domain_template_key(domain, "小企业准则") == sme_key
        assert domain_template_key(domain, "商贸") == {
            "plan": "plan/v2025.xlsx", "review": "review/v2025.xlsx",
            "checklist": "checklist/v2025.xlsx", "ar": "ar/v2025.xlsx",
        }[domain]
    # 银行询证函：监管统一版式，两准则同键
    assert domain_template_key("bank", "小企业准则") == domain_template_key("bank", "商贸")
    with pytest.raises(ValueError, match="未登记"):
        domain_template_key("report", "商贸")


def test_sme_project_plan_and_checklist_export(client, db, isolated_storage):
    """SME 项目计划/核对表出稿：模板键路由到 -sme 独立键 + 表头要素落格 + 指纹零触碰。"""
    project_id = _setup_sme(client, db)

    resp = client.post(f"/api/v1/projects/{project_id}/plan-export", json={},
                       headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["template_key"] == "plan-sme/v2025.xlsx"
    assert body["file_key"] == f"planning/{project_id}/plan.xlsx"

    wb = load_workbook(io.BytesIO((isolated_storage / body["file_key"]).read_bytes()))
    plan = wb["2600审计计划"]
    assert _merged_value(plan, "B2") == "示例小企业"
    assert _merged_value(plan, "B4") == "王经理"
    assert _merged_value(plan, "A2") == "被审计单位："  # 标签未被覆写（2600 为 A 列独立标签布局）
    manifest = json.loads(
        (plan_render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["plan-sme/v2025.xlsx"]["structure_fingerprint"]
    wb.close()

    resp = client.post(f"/api/v1/quality/export-checklist/{project_id}", json={},
                      headers=_login(client, "qc"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["template_key"] == "checklist-sme/v2025.xlsx"
    wb = load_workbook(io.BytesIO((isolated_storage / body["file_key"]).read_bytes()))
    ws = wb["审计工作完成情况核对表6600"]
    assert _merged_value(ws, "C2") == "示例小企业"
    assert _merged_value(ws, "A2") == "被审计单位："  # 标签未被覆写
    manifest = json.loads(
        (qc_render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["checklist-sme/v2025.xlsx"]["structure_fingerprint"]
    wb.close()


def test_sme_project_review_sheet_export(client, db, isolated_storage):
    """SME 项目复核核对表出稿：路由到 review-sme 独立键 + 表头要素落格。"""
    project_id = _setup_sme(client, db)
    ws_id, _, _ = _mkworksheet_chain(client, db, project_id)

    resp = client.post(f"/api/v1/worksheets/{ws_id}/review-export", json={},
                       headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["template_key"] == "review-sme/v2025.xlsx"

    wb = load_workbook(io.BytesIO((isolated_storage / body["file_key"]).read_bytes()))
    ws = wb["业务复核核对表-准则稿"]
    assert _merged_value(ws, "C2") == "示例小企业"
    assert _merged_value(ws, "C4") == "王经理"  # 编制=AI采信落版人
    assert _merged_value(ws, "H4") == "李合伙人"  # 复核=最近复核记录复核人（R1）
    # 标签未被覆写（值区锚点写回，不落标签合并锚点）
    assert _merged_value(ws, "A2") == "被审计单位："
    manifest = json.loads(
        (review_render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["review-sme/v2025.xlsx"]["structure_fingerprint"]
    wb.close()


def test_real_template_ai_draft_adopt_renders_header(client, db, isolated_storage):
    """真实模板底稿 AI 采信写回（FR-03-02 修复回归）：按 header_map 语义位落格。

    真实模板（4101）表头为横向布局（被审计单位 B2/索引号 G2/项目 B3/编制 B4/
    日期 B5），与占位模板纵排坐标约定不同——采信写回若硬编码坐标会被
    fill_cells 白名单闸拒绝（render_fidelity 越界即 422）。此处断言语义位
    正确落格、复核位不编造、指纹零触碰。
    """
    project_id = _setup_sme(client, db)
    ws_id, run_no, _ = _mkworksheet_chain(client, db, project_id)

    file_key = f"worksheets/{ws_id}/{run_no}.xlsx"
    wb = load_workbook(io.BytesIO((isolated_storage / file_key).read_bytes()))
    ws = wb["程序表"]
    # 值区锚点落格（A2:B2 为标签合并，真值位在 C2:D2 锚点 C2）
    assert _merged_value(ws, "C2") == "示例小企业"  # 被审计单位
    assert _merged_value(ws, "C4") == "赵审计员"  # 编制=AI 触发人
    assert _merged_value(ws, "H4") is None  # 复核位不编造（AI 初稿阶段复核人未定）
    # 模板预填保留（不覆写）：索引号 H2 预填 4101、项目 C3 预填科目名
    assert _merged_value(ws, "H2") == 4101
    assert _merged_value(ws, "C3") == "货币资金"
    # 标签未被覆写（修正前写 B2 会落到标签锚点 A2 覆写「被审计单位：」）
    assert _merged_value(ws, "A2") == "被审计单位："
    assert _merged_value(ws, "F4") == "复      核："
    manifest = json.loads(
        (ws_render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["templates/sme/4101.xlsx"]["structure_fingerprint"]
    wb.close()


def test_sme_project_letter_routing(client, db, isolated_storage):
    """SME 项目函证件出稿：往来函默认路由 ar-sme 独立键；银行函监管统一版式共用 bank 键。"""
    project_id = _setup_sme(client, db)
    h = _login(client, "auditor")
    resp = client.post("/api/v1/confirmations", json={
        "project_id": project_id,
        "items": [
            {"type": "银行", "counterparty": "工商银行示例支行", "amount": "1000000.99"},
            {"type": "往来", "counterparty": "乙往来有限公司", "amount": "5000.00"},
        ],
    }, headers=h)
    assert resp.status_code == 201, resp.text
    bank_id, ar_id = [r["id"] for r in resp.json()]

    # 往来函：小企业准则项目默认 → ar-sme 独立键，白名单落格 + 指纹校验 + 金额不出站
    resp = client.post(f"/api/v1/confirmations/{ar_id}/letter", json={}, headers=h)
    assert resp.status_code == 201, resp.text
    ar_body = resp.json()
    assert ar_body["template_key"] == "ar-sme/v2025.xlsx"
    wb = load_workbook(io.BytesIO((isolated_storage / ar_body["file_key"]).read_bytes()))
    ws = wb["往来账项询证函（积极式询证函）"]
    assert ws["B4"].value == "乙往来有限公司"
    assert ws["F4"].value == ar_body["letter_no"]
    manifest = json.loads(
        (cf_letter.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["ar-sme/v2025.xlsx"]["structure_fingerprint"]
    for row in ws.iter_rows(values_only=True):
        for v in row:
            assert "5000.00" not in str(v), "函证金额泄漏进询证函产物"
    wb.close()

    # 银行函：监管统一版式，SME 项目仍共用 bank 键
    resp = client.post(f"/api/v1/confirmations/{bank_id}/letter", json={}, headers=h)
    assert resp.status_code == 201, resp.text
    assert resp.json()["template_key"] == "bank/v2025.xlsx"
