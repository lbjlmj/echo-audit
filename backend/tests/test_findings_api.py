"""FR-03-06 问题域集成测试：权限矩阵 / 六状态全流 / 误报排除 / 分歧裁决阻塞 / AI 归集留痕。

六状态只走 engine（硬约束 2）；「已归集」即 AI 线索态，确认/排除为人工复核标记（红线 1/R2）；
涉及报告意见类型的分歧升级后不得关闭，合伙人裁决解除阻塞（FR-05-05）；
排除必附理由、解决必附结果、裁决必附引用（engine 前置 + FD-1xxx 参数校验）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.knowledge import RuleAnchor, Template
from app.models.user import User
from app.models.worksheet import WorksheetVersion
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


def _setup(client, db) -> tuple[str, str]:
    """五角色 + 客户 + 已批准项目 + 模板/锚点底稿，返回 (project_id, worksheet_id)。"""
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

    # 来源底稿（problem.worksheet_id 必填 FK）
    resp = client.post(f"/api/v1/worksheets?project_id={project_id}", json={
        "title": "货币资金底稿", "template_id": tpl.id,
        "rule_anchor_id": anchor.id, "is_important": False,
    }, headers=_login(client, "auditor"))
    assert resp.status_code == 201, resp.text
    return project_id, resp.json()["id"]


def _collect(client, project_id: str, worksheet_id: str, *, source: str = "人工",
             descriptions: tuple[str, ...] = ("账面营业收入与经营范围不符，需进一步核查",)):
    return client.post("/api/v1/findings", json={
        "project_id": project_id, "worksheet_id": worksheet_id,
        "items": [{"description": d} for d in descriptions], "source": source,
    }, headers=_login(client, "auditor"))


def test_permission_matrix_and_collect(client, db):
    """权限矩阵（PRD §3）：审计员可登记（反馈说明）；确认须项目经理；质控可查看。"""
    project_id, worksheet_id = _setup(client, db)
    qc_h, auditor_h, manager_h = _login(client, "qc"), _login(client, "auditor"), _login(client, "manager")

    # 质控（VIEW < PROPOSE）不可登记 → 403
    resp = client.post("/api/v1/findings", json={
        "project_id": project_id, "worksheet_id": worksheet_id,
        "items": [{"description": "质控越权登记的问题描述"}],
    }, headers=qc_h)
    assert resp.status_code == 403

    # 审计员人工登记 1 条 + AI 归集 2 条 → 全部「已归集」；AI 归集返回留痕编号
    resp = _collect(client, project_id, worksheet_id)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ai_run_no"] is None and body["findings"][0]["status"] == "已归集"
    manual_id = body["findings"][0]["id"]

    resp = _collect(client, project_id, worksheet_id, source="AI 归集", descriptions=(
        "存货盘点表与总账存在差异线索", "大额现金收支缺乏审批线索",
    ))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ai_run_no"], "AI 归集须写 AIRun 留痕（硬约束 4）"

    # 审计员（PROPOSE < OPERATE）不可确认 → 403；合伙人（VIEW）也不可 → 403
    assert client.post(f"/api/v1/findings/{manual_id}/confirm", headers=auditor_h).status_code == 403
    assert client.post(f"/api/v1/findings/{manual_id}/confirm", headers=_login(client, "partner")).status_code == 403

    # 经理确认 → 已确认并成为责任人
    resp = client.post(f"/api/v1/findings/{manual_id}/confirm", headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已确认"

    # 来源非法 → 422；空条目 → 422；底稿不存在 → 404 FD-1002
    assert client.post("/api/v1/findings", json={
        "project_id": project_id, "worksheet_id": worksheet_id,
        "items": [{"description": "来源字面量非法的描述"}], "source": "外部",
    }, headers=auditor_h).status_code == 422
    assert client.post("/api/v1/findings", json={
        "project_id": project_id, "worksheet_id": worksheet_id, "items": [],
    }, headers=auditor_h).status_code == 422
    assert client.post("/api/v1/findings", json={
        "project_id": project_id, "worksheet_id": "no-such-ws",
        "items": [{"description": "底稿不存在时归集的描述"}],
    }, headers=auditor_h).status_code == 404

    # 质控（VIEW）可查看清单；清单含 3 条
    resp = client.get(f"/api/v1/findings?project_id={project_id}", headers=qc_h)
    assert resp.status_code == 200 and len(resp.json()) == 3
    assert verify_chain(db), "归集与确认留痕必须续哈希链（硬约束 4）"


def test_full_flow_and_illegal_transitions(client, db):
    """六状态正向全流（§4.5）：归集→确认→处理→解决→关闭；非法流转一律 409 FD-1001。"""
    project_id, worksheet_id = _setup(client, db)
    manager_h = _login(client, "manager")
    resp = _collect(client, project_id, worksheet_id)
    fid = resp.json()["findings"][0]["id"]

    # 已归集直接关闭：engine 无此出边 → 409 FD-1001
    resp = client.post(f"/api/v1/findings/{fid}/close", headers=manager_h)
    assert resp.status_code == 409 and "FD-1001" in resp.text

    assert client.post(f"/api/v1/findings/{fid}/confirm", headers=manager_h).status_code == 200
    # 重复确认：已确认无此出边 → 409
    assert client.post(f"/api/v1/findings/{fid}/confirm", headers=manager_h).status_code == 409
    assert client.post(f"/api/v1/findings/{fid}/start", headers=manager_h).status_code == 200

    # 处理结果必填：空白 → 422 FD-1007
    resp = client.post(f"/api/v1/findings/{fid}/resolve", json={"resolution": "  "}, headers=manager_h)
    assert resp.status_code == 422 and "FD-1007" in resp.text
    resp = client.post(f"/api/v1/findings/{fid}/resolve",
                       json={"resolution": "已获取管理层说明并执行截止测试，差异已消除"}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已解决"

    resp = client.post(f"/api/v1/findings/{fid}/close", headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已关闭"
    # 已关闭为终态：再流转 → 409
    assert client.post(f"/api/v1/findings/{fid}/start", headers=manager_h).status_code == 409
    assert verify_chain(db), "全流留痕须可整链验证（硬约束 4）"


def test_reject_flow_with_reason(client, db):
    """误报排除（§4.5）：必附理由留痕；已排除为终态。"""
    project_id, worksheet_id = _setup(client, db)
    manager_h = _login(client, "manager")
    fid = _collect(client, project_id, worksheet_id).json()["findings"][0]["id"]

    # 理由缺失：Schema 拒空串 → 422；服务层 strip 拒空白 → 422 FD-1007
    assert client.post(f"/api/v1/findings/{fid}/reject", json={"reason": ""}, headers=manager_h).status_code == 422
    resp = client.post(f"/api/v1/findings/{fid}/reject", json={"reason": "  "}, headers=manager_h)
    assert resp.status_code == 422 and "FD-1007" in resp.text

    resp = client.post(f"/api/v1/findings/{fid}/reject",
                       json={"reason": "复核确认为口径差异，非审计问题（误报）"}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已排除"
    # 已排除为终态：确认/关闭 → 409
    assert client.post(f"/api/v1/findings/{fid}/confirm", headers=manager_h).status_code == 409
    assert client.post(f"/api/v1/findings/{fid}/close", headers=manager_h).status_code == 409
    assert verify_chain(db)


def test_escalation_blocks_close_until_adjudication(client, db):
    """分歧裁决（FR-05-05）：升级后不得关闭，合伙人裁决落引用解除阻塞；裁决为项目级审批。"""
    project_id, worksheet_id = _setup(client, db)
    auditor_h, manager_h, partner_h = _login(client, "auditor"), _login(client, "manager"), _login(client, "partner")

    # 第一条：升级 → 裁决 → 关闭
    fid = _collect(client, project_id, worksheet_id).json()["findings"][0]["id"]
    assert client.post(f"/api/v1/findings/{fid}/confirm", headers=manager_h).status_code == 200
    assert client.post(f"/api/v1/findings/{fid}/start", headers=manager_h).status_code == 200
    assert client.post(f"/api/v1/findings/{fid}/resolve",
                       json={"resolution": "拟调整入账，但管理层坚持原处理，存在意见分歧"}, headers=manager_h).status_code == 200

    # 审计员（PROPOSE）可升级登记（任一环节反馈）；说明必填 → 422
    resp = client.post(f"/api/v1/findings/{fid}/escalate", json={"note": " "}, headers=auditor_h)
    assert resp.status_code == 422
    resp = client.post(f"/api/v1/findings/{fid}/escalate",
                       json={"note": "涉及报告意见类型，提请合伙人裁决"}, headers=auditor_h)
    assert resp.status_code == 200 and resp.json()["escalated"] is True

    # 升级未裁决：关闭被 engine 前置拦截 → 409
    resp = client.post(f"/api/v1/findings/{fid}/close", headers=manager_h)
    assert resp.status_code == 409 and "escalation_cleared" in resp.text

    # 裁决是项目级审批（合伙人专属）：经理 403；未升级问题裁决 422；引用必填 422
    assert client.post(f"/api/v1/findings/{fid}/adjudicate",
                       json={"adjudication_ref": "裁决-2025-001"}, headers=manager_h).status_code == 403
    assert client.post(f"/api/v1/findings/{fid}/adjudicate",
                       json={"adjudication_ref": "  "}, headers=partner_h).status_code == 422

    # 未升级的第二条问题：裁决 → 422
    fid2 = _collect(client, project_id, worksheet_id).json()["findings"][0]["id"]
    assert client.post(f"/api/v1/findings/{fid2}/adjudicate",
                       json={"adjudication_ref": "裁决-2025-002"}, headers=partner_h).status_code == 422

    resp = client.post(f"/api/v1/findings/{fid}/adjudicate",
                       json={"adjudication_ref": "裁决-2025-001"}, headers=partner_h)
    assert resp.status_code == 200 and resp.json()["escalation_ref"] == "裁决-2025-001"

    resp = client.post(f"/api/v1/findings/{fid}/close", headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已关闭"
    assert verify_chain(db), "分歧登记与裁决留痕必须续哈希链（硬约束 4）"


def test_ai_finding_review_dispatch(client, db):
    """AI 归集线索复核（红线 1/R2）：ai_finding 分派只标记留痕，不落底稿版本链。"""
    project_id, worksheet_id = _setup(client, db)
    manager_h = _login(client, "manager")

    resp = _collect(client, project_id, worksheet_id, source="AI 归集")
    run_no = resp.json()["ai_run_no"]
    assert db.query(WorksheetVersion).count() == 0

    # 不采信缺原因 → 422；采信 → 留痕标记完成，且不产生底稿版本
    resp = client.post("/api/v1/worksheets/ai-runs/%s/review" % run_no,
                       json={"conclusion": "不采信"}, headers=manager_h)
    assert resp.status_code == 422
    resp = client.post("/api/v1/worksheets/ai-runs/%s/review" % run_no,
                       json={"conclusion": "采信"}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已采信"
    assert db.query(WorksheetVersion).count() == 0, "ai_finding 采信不得误落底稿版本链"
    # 重复标记一次性 → 409
    assert client.post("/api/v1/worksheets/ai-runs/%s/review" % run_no,
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 409
    assert verify_chain(db)


def test_paused_project_and_not_found(client, db):
    """§15-17 暂停冻结 + 404 口径（夹具模拟引擎流转结果）。"""
    from app.models.project import AuditProject

    project_id, worksheet_id = _setup(client, db)
    project = db.get(AuditProject, project_id)
    project.status = "暂停"
    db.commit()

    resp = _collect(client, project_id, worksheet_id)
    assert resp.status_code == 409 and "FD-1005" in resp.text
    # 经理（有权限）操作不存在的问题 → 404 FD-1002（权限校验先于存在性，审计员此处为 403）
    assert client.post("/api/v1/findings/no-such-id/confirm",
                       headers=_login(client, "manager")).status_code == 404
