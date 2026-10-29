"""第二批真实模板接入测试（审计计划 2600 / 复核底稿 6700 / 完成核对表 6600）。

三域出稿共用三道闸（硬约束 8）：发布指纹回归、白名单表头要素写回 + 写后重开校验、
权限与前置状态拒绝（PL-1010/WS-1020/QU-1020）。
"""
from __future__ import annotations

import io
import json

from openpyxl import load_workbook

from app.engine.fingerprint import compute_fingerprint
from app.services.planning import render as plan_render
from app.services.quality import render as qc_render
from app.services.worksheets import review_render

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
    """五角色 + 客户 + 已批准项目（商贸行业，含已批准重要性水平）。"""
    from datetime import datetime, timezone
    from decimal import Decimal

    from app.models.project import Client
    from app.models.risk import Materiality
    from app.models.user import User

    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(client_row)
    db.commit()
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    manager_id = db.query(User).filter_by(login_name="manager").first().id
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
    # 已批准重要性水平（2610 跨表联动的 R8 数据前置）
    db.add(Materiality(
        project_id=project_id, benchmark_type="营业收入", benchmark_amount=Decimal("100000000"),
        ratio=Decimal("0.0050"), overall_materiality=Decimal("500000"),
        execution_ratio=Decimal("60"), trivial_ratio=Decimal("5"),
        approved_by=manager_id, approved_at=datetime.now(tz=timezone.utc),
    ))
    db.commit()
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


def _mkworksheet_chain(client, db, project_id: str) -> str:
    """三层材料→AI初稿→采信→提交→受理→复核通过，返回 worksheet_id（§4.3 正向流）。"""
    from app.models.knowledge import RuleAnchor, Template
    from app.models.material import Material

    tpl_row = Template(type="商贸", version="v2025", skeleton_key="templates/trade/v2025.xlsx")
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
    ws_id = resp.json()["id"]
    run_no = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=h).json()["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=_login(client, "manager")).status_code == 200
    assert client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=h).status_code == 200
    # 采信落版人=经理（R1 自审拒绝），复核受理/结论由合伙人执行
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=_login(client, "partner")
                       ).status_code == 200
    resp = client.post(f"/api/v1/worksheets/{ws_id}/review/conclude",
                       json={"conclusion": "通过"}, headers=_login(client, "partner"))
    assert resp.status_code == 200, resp.text
    return ws_id


def test_second_batch_fingerprints_match_template_files():
    """发布指纹回归：2600/6700/6600 三份模板 manifest 指纹与文件现状一致。"""
    roots = {
        plan_render.TEMPLATE_DIR: [("plan/v2025.xlsx", "plan_v2025.xlsx")],
        review_render.TEMPLATE_DIR: [("review/v2025.xlsx", "review_v2025.xlsx")],
        qc_render.TEMPLATE_DIR: [("checklist/v2025.xlsx", "checklist_v2025.xlsx")],
    }
    for root, entries in roots.items():
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for key, rel in entries:
            wb = load_workbook(root / rel)
            assert compute_fingerprint(wb) == manifest[key]["structure_fingerprint"], key
            wb.close()


def test_plan_export_fidelity(client, db, isolated_storage):
    """审计计划出稿（FR-02-01）：2600 表头要素落格 + 2610 跨表公式联动 + 指纹零触碰。"""
    project_id = _setup(client, db)
    resp = client.post(f"/api/v1/projects/{project_id}/plan-export", json={},
                       headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["template_key"] == "plan/v2025.xlsx"
    assert body["file_key"] == f"planning/{project_id}/plan.xlsx"

    wb = load_workbook(io.BytesIO((isolated_storage / body["file_key"]).read_bytes()))
    plan = wb["2600审计计划"]
    assert _merged_value(plan, "B2") == "示例公司"
    assert _merged_value(plan, "B4") == "王经理"
    assert _merged_value(plan, "G4") == "2025 年度"
    manifest = json.loads(
        (plan_render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["plan/v2025.xlsx"]["structure_fingerprint"]
    # 2610 重要性表头为跨表公式（公式区零触碰，指纹闸保证）
    assert str(wb["2610重要性"]["B2"].value).startswith("= '2600审计计划'") or str(
        wb["2610重要性"]["B2"].value
    ).startswith("='2600审计计划'")
    wb.close()

    # 权限：审计员（plan.confirm < OPERATE）→ 403；项目不存在 → 404
    assert client.post(f"/api/v1/projects/{project_id}/plan-export", json={},
                       headers=_login(client, "auditor")).status_code == 403
    assert client.post("/api/v1/projects/no-such/plan-export", json={},
                       headers=_login(client, "manager")).status_code == 404


def test_review_export_after_conclusion(client, db, isolated_storage):
    """复核核对表出稿（FR-05-02）：复核通过前置 + 表头要素落格；未复核通过 422。"""
    project_id = _setup(client, db)
    ws_id = _mkworksheet_chain(client, db, project_id)

    resp = client.post(f"/api/v1/worksheets/{ws_id}/review-export", json={},
                       headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["worksheet_status"] == "复核通过"
    assert body["template_key"] == "review/v2025.xlsx"

    wb = load_workbook(io.BytesIO((isolated_storage / body["file_key"]).read_bytes()))
    ws = wb["业务复核核对表-准则稿"]
    assert _merged_value(ws, "C2") == "示例公司"
    assert _merged_value(ws, "C4") == "王经理"  # 编制=AI采信落版人（经理）
    assert _merged_value(ws, "H4") == "李合伙人"  # 复核=最近复核记录复核人
    assert _merged_value(ws, "A2") == "被审计单位："  # 标签未被覆写（值区锚点写回）
    manifest = json.loads(
        (review_render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["review/v2025.xlsx"]["structure_fingerprint"]
    wb.close()

    # 前置拒绝：草稿态底稿 → 422 WS-1007；权限：审计员（review_sign 无 OPERATE）→ 403
    from app.models.knowledge import RuleAnchor, Template

    tpl_row = db.query(Template).first()
    anchor_row = db.query(RuleAnchor).first()
    h = _login(client, "auditor")
    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "其他底稿", "template_id": tpl_row.id, "rule_anchor_id": anchor_row.id,
    }, headers=h)
    draft_id = resp.json()["id"]
    # 草稿态：合伙人（有权限）出稿 → 422 WS-1007 前置拒绝
    resp = client.post(f"/api/v1/worksheets/{draft_id}/review-export", json={},
                       headers=_login(client, "partner"))
    assert resp.status_code == 422 and "WS-1007" in resp.text
    # 权限：审计员（review_sign 无 OPERATE）→ 403
    assert client.post(f"/api/v1/worksheets/{ws_id}/review-export", json={},
                       headers=h).status_code == 403


def test_checklist_export_fidelity(client, db, isolated_storage):
    """完成情况核对表出稿（FR-06-01）：6600 表头要素落格 + 质控权限卡控。"""
    project_id = _setup(client, db)
    resp = client.post(f"/api/v1/quality/export-checklist/{project_id}", json={},
                       headers=_login(client, "qc"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["template_key"] == "checklist/v2025.xlsx"

    wb = load_workbook(io.BytesIO((isolated_storage / body["file_key"]).read_bytes()))
    ws = wb["审计工作完成情况核对表6600"]
    assert _merged_value(ws, "C2") == "示例公司"
    assert _merged_value(ws, "C4") == "qc"  # 编制=质控专员（测试账号直用登录名）
    assert _merged_value(ws, "A2") == "被审计单位："  # 标签未被覆写（值区锚点写回）
    manifest = json.loads(
        (qc_render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["checklist/v2025.xlsx"]["structure_fingerprint"]
    wb.close()

    # 审计员（archive.access=VIEW）→ 403
    assert client.post(f"/api/v1/quality/export-checklist/{project_id}", json={},
                       headers=_login(client, "auditor")).status_code == 403
