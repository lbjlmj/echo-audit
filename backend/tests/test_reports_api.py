"""FR-05-03/04 报告域集成测试：权限矩阵 / 附注 AI 线索门禁 / 审批流三步 / 分歧阻签 / 报告门。

附注 AI 恒为线索（module=ai_notes、related=report，硬约束 3），采信复用 ai-runs review 分派；
审批流：模板出稿（经理）→ 质控会签 → 合伙人签发（FR-05-04），升级未裁决分歧阻签（FR-05-05）；
签发即报告门（门 7）report_signed 成立，由 /quality/gates/报告/pass 串联。
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
    """五角色 + 客户 + 已批准项目 + 底稿，返回 (project_id, worksheet_id)。"""
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
    project_id = resp.json()["id"]
    assert client.post(
        f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner")
    ).status_code == 200

    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金底稿", "template_id": tpl.id,
        "rule_anchor_id": anchor.id, "is_important": False,
    }, headers=_login(client, "auditor"))
    assert resp.status_code == 201, resp.text
    return project_id, resp.json()["id"]


def _reach_completion_gate(client, db, project_id: str, worksheet_id: str) -> None:
    """底稿复核通过 + 质控检查采信 → 完成门（门 6）通过（FR-05-03 触发前置）。"""
    from app.models.worksheet import Worksheet

    manager_h = _login(client, "manager")
    db.get(Worksheet, worksheet_id).status = "复核通过"
    db.commit()
    resp = client.post("/api/v1/quality/qc-checks", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 201, resp.text
    run_no = resp.json()["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 200
    resp = client.post("/api/v1/quality/gates/完成/pass", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 200, resp.text


def _make_note_draft(client, project_id: str) -> str:
    """经理发起附注并采信，返回 report_id（附注已进入报告稿）。"""
    manager_h = _login(client, "manager")
    resp = client.post("/api/v1/reports/notes-draft", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "线索" and body["run_no"]  # 版本随项目报告序号递增（v1/v2…）
    assert client.post(f"/api/v1/worksheets/ai-runs/{body['run_no']}/review",
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 200
    return body["report_id"]


def test_note_draft_permissions_and_lead(client, db):
    """FR-05-03：完成门未过不可发起；审计员/质控不可发起；产物线索态；未采信不可重复发起。"""
    project_id, worksheet_id = _setup(client, db)
    auditor_h, qc_h, manager_h = _login(client, "auditor"), _login(client, "qc"), _login(client, "manager")

    # 完成门未通过：发起附注 → 422 RP-1001（触发前置）
    resp = client.post("/api/v1/reports/notes-draft", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 422 and "RP-1001" in resp.text

    _reach_completion_gate(client, db, project_id, worksheet_id)

    # 审计员（plan.confirm=NONE）、质控（VIEW < OPERATE）不可发起 → 403
    assert client.post("/api/v1/reports/notes-draft", json={"project_id": project_id},
                       headers=auditor_h).status_code == 403
    assert client.post("/api/v1/reports/notes-draft", json={"project_id": project_id},
                       headers=qc_h).status_code == 403

    # 经理发起 → 报告建档 v1 + 附注线索态；未采信重复发起 → 409 RP-1006
    resp = client.post("/api/v1/reports/notes-draft", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "线索" and body["run_no"]
    report_id = body["report_id"]
    assert client.post("/api/v1/reports/notes-draft", json={"project_id": project_id},
                       headers=manager_h).status_code == 409

    # 附注未采信：出稿 → 422（FR-05-04 触发前置）
    resp = client.post(f"/api/v1/reports/{report_id}/finalize",
                       json={"template_key": "report/general/v2025"}, headers=manager_h)
    assert resp.status_code == 422 and "RP-1001" in resp.text

    # 采信：审计员（PROPOSE < OPERATE）403；经理采信 → 附注进入报告稿
    review_url = f"/api/v1/worksheets/ai-runs/{body['run_no']}/review"
    assert client.post(review_url, json={"conclusion": "采信"}, headers=auditor_h).status_code == 403
    assert client.post(review_url, json={"conclusion": "采信"}, headers=manager_h).status_code == 200

    reports_list = client.get(f"/api/v1/reports?project_id={project_id}", headers=qc_h).json()
    assert len(reports_list) == 1 and reports_list[0]["notes_draft_key"]
    assert verify_chain(db), "附注生成与采信留痕必须续哈希链（硬约束 4）"


def test_report_ai_runs_and_trails_readonly(client, db):
    """报告 AI 线索/留痕时间线只读端点：附注线索可反查；审批流五步留痕正序可追溯。"""
    project_id, worksheet_id = _setup(client, db)
    _reach_completion_gate(client, db, project_id, worksheet_id)
    manager_h, qc_h = _login(client, "manager"), _login(client, "qc")

    # 发起附注（线索态）但暂不采信：ai-runs 可反查线索
    resp = client.post("/api/v1/reports/notes-draft", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 201, resp.text
    report_id, run_no = resp.json()["report_id"], resp.json()["run_no"]
    resp = client.get(f"/api/v1/reports/{report_id}/ai-runs", headers=manager_h)
    assert resp.status_code == 200, resp.text
    runs = resp.json()
    assert len(runs) == 1 and runs[0]["run_no"] == run_no and runs[0]["status"] == "线索"
    assert runs[0]["module"] == "ai_notes"

    # 审批流：采信 → 出稿 → 会签 → 签发，时间线五动作正序
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 200
    assert client.post(f"/api/v1/reports/{report_id}/finalize",
                       json={"template_key": "report/general/v2025"},
                       headers=manager_h).status_code == 200
    assert client.post(f"/api/v1/reports/{report_id}/countersign",
                       json={"opinion": "附注与底稿一致，同意会签"}, headers=qc_h).status_code == 200
    assert client.post(f"/api/v1/reports/{report_id}/sign", headers=_login(client, "partner")
                       ).status_code == 200
    resp = client.get(f"/api/v1/reports/{report_id}/trails", headers=qc_h)
    assert resp.status_code == 200, resp.text
    acts = [t["action"] for t in resp.json()]
    assert acts == [
        "报告建档并发起附注生成（线索态）", "附注复核通过进入报告稿",
        "报告模板出稿", "报告质控会签", "报告签发（合伙人）",
    ], acts

    # 不存在报告 → 404 RP-1002
    assert client.get("/api/v1/reports/no-such-report/trails", headers=manager_h).status_code == 404
    assert verify_chain(db)


def test_approval_flow_dispute_block_and_report_gate(client, db):
    """FR-05-04 审批流：出稿→会签→签发；FR-05-05 分歧阻签；签发后报告门通过（门 7）。"""
    project_id, worksheet_id = _setup(client, db)
    auditor_h, qc_h, partner_h = _login(client, "auditor"), _login(client, "qc"), _login(client, "partner")

    _reach_completion_gate(client, db, project_id, worksheet_id)
    report_id = _make_note_draft(client, project_id)

    # 会签前置：未模板出稿 → 422；出稿权限：审计员 403；模板键必填 422
    assert client.post(f"/api/v1/reports/{report_id}/countersign",
                       json={"opinion": "附注与底稿一致，同意会签"}, headers=qc_h).status_code == 422
    assert client.post(f"/api/v1/reports/{report_id}/finalize",
                       json={"template_key": "report/general/v2025"},
                       headers=auditor_h).status_code == 403
    assert client.post(f"/api/v1/reports/{report_id}/finalize", json={"template_key": " "},
                       headers=_login(client, "manager")).status_code == 422
    resp = client.post(f"/api/v1/reports/{report_id}/finalize",
                       json={"template_key": "report/general/v2025"},
                       headers=_login(client, "manager"))
    assert resp.status_code == 200 and resp.json()["template_key"] == "report/general/v2025"
    assert resp.json()["file_key"].endswith(".xlsx")  # 出稿产物对象键（保真写回落存）

    # 会签：经理（PROPOSE < OPERATE）403；意见必填 422；质控会签 → 200 落会签人
    assert client.post(f"/api/v1/reports/{report_id}/countersign",
                       json={"opinion": "经理越权会签意见"}, headers=_login(client, "manager")).status_code == 403
    assert client.post(f"/api/v1/reports/{report_id}/countersign", json={"opinion": " "},
                       headers=qc_h).status_code == 422
    resp = client.post(f"/api/v1/reports/{report_id}/countersign",
                       json={"opinion": "附注与底稿数据核对一致，同意会签"}, headers=qc_h)
    assert resp.status_code == 200 and resp.json()["status"] == "会签"
    assert resp.json()["countersigned_by"]

    # 分歧阻签（FR-05-05）：升级未裁决 → 签发 422 RP-1001；合伙人裁决后解除
    fid = client.post("/api/v1/findings", json={
        "project_id": project_id, "worksheet_id": worksheet_id,
        "items": [{"description": "收入确认口径与附注披露存在意见分歧"}], "source": "人工",
    }, headers=auditor_h).json()["findings"][0]["id"]
    for action, payload in (("confirm", None), ("start", None),
                            ("resolve", {"resolution": "管理层坚持口径，涉及报告意见类型"}),
                            ("escalate", {"note": "提请合伙人裁决"})):
        resp = client.post(f"/api/v1/findings/{fid}/{action}", json=payload, headers=_login(client, "manager"))
        assert resp.status_code == 200, resp.text
    resp = client.post(f"/api/v1/reports/{report_id}/sign", headers=partner_h)
    assert resp.status_code == 422 and "FR-05-05" in resp.text

    # 签发权限：质控（OPERATE < APPROVE）403；合伙人裁决 → 签发 200
    assert client.post(f"/api/v1/reports/{report_id}/sign", headers=qc_h).status_code == 403
    assert client.post(f"/api/v1/findings/{fid}/adjudicate",
                       json={"adjudication_ref": "裁决-2025-R01"}, headers=partner_h).status_code == 200
    resp = client.post(f"/api/v1/reports/{report_id}/sign", headers=partner_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已签发"
    assert resp.json()["signed_by"]

    # 已签发为终态：重复签发 → 422；重复会签 → 422
    assert client.post(f"/api/v1/reports/{report_id}/sign", headers=partner_h).status_code == 422
    assert client.post(f"/api/v1/reports/{report_id}/countersign",
                       json={"opinion": "重复会签意见"}, headers=qc_h).status_code == 422

    # 报告门（门 7）：质控（report.sign=OPERATE < APPROVE）403；合伙人 pass → 已通过
    assert client.post("/api/v1/quality/gates/报告/pass", json={"project_id": project_id},
                       headers=qc_h).status_code == 403
    resp = client.post("/api/v1/quality/gates/报告/pass", json={"project_id": project_id}, headers=partner_h)
    assert resp.status_code == 200 and resp.json()["gate_status"] == "已通过"
    snapshot = resp.json()["conditions_snapshot"]
    assert snapshot["report_signed"] is True and snapshot["escrow_opinion_resolved"] is True
    assert verify_chain(db)


def test_report_finalize_fidelity_and_fingerprint(client, db, isolated_storage, monkeypatch):
    """硬约束 8 模板保真出稿：白名单填充、公式/下拉零触碰、发布指纹回归、写后回滚、产物下载。"""
    import io
    import json as _json
    import shutil

    from openpyxl import load_workbook

    from app.core import excel_render
    from app.engine.fingerprint import (
        FingerprintError,
        compute_fingerprint,
        verify_structure,
    )
    from app.services.reports import render

    project_id, worksheet_id = _setup(client, db)
    _reach_completion_gate(client, db, project_id, worksheet_id)
    manager_h = _login(client, "manager")
    report_id = _make_note_draft(client, project_id)

    # 模板清单端点：登记键可查（FR-05-04 出稿下拉数据源）
    resp = client.get("/api/v1/reports/templates", headers=manager_h)
    assert resp.status_code == 200
    assert "report/general/v2025" in [t["key"] for t in resp.json()]

    # 未登记键 → 422 RP-1007（防任意键注入），库内不落 file_key
    resp = client.post(f"/api/v1/reports/{report_id}/finalize",
                       json={"template_key": "templates/hack/v1.xlsx"}, headers=manager_h)
    assert resp.status_code == 422 and "RP-1007" in resp.text

    # 出稿 → 200：登记键 + 出稿对象键落库
    resp = client.post(f"/api/v1/reports/{report_id}/finalize",
                       json={"template_key": "report/general/v2025"}, headers=manager_h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template_key"] == "report/general/v2025"
    assert body["file_key"] == f"reports/{project_id}/{report_id}/v1.xlsx"

    # 下载产物（NFS-04 保真回归）：白名单要素已填、公式区与下拉框零触碰、
    # 重开指纹 == manifest 发布登记指纹（TECH_DESIGN §7 模板指纹回归口径）
    resp = client.get(f"/api/v1/reports/{report_id}/file", headers=manager_h)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    wb = load_workbook(io.BytesIO(resp.content))
    assert wb["审计报告"]["B3"].value == "示例公司"
    assert wb["审计报告"]["B6"].value == "v1"
    assert "货币资金底稿" in wb["审计报告"]["B10"].value  # 附注章节（底稿清单）已写入
    assert wb["审计报告"]["D3"].value == "=LEN(B3)"  # 公式区零触碰
    assert len(wb["审计报告"].data_validations.dataValidation) == 1  # 意见类型下拉框零触碰
    manifest = _json.loads(
        (render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert compute_fingerprint(wb) == manifest["report/general/v2025"]["structure_fingerprint"]

    # 已出稿重复出稿 → 409 RP-1006
    assert client.post(f"/api/v1/reports/{report_id}/finalize",
                       json={"template_key": "report/general/v2025"},
                       headers=manager_h).status_code == 409

    # 负向 1：模板文件被篡改（公式区被改写）→ 422 RP-1010 拒绝出稿，库内无痕
    report_id2 = _make_note_draft(client, project_id)  # 前一份已出稿，可再发起（无未复核线索）
    orig_tpl = render.TEMPLATE_DIR
    tpl_dir = isolated_storage.parent / "tpl"
    tpl_dir.mkdir()
    tampered = load_workbook(orig_tpl / "general_v2025.xlsx")
    tampered["审计报告"]["D3"] = "=LEN(B4)"
    tampered.save(tpl_dir / "general_v2025.xlsx")
    shutil.copy(orig_tpl / "manifest.json", tpl_dir / "manifest.json")
    monkeypatch.setattr(render, "TEMPLATE_DIR", tpl_dir)
    resp = client.post(f"/api/v1/reports/{report_id2}/finalize",
                       json={"template_key": "report/general/v2025"}, headers=manager_h)
    assert resp.status_code == 422 and "RP-1010" in resp.text
    reports_list = client.get(f"/api/v1/reports?project_id={project_id}", headers=manager_h).json()
    v2 = next(r for r in reports_list if r["id"] == report_id2)
    assert v2["file_key"] is None and v2["template_key"] is None  # 回滚：字段未落

    # 负向 2：写后指纹校验失败（注入 verify_structure 抛错）→ 422 RP-1010，
    # 且已落盘对象必须被删除（失败回滚，硬约束 8）。模板恢复为发布原件，仅注入写后校验。
    monkeypatch.setattr(render, "TEMPLATE_DIR", orig_tpl)

    def _boom(baseline, current) -> None:
        raise FingerprintError("注入破坏")

    monkeypatch.setattr(excel_render, "verify_structure", _boom)
    resp = client.post(f"/api/v1/reports/{report_id2}/finalize",
                       json={"template_key": "report/general/v2025"}, headers=manager_h)
    assert resp.status_code == 422 and "RP-1010" in resp.text
    assert not (isolated_storage / f"reports/{project_id}/{report_id2}/v2.xlsx").exists()
    v2 = next(r for r in client.get(f"/api/v1/reports?project_id={project_id}",
                                    headers=manager_h).json() if r["id"] == report_id2)
    assert v2["file_key"] is None

    # 未出稿下载 → 422 RP-1001；verify_structure 恢复后正常出稿 → 200（编排自愈）
    assert client.get(f"/api/v1/reports/{report_id2}/file", headers=manager_h).status_code == 422
    monkeypatch.setattr(excel_render, "verify_structure", verify_structure)
    assert client.post(f"/api/v1/reports/{report_id2}/finalize",
                       json={"template_key": "report/general/v2025"},
                       headers=manager_h).status_code == 200
    assert verify_chain(db)
