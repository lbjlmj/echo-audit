"""FR-03 底稿域集成测试：三层材料拦截 / AI 线索门禁 / 六状态全流 / 模板结构指纹。

六状态只走 engine（硬约束 2）；AI 线索无复核标记不可进版本链不可提交（红线 1/R2，硬约束 3）；
R1 编制人=复核人拒绝；指纹校验单点在 engine/fingerprint（硬约束 8）。
"""
from __future__ import annotations

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.datavalidation import DataValidation

from app.core.audit_middleware import verify_chain
from app.engine.fingerprint import FingerprintError, compute_fingerprint, verify_structure
from app.models.material import Material
from app.models.user import User
from app.models.worksheet import WorksheetReview, WorksheetVersion
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


def _setup(client, db) -> tuple[str, str, str]:
    """五角色 + 客户 + 已批准项目 + 模板 + 准则锚点，返回 (project_id, template_id, anchor_id)。"""
    from app.models.knowledge import RuleAnchor, Template
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
        "evaluation": _EVAL_OK, "members": [],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 201, resp.text
    project_id = resp.json()["id"]
    assert client.post(
        f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner")
    ).status_code == 200
    return project_id, tpl.id, anchor.id


def _add_materials(db, project_id: str, levels: tuple[str, ...]) -> None:
    """登记三层级材料（FR-03-01）：MinIO 未接线的 Phase 1 口径，仅落登记行。"""
    for lv in levels:
        db.add(Material(
            project_id=project_id, level=lv, file_key=f"materials/{project_id}/{lv}.xlsx",
            parse_status="已解析", gap_flag=False,
        ))
    db.commit()


def _mkworksheet_with_project(client, db, project_id: str) -> str:
    """在既有项目上创建底稿（复用 _setup 建好的模板与锚点）。"""
    from app.models.knowledge import RuleAnchor, Template

    tpl = db.query(Template).first()
    anchor = db.query(RuleAnchor).first()
    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金底稿", "template_id": tpl.id,
        "rule_anchor_id": anchor.id, "is_important": False,
    }, headers=_login(client, "auditor"))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_permission_matrix_on_worksheet_flow(client, db):
    """权限矩阵（PRD §3）：质控不可编制；审计员不可复核/采信；经理不可终审。"""
    project_id, template_id, anchor_id = _setup(client, db)
    qc_h, auditor_h, manager_h = _login(client, "qc"), _login(client, "auditor"), _login(client, "manager")

    # 质控 worksheet.edit=查看（VIEW < OPERATE）→ 403
    assert client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "质控越权底稿", "template_id": template_id, "rule_anchor_id": anchor_id,
    }, headers=qc_h).status_code == 403

    # 审计员可编制（OPERATE）
    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金底稿", "template_id": template_id,
        "rule_anchor_id": anchor_id, "is_important": False,
    }, headers=auditor_h)
    assert resp.status_code == 201
    ws_id = resp.json()["id"]

    # 审计员复核签发=无 → 受理 403；AI 采信=提交待复核（PROPOSE < OPERATE）→ 标记 403
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=auditor_h).status_code == 403
    assert client.post("/api/v1/worksheets/ai-runs/FAKE/review", json={
        "conclusion": "采信",
    }, headers=auditor_h).status_code == 403
    # 经理终审=复核（OPERATE < APPROVE）→ 403
    assert client.post(f"/api/v1/worksheets/{ws_id}/final-approve", headers=manager_h).status_code == 403
    assert db.query(WorksheetVersion).count() == 0  # 被拒请求无副作用


def test_three_layer_gap_blocks_ai_draft(client, db):
    """FR-03-01：三层材料任一层缺失即拦截并列缺口清单，不出半成品；齐备后解锁并落线索留痕。"""
    project_id, _, _ = _setup(client, db)
    ws_id = _mkworksheet_with_project(client, db, project_id)
    auditor_h = _login(client, "auditor")

    # 全缺 → 422 且列出三层缺口
    resp = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h)
    assert resp.status_code == 422 and "WS-1003" in resp.text
    for label in ("客户层", "事务所层", "填表说明层"):
        assert label in resp.text

    # 只补客户层 → 仍拦截并列剩余两层
    _add_materials(db, project_id, ("customer",))
    resp = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h)
    assert resp.status_code == 422 and "客户层" not in resp.text
    assert "事务所层" in resp.text and "填表说明层" in resp.text

    # 三层齐备 → 解锁；产物为线索态 AI 留痕（三段式前两段）
    _add_materials(db, project_id, ("firm", "rule"))
    resp = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_no"].startswith("AI-") and body["status"] == "线索"
    assert body["output_digest"] and body["output_key"]
    assert verify_chain(db), "AI 调用留痕必须续哈希链（硬约束 4）"


def test_lead_cannot_enter_business_unless_reviewed(client, db):
    """红线 1/R2：线索态禁止提交；不采信缺原因 422；采信后才落版本链并可提交。"""
    project_id, _, _ = _setup(client, db)
    _add_materials(db, project_id, ("customer", "firm", "rule"))
    ws_id = _mkworksheet_with_project(client, db, project_id)
    auditor_h, manager_h = _login(client, "auditor"), _login(client, "manager")

    run_no = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h).json()["run_no"]

    # 线索未标记 → engine 拒绝草稿→待复核（409）
    assert client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=auditor_h).status_code == 409

    # 不采信缺原因 → 422（§15-16）；附原因 → 不采信落库，无版本产生
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review", json={
        "conclusion": "不采信",
    }, headers=manager_h).status_code == 422
    resp = client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review", json={
        "conclusion": "不采信", "rejection_reason": "幻觉",
    }, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "不采信"
    assert db.query(WorksheetVersion).count() == 0

    # 不采信后仍无内容可提交（版本链为空 → WS-1006）
    resp = client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=auditor_h)
    assert resp.status_code == 409 and "WS-1006" in resp.text

    # 复核标记一次性（append-only）：已标记 run 再标记 → 409
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review", json={
        "conclusion": "采信",
    }, headers=manager_h).status_code == 409

    # 重新生成 → 项目经理采信 → 版本 v1 落链
    run_no2 = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h).json()["run_no"]
    resp = client.post(f"/api/v1/worksheets/ai-runs/{run_no2}/review", json={
        "conclusion": "采信",
    }, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已采信"
    versions = db.query(WorksheetVersion).filter(WorksheetVersion.worksheet_id == ws_id).all()
    assert len(versions) == 1 and versions[0].version_no == "v1"

    # 采信后提交放行（两条 run 均已标记，R2 满足）
    assert client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=auditor_h).status_code == 200
    assert verify_chain(db)


def test_rag_run_review_marks_only_no_business_write(client, db):
    """FR-07-01 RAG 问答留痕：第三段标记即闭环——不进业务表、不落版本链
    （负向：防止未知 module 掉入底稿写回兜底分支误写）。"""
    from app.models.audit import AIRun

    project_id, _, _ = _setup(client, db)
    manager_h = _login(client, "manager")
    run = AIRun(
        run_no="AI-20261028-RAG00000001", project_id=project_id, module="rag",
        skill_version="rag@1.0.0", model_version="rules@1.0.0",
        triggered_by=db.query(User).filter_by(login_name="manager").first().id,
        input_material_ids=[], input_hashes={}, status="线索",
        related_type="project", related_id=project_id,
    )
    db.add(run)
    db.commit()

    resp = client.post("/api/v1/worksheets/ai-runs/AI-20261028-RAG00000001/review",
                       json={"conclusion": "采信"}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已采信"
    assert db.query(WorksheetVersion).count() == 0  # 标记即闭环：无业务表写入
    assert verify_chain(db)


def test_six_state_flow_with_r1_and_final_approve(client, db):
    """六状态全流（engine 裁决）：提交→R1 拒绝→退回必附意见→通过→终审仅限重要底稿。"""
    project_id, _, _ = _setup(client, db)
    _add_materials(db, project_id, ("customer", "firm", "rule"))
    ws_id = _mkworksheet_with_project(client, db, project_id)
    auditor_h, manager_h, partner_h = _login(client, "auditor"), _login(client, "manager"), _login(client, "partner")

    # 铺至待复核：AI 初稿 → 采信（编制人=王经理）→ 提交
    run_no = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h).json()["run_no"]
    client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review", json={"conclusion": "采信"}, headers=manager_h)
    client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=auditor_h)

    # R1：编制人（采信落版人）=复核人拒绝 → 403
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=manager_h).status_code == 403
    # 合伙人受理 → 复核中
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=partner_h).json()["status"] == "复核中"

    # 退回缺意见 → engine 拒绝（409，FR-05-02）；附意见 → 退回草稿并留复核痕迹
    assert client.post(f"/api/v1/worksheets/{ws_id}/review/conclude", json={
        "conclusion": "退回",
    }, headers=partner_h).status_code == 409
    resp = client.post(f"/api/v1/worksheets/{ws_id}/review/conclude", json={
        "conclusion": "退回", "opinion": "截止测试样本不足", "revision_requirement": "补充 12 月凭证",
    }, headers=partner_h)
    assert resp.status_code == 200 and resp.json()["status"] == "草稿"
    first = db.query(WorksheetReview).filter(WorksheetReview.worksheet_id == ws_id).one()
    assert first.conclusion == "退回" and first.opinion == "截止测试样本不足" and first.round_no == 1

    # 修订后重新提交→受理→通过（带电子签名）
    assert client.post(f"/api/v1/worksheets/{ws_id}/submit", headers=auditor_h).status_code == 200
    client.post(f"/api/v1/worksheets/{ws_id}/review/take", headers=partner_h)
    resp = client.post(f"/api/v1/worksheets/{ws_id}/review/conclude", json={
        "conclusion": "通过",
    }, headers=partner_h)
    assert resp.status_code == 200 and resp.json()["status"] == "复核通过"
    passed = db.query(WorksheetReview).filter(
        WorksheetReview.worksheet_id == ws_id, WorksheetReview.conclusion == "通过"
    ).one()
    assert passed.signature["复核人"] == "李合伙人" and passed.signature["证书号"].startswith("CERT-")

    # 终审仅限重要底稿（§15-11）：非重要 → 409；置重要后 → 终审通过
    assert client.post(f"/api/v1/worksheets/{ws_id}/final-approve", headers=partner_h).status_code == 409
    from app.models.worksheet import Worksheet

    ws = db.get(Worksheet, ws_id)
    ws.is_important = True  # §15-11 清单判定在服务外，测试夹具模拟判定结果
    db.commit()
    resp = client.post(f"/api/v1/worksheets/{ws_id}/final-approve", headers=partner_h)
    assert resp.status_code == 200 and resp.json()["status"] == "终审通过"
    assert verify_chain(db), "全链留痕须可整链验证（硬约束 4）"


# ---------------------------------------------------------------------------
# 模板结构指纹（硬约束 8，engine/fingerprint 单点）
# ---------------------------------------------------------------------------
def _demo_workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "货币资金"
    ws["A1"] = "=SUM(B1:B3)"
    ws["B1"] = 100
    dv = DataValidation(type="list", formula1='"是,否"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("C1")
    return wb


def test_template_fingerprint_guard():
    """指纹回归：公式区/验证规则未变通过；任一篡改即失败（失败回滚由调用方执行）。"""
    wb = _demo_workbook()
    baseline = compute_fingerprint(wb)

    verify_structure(baseline, compute_fingerprint(_demo_workbook()))  # 同构 → 通过

    tampered = _demo_workbook()
    tampered["货币资金"]["A1"] = "=SUM(B1:B4)"  # 公式被改写
    with pytest.raises(FingerprintError, match="指纹不一致"):
        verify_structure(baseline, compute_fingerprint(tampered))

    altered = _demo_workbook()
    extra = DataValidation(type="list", formula1='"甲,乙"')  # 下拉框被发挥
    altered["货币资金"].add_data_validation(extra)
    altered["货币资金"].data_validations.dataValidation[-1].add("D1")
    with pytest.raises(FingerprintError):
        verify_structure(baseline, compute_fingerprint(altered))


# ---------------------------------------------------------------------------
# AI 初稿采信 → 底稿模板保真写回（硬约束 8，FR-03-02 收口）
# ---------------------------------------------------------------------------
def test_ai_draft_approval_renders_fidelity_xlsx(client, db, isolated_storage, monkeypatch):
    """采信落链 = 模板保真写回：白名单要素填充、公式/下拉零触碰、发布指纹回归、
    篡改模板拒绝写回且版本链不落（回滚）、产物可下载。"""
    import io
    import json as _json
    import shutil

    from openpyxl import load_workbook

    from app.core import excel_render
    from app.engine.fingerprint import compute_fingerprint
    from app.services.worksheets import render as ws_render

    project_id, _, _ = _setup(client, db)
    _add_materials(db, project_id, ("customer", "firm", "rule"))
    ws_id = _mkworksheet_with_project(client, db, project_id)
    auditor_h, manager_h = _login(client, "auditor"), _login(client, "manager")

    # 铺已批准的重要性水平（R8）：1 亿营收 × 0.5% = 50 万，执行 60% → 30 万（底稿引用行）
    from datetime import datetime, timezone
    from decimal import Decimal

    from app.models.risk import Materiality

    db.add(Materiality(
        project_id=project_id, benchmark_type="营业收入", benchmark_amount=Decimal("100000000"),
        ratio=Decimal("0.0050"), overall_materiality=Decimal("500000"),
        execution_ratio=Decimal("60"), trivial_ratio=Decimal("5"),
        approved_by=db.query(User).filter_by(login_name="partner").first().id,
        approved_at=datetime.now(tz=timezone.utc),
    ))
    db.commit()

    # 采信 → 版本 v1 落链，file_key 为模板保真写回的 xlsx 对象键
    run_no = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h).json()["run_no"]
    resp = client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=manager_h)
    assert resp.status_code == 200, resp.text
    from app.models.worksheet import Worksheet

    ws = db.get(Worksheet, ws_id)
    assert ws.file_key == f"worksheets/{ws_id}/{run_no}.xlsx"
    assert ws.current_version == "v1"

    # 下载产物（NFS-04 保真回归）：编制人/底稿编号/锚点已填，公式与下拉零触碰，
    # 重开指纹 == manifest 发布登记指纹
    resp = client.get(f"/api/v1/worksheets/{ws_id}/file", headers=manager_h)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    wb = load_workbook(io.BytesIO(resp.content))
    assert wb["底稿"]["B3"].value == "赵审计员"  # 编制人 = AI 触发人（非采信人）
    assert wb["底稿"]["B5"].value == ws.code
    assert "1301" in wb["底稿"]["B6"].value  # 准则锚点强制带出（红线 2）
    assert "AI 未能生成" in wb["底稿"]["B8"].value  # 置空机制：不编造（红线 1）
    assert wb["底稿"]["B9"].value == "¥300,000.00（整体 ¥500,000.00 × 60%）"  # R8 引用（已批准）
    assert wb["底稿"]["D3"].value == "=LEN(B3)"  # 公式区零触碰
    assert len(wb["底稿"].data_validations.dataValidation) == 1  # 下拉框零触碰
    manifest = _json.loads((ws_render.TEMPLATE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert compute_fingerprint(wb) == manifest["templates/trade/v2025.xlsx"]["structure_fingerprint"]

    # 负向：模板文件被篡改（公式区被改写）→ 采信 422 WS-1010，版本链不落、对象无残留
    orig_tpl = ws_render.TEMPLATE_DIR
    tpl_dir = isolated_storage.parent / "ws-tpl"
    tpl_dir.mkdir()
    tampered = load_workbook(orig_tpl / "trade" / "v2025.xlsx")
    tampered["底稿"]["D3"] = "=LEN(B4)"
    (tpl_dir / "trade").mkdir()
    tampered.save(tpl_dir / "trade" / "v2025.xlsx")
    shutil.copy(orig_tpl / "manifest.json", tpl_dir / "manifest.json")
    monkeypatch.setattr(ws_render, "TEMPLATE_DIR", tpl_dir)
    run_no2 = client.post(f"/api/v1/worksheets/{ws_id}/ai-draft", headers=auditor_h).json()["run_no"]
    resp = client.post(f"/api/v1/worksheets/ai-runs/{run_no2}/review",
                       json={"conclusion": "采信"}, headers=manager_h)
    assert resp.status_code == 422 and "WS-1010" in resp.text
    versions = db.query(WorksheetVersion).filter(WorksheetVersion.worksheet_id == ws_id).all()
    assert len(versions) == 1  # 回滚：版本链未追加
    assert db.get(Worksheet, ws_id).current_version == "v1"
    assert not (isolated_storage / f"worksheets/{ws_id}/{run_no2}.xlsx").exists()

    # 模板恢复为发布原件后重新采信 → 200（编排自愈），产物覆盖校验通过
    monkeypatch.setattr(ws_render, "TEMPLATE_DIR", orig_tpl)
    resp = client.post(f"/api/v1/worksheets/ai-runs/{run_no2}/review",
                       json={"conclusion": "采信"}, headers=manager_h)
    assert resp.status_code == 200, resp.text
    assert db.get(Worksheet, ws_id).current_version == "v2"
    assert verify_chain(db)
