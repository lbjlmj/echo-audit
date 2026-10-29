"""FR-06 质控与归档域集成测试：权限矩阵 / 质控 AI 线索门禁 / 归档阻断负向 / 归档收口 / 门卡控。

质控 AI 检查产物恒为线索（module=ai_qc，硬约束 3），采信复用 /worksheets/ai-runs/{run_no}/review；
归档前缺人工复核标记一律阻断（红线 1/R2），哈希链断链阻断（硬约束 4）；
项目+底稿双状态机只走 engine（硬约束 2）；跳过门须合伙人+质控双批（R3）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.audit import AIRun
from app.models.finding import AuditReport
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
    """五角色 + 客户 + 已批准项目 + 底稿（草稿态），返回 (project_id, worksheet_id)。"""
    from app.models.project import Client

    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(client_row)
    tpl = Template(type="商贸", version="v2025", skeleton_key="templates/trade/v2025.xlsx")
    anchor = RuleAnchor(standard_no="1301", clause="第十章", purpose="函证程序底线要求")
    db.add_all([tpl, anchor])
    db.commit()
    partner = _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    qc = _mkuser(db, "qc", "质控专员")
    assert partner and qc
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


def _qc_run(client, project_id: str) -> dict:
    resp = client.post("/api/v1/quality/qc-checks", json={"project_id": project_id},
                       headers=_login(client, "qc"))
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_qc_check_permissions_and_lead(client, db):
    """权限矩阵（PRD §3）：审计员不可发起质控 AI 检查与过门；检查产物为线索态（硬约束 3）。"""
    project_id, worksheet_id = _setup(client, db)
    qc_h, auditor_h, manager_h = _login(client, "qc"), _login(client, "auditor"), _login(client, "manager")

    # 审计员（archive.access=VIEW < OPERATE）不可发起质控 AI 检查 → 403
    assert client.post("/api/v1/quality/qc-checks", json={"project_id": project_id},
                       headers=auditor_h).status_code == 403

    # 断链底稿（R7）：交叉索引引用不存在的底稿编号
    from app.models.worksheet import Worksheet

    ws = db.get(Worksheet, worksheet_id)
    ws.cross_refs = [{"code": "GS-999"}]
    db.commit()

    body = _qc_run(client, project_id)
    assert body["status"] == "线索" and body["passed"] is False
    assert body["checks"]["worksheets_reviewed"] is False
    assert body["checks"]["cross_refs_ok"] is False
    failed_items = {f["item"] for f in body["failed_items"]}
    assert {"底稿完整性", "交叉索引校验"} <= failed_items, body["failed_items"]

    # 质控检查留痕清单可见；GET 清单 1 条且为线索态
    resp = client.get(f"/api/v1/quality/qc-checks?project_id={project_id}", headers=qc_h)
    assert resp.status_code == 200 and len(resp.json()) == 1
    assert resp.json()[0]["status"] == "线索"

    # 完成门条件不足（底稿未复核）：经理过门 → 422 QU-1001
    resp = client.post("/api/v1/quality/gates/完成/pass", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 422 and "QU-1001" in resp.text

    # 审计员无 plan.confirm 权限，过实质性门 → 403；非法阶段 → 422
    assert client.post("/api/v1/quality/gates/实质性/pass", json={"project_id": project_id},
                       headers=auditor_h).status_code == 403
    assert client.post("/api/v1/quality/gates/不存在的门/pass", json={"project_id": project_id},
                       headers=manager_h).status_code == 422

    # 八门清单全程可见
    resp = client.get(f"/api/v1/quality/gates?project_id={project_id}", headers=qc_h)
    assert resp.status_code == 200 and len(resp.json()) == 8
    assert verify_chain(db), "质控检查留痕必须续哈希链（硬约束 4）"


def test_qc_check_review_flow(client, db):
    """第三段复核标记（红线 1/R2）：审计员/质控不可采信 ai_qc；经理采信；一次性标记。"""
    project_id, _ = _setup(client, db)
    run_no = _qc_run(client, project_id)["run_no"]
    auditor_h, qc_h, manager_h = _login(client, "auditor"), _login(client, "qc"), _login(client, "manager")

    # 不采信缺原因分类 → 422（§15-16）
    resp = client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "不采信"}, headers=manager_h)
    assert resp.status_code == 422

    # 审计员（PROPOSE < OPERATE）、质控（VIEW < OPERATE）不可采信 → 403
    review_url = f"/api/v1/worksheets/ai-runs/{run_no}/review"
    assert client.post(review_url, json={"conclusion": "采信"}, headers=auditor_h).status_code == 403
    assert client.post(review_url, json={"conclusion": "采信"}, headers=qc_h).status_code == 403

    # 经理采信 → 已采信；重复标记 → 409（append-only 一次性）
    resp = client.post(review_url, json={"conclusion": "采信"}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已采信"
    assert client.post(review_url, json={"conclusion": "采信"}, headers=manager_h).status_code == 409
    assert verify_chain(db)


def test_archive_precheck_blocks(client, db):
    """归档阻断负向（FR-06-02）：缺复核标记 AI 留痕 / 底稿未复核 / 报告未签发，逐项列阻断清单。"""
    project_id, _ = _setup(client, db)
    qc_h = _login(client, "qc")

    # 项目内两条线索：质控检查 run + 人工插入的问题归集 run（均未过第三段）
    run_no = _qc_run(client, project_id)["run_no"]
    db.add(AIRun(
        run_no="AI-20261028-TEST00000001", project_id=project_id, module="ai_finding",
        skill_version="finding@1.0.0", model_version="rules@1.0.0",
        triggered_by=db.query(User).filter_by(login_name="qc").first().id,
        input_material_ids=[], input_hashes={}, status="线索",
        related_type="project", related_id=project_id,
    ))
    db.commit()

    resp = client.post("/api/v1/quality/archive/precheck", json={"project_id": project_id}, headers=qc_h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocked"] is True
    codes = {b["code"] for b in body["blockers"]}
    assert codes == {"ai_lead_unreviewed", "worksheet_not_reviewed", "report_not_signed"}, codes
    lead_blocker = next(b for b in body["blockers"] if b["code"] == "ai_lead_unreviewed")
    assert set(lead_blocker["run_nos"]) == {run_no, "AI-20261028-TEST00000001"}

    # 校验未通过：归档 → 409 QU-1008
    resp = client.post("/api/v1/quality/archive", json={"project_id": project_id}, headers=qc_h)
    assert resp.status_code == 409 and "QU-1008" in resp.text
    assert verify_chain(db)


def test_archive_success_freeze_and_rearchive(client, db):
    """归档收口（FR-06-04）：暂停冻结 → 校验通过封包哈希 → 项目+底稿双状态机「已归档」→ 重复归档 409。"""
    project_id, worksheet_id = _setup(client, db)
    qc_h, manager_h = _login(client, "qc"), _login(client, "manager")

    # 造齐归档前置：底稿终审通过（重要底稿）+ 报告已签发 + 质控检查已采信
    from app.models.project import AuditProject
    from app.models.worksheet import Worksheet

    partner = db.query(User).filter_by(login_name="partner").first()
    db.add(AuditReport(project_id=project_id, version="v1", status="已签发", signed_by=partner.id))
    ws = db.get(Worksheet, worksheet_id)
    ws.status = "终审通过"
    ws.is_important = True
    db.commit()
    run_no = _qc_run(client, project_id)["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 200

    # 暂停冻结（§15-17）：直接改 ORM 模拟引擎流转结果 → 归档 409 QU-1005
    project = db.get(AuditProject, project_id)
    project.status = "暂停"
    db.commit()
    resp = client.post("/api/v1/quality/archive", json={"project_id": project_id}, headers=qc_h)
    assert resp.status_code == 409 and "QU-1005" in resp.text
    project.status = "进行中"
    db.commit()

    # 校验通过 → 一键归档：封包哈希 + 双状态机收口 + 归档门自动通过
    resp = client.post("/api/v1/quality/archive", json={"project_id": project_id}, headers=qc_h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "已归档" and body["stage"] == "归档"
    assert len(body["archive_package_hash"]) == 64
    assert body["archived_worksheets"] == 1
    db.expire_all()
    assert db.get(AuditProject, project_id).archive_package_hash == body["archive_package_hash"]
    assert db.get(Worksheet, worksheet_id).status == "已归档"

    gates = {g["stage"]: g for g in client.get(
        f"/api/v1/quality/gates?project_id={project_id}", headers=qc_h).json()}
    assert gates["归档"]["gate_status"] == "已通过"

    # 已归档为项目终态：重复归档 → 409 QU-1006（项目已终结，禁止写操作）
    resp = client.post("/api/v1/quality/archive", json={"project_id": project_id}, headers=qc_h)
    assert resp.status_code == 409 and "QU-1006" in resp.text
    assert verify_chain(db)


def test_stage_gates_5_6_and_skip(client, db):
    """门 5/门 6 卡控串联 + R3 双批跳过：函证未闭环阻断门 5；问题/底稿/质控检查收齐过门 6。"""
    project_id, worksheet_id = _setup(client, db)
    auditor_h, manager_h, partner_h, qc_h = (
        _login(client, "auditor"), _login(client, "manager"),
        _login(client, "partner"), _login(client, "qc"),
    )

    # 门 5：存在未闭环函证 → 422 confirmations_closed；ORM 模拟闭环 → 通过
    resp = client.post("/api/v1/confirmations", json={
        "project_id": project_id,
        "items": [{"type": "银行", "counterparty": "示例银行", "amount": "100000.00"}],
    }, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    resp = client.post("/api/v1/quality/gates/实质性/pass", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 422 and "confirmations_closed" in resp.text

    from app.models.confirmation import Confirmation

    cf = db.query(Confirmation).filter_by(project_id=project_id).first()
    cf.status = "闭环"
    db.commit()
    resp = client.post("/api/v1/quality/gates/实质性/pass", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["gate_status"] == "已通过"

    # 门 6：问题全闭环 + 底稿复核通过 + 质控检查已采信 → 通过（三条件逐项收齐）
    fid = client.post("/api/v1/findings", json={
        "project_id": project_id, "worksheet_id": worksheet_id,
        "items": [{"description": "大额现金收支缺乏审批线索"}], "source": "人工",
    }, headers=auditor_h).json()["findings"][0]["id"]
    for action, payload in (("confirm", None), ("start", None),
                            ("resolve", {"resolution": "已补充审批单并执行重新执行测试，缺陷消除"})):
        resp = client.post(f"/api/v1/findings/{fid}/{action}", json=payload, headers=manager_h)
        assert resp.status_code == 200, resp.text
    assert client.post(f"/api/v1/findings/{fid}/close", headers=manager_h).status_code == 200

    from app.models.worksheet import Worksheet

    db.get(Worksheet, worksheet_id).status = "复核通过"
    db.commit()
    # 缺质控检查采信 → 422 qc_check_passed
    resp = client.post("/api/v1/quality/gates/完成/pass", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 422 and "qc_check_passed" in resp.text

    run_no = _qc_run(client, project_id)["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 200
    resp = client.post("/api/v1/quality/gates/完成/pass", json={"project_id": project_id}, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["gate_status"] == "已通过"

    # R3 双批跳过：经理 → 403；质控批注人角色不符 → 422；合伙人+质控 → 200 落双批
    auditor_id = db.query(User).filter_by(login_name="auditor").first().id
    assert client.post("/api/v1/quality/gates/计划/skip",
                       json={"project_id": project_id, "qc_reviewer_id": auditor_id},
                       headers=manager_h).status_code == 403
    assert client.post("/api/v1/quality/gates/计划/skip",
                       json={"project_id": project_id, "qc_reviewer_id": auditor_id},
                       headers=partner_h).status_code == 422
    qc_id = db.query(User).filter_by(login_name="qc").first().id
    resp = client.post("/api/v1/quality/gates/计划/skip",
                       json={"project_id": project_id, "qc_reviewer_id": qc_id}, headers=partner_h)
    assert resp.status_code == 200 and resp.json()["gate_status"] == "跳过"
    assert resp.json()["skip_qc_id"] == qc_id
    assert verify_chain(db)


def test_inspection_pack_anchor_trace_and_digest(client, db):
    """FR-06-05 检查应对：审计员发起 403；锚点反查表（红线 2 可反查准则依据）；
    质控报表留痕核对；同批数据重生成 digest 恒定（出证可复算）；生成动作留痕续链。"""
    project_id, _ws_id = _setup(client, db)
    qc_h, auditor_h = _login(client, "qc"), _login(client, "auditor")

    # 造一条 AI 留痕（质控检查线索态，未复核标记）→ 材料包报表应如实核对
    _qc_run(client, project_id)

    # 审计员 archive.access=查看（VIEW < OPERATE）→ 403（PRD §3：质控按需发起）
    assert client.post("/api/v1/quality/inspection-pack", json={"project_id": project_id},
                       headers=auditor_h).status_code == 403

    resp = client.post("/api/v1/quality/inspection-pack", json={"project_id": project_id},
                       headers=qc_h)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # 锚点反查表：每张底稿可反查「这条程序依据哪条准则」（红线 2 库端 100% 可反查）
    assert len(body["anchor_map"]) == 1
    row = body["anchor_map"][0]
    assert row["standard_no"] == "1301" and row["clause"] == "第十章"
    assert row["purpose"] == "函证程序底线要求"

    # 质控报表：AI 留痕核对（线索态未标记如实列出）+ 复核痕迹 + 八门状态
    ai = body["report"]["ai_runs"]
    assert ai["total"] == 1 and ai["reviewed"] == 0 and len(ai["unreviewed"]) == 1
    assert ai["rejected_by_reason"] == {}
    assert body["report"]["review_rounds"] == 0
    assert "实质性" in body["report"]["gates"] and "归档" in body["report"]["gates"]
    assert len(body["package_digest"]) == 64

    # 同批可复算：数据未变重生成 → digest 恒定（出证完整性，封包无易变项）
    body2 = client.post("/api/v1/quality/inspection-pack", json={"project_id": project_id},
                        headers=qc_h).json()
    assert body2["package_digest"] == body["package_digest"]
    assert verify_chain(db), "材料包生成留痕须可整链验证（硬约束 4）"


def test_archive_package_zip_download_and_tamper(client, db):
    """FR-06-04 归档实包（TECH_DESIGN §4.3.4）：zip 收录真实产物 + SHA256SUMS 防篡改三道核验。

    正向：归档 → 实包落 storage（键含封包哈希，WORM 语义）→ 下载重开：manifest 自证
    == 库内登记哈希、底稿/报告 xlsx 成员字节与源对象一致；防篡改校验通过。
    负向：调包成员内容（SHA256SUMS 不更新）→ 422 QU-1009；未归档下载 → 409 QU-1006。
    """
    import io as _io
    import json
    import zipfile

    from openpyxl import Workbook

    import app.services.quality.service as quality_service

    project_id, worksheet_id = _setup(client, db)
    qc_h, auditor_h = _login(client, "qc"), _login(client, "auditor")
    storage = quality_service.get_storage()

    # 未归档下载 → 409 QU-1006（无归档包）
    resp = client.get(f"/api/v1/quality/archive/{project_id}/package", headers=qc_h)
    assert resp.status_code == 409 and "QU-1006" in resp.text

    # 铺齐归档前置 + 真实产物：底稿终审（xlsx 产物）+ 报告已签发（xlsx 产物）+ 质控采信
    from app.models.project import AuditProject
    from app.models.worksheet import Worksheet

    partner = db.query(User).filter_by(login_name="partner").first()

    def _xlsx(tag: str) -> bytes:
        wb = Workbook()
        wb.active["A1"] = tag
        buf = _io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    ws_bytes, rp_bytes = _xlsx("货币资金底稿产物"), _xlsx("审计报告产物")
    ws_key = f"worksheets/{worksheet_id}/AI-20261028-PKGTEST.xlsx"
    storage.save_bytes(ws_key, ws_bytes)
    report = AuditReport(
        project_id=project_id, version="v1", status="已签发", signed_by=partner.id,
        file_key=f"reports/{project_id}/RP-PKGTEST/v1.xlsx",
    )
    db.add(report)
    db.flush()
    rp_key = report.file_key
    storage.save_bytes(rp_key, rp_bytes)
    ws = db.get(Worksheet, worksheet_id)
    ws.status, ws.is_important, ws.file_key = "终审通过", True, ws_key
    db.commit()
    run_no = _qc_run(client, project_id)["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"},
                       headers=_login(client, "manager")).status_code == 200

    # 一键归档：实包落 storage，键含封包哈希
    resp = client.post("/api/v1/quality/archive", json={"project_id": project_id}, headers=qc_h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    package_hash = body["archive_package_hash"]
    assert body["archive_object_key"] == f"archives/{project_id}/{package_hash}.zip"

    # 质控驾驶舱项目清单（archive.access ≥ VIEW）：审计员可读且含已归档项目
    resp = client.get("/api/v1/quality/projects", headers=auditor_h)
    assert resp.status_code == 200, resp.text
    assert any(p["id"] == project_id and p["status"] == "已归档" for p in resp.json())

    # 下载实包 → 重开核验：manifest 自证 / 真实产物字节一致 / SHA256SUMS 覆盖全成员
    resp = client.get(f"/api/v1/quality/archive/{project_id}/package", headers=qc_h)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(_io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names and "SHA256SUMS" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["package_hash"] == package_hash
        ws_member = next(n for n in names if n.startswith("worksheets/"))
        rp_member = next(n for n in names if n.startswith("reports/"))
        assert zf.read(ws_member) == ws_bytes
        assert zf.read(rp_member) == rp_bytes
        sums = {}
        for line in zf.read("SHA256SUMS").decode("utf-8").splitlines():
            digest, _, name = line.partition("  ")
            sums[name] = digest
        assert names - {"SHA256SUMS"} == set(sums)
        import hashlib

        assert all(
            hashlib.sha256(zf.read(n)).hexdigest() == d for n, d in sums.items()
        )

    # 防篡改校验通过（审计员 archive.access=VIEW 亦可调阅校验）
    resp = client.post(f"/api/v1/quality/archive/{project_id}/verify", headers=auditor_h)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert resp.json()["package_hash"] == package_hash

    # 负向：调包成员内容（重写 zip：篡改底稿成员，SHA256SUMS 原样）→ 422 QU-1009
    resp = client.get(f"/api/v1/quality/archive/{project_id}/package", headers=qc_h)
    with zipfile.ZipFile(_io.BytesIO(resp.content)) as zf:
        items = {n: zf.read(n) for n in zf.namelist()}
    ws_name = next(n for n in items if n.startswith("worksheets/"))
    items[ws_name] = _xlsx("被篡改内容")  # 成员被调包，SHA256SUMS/manifest 未同步
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(items):
            zf.writestr(name, items[name])
    storage.save_bytes(f"archives/{project_id}/{package_hash}.zip", buf.getvalue())
    resp = client.post(f"/api/v1/quality/archive/{project_id}/verify", headers=auditor_h)
    assert resp.status_code == 422 and "QU-1009" in resp.text
    assert "成员 sha256 与清单不符" in resp.text

    assert verify_chain(db), "归档与打包留痕须可整链验证（硬约束 4）"


def test_correction_rearchive_flow(client, db):
    """§15-16 归档后差错 v(n+1) 全链：登记→合伙人批准→副本修订（版本链）→质控复核→重新归档。

    正向：v(n+1) 包 manifest 含 supersedes + 差错单引用，修订产物字节入包，项目哈希指向
    新包，旧包 WORM 不动，双哈希随差错单留痕可查。负向：角色门禁 / 未归档登记 / 在途单
    重复登记 / 未批准先修订 / 未修订先提交 / 退回无意见 / 重复重新归档，逐项拦截。
    """
    import io as _io
    import json
    import zipfile

    from openpyxl import Workbook

    import app.services.quality.service as quality_service
    from app.models.project import AuditProject
    from app.models.worksheet import Worksheet

    project_id, worksheet_id = _setup(client, db)
    qc_h, manager_h = _login(client, "qc"), _login(client, "manager")
    partner_h, auditor_h = _login(client, "partner"), _login(client, "auditor")
    storage = quality_service.get_storage()

    def _register(headers) -> object:
        return client.post("/api/v1/quality/corrections", json={
            "project_id": project_id, "description": "银行未达账项漏调",
            "affected_worksheet_ids": [worksheet_id],
        }, headers=headers)

    # 未归档项目登记差错 → 409 QU-1006
    resp = _register(qc_h)
    assert resp.status_code == 409 and "QU-1006" in resp.text

    # 铺齐归档前置并归档（复用既有口径：复核通过 + 报告已签发 + 质控检查采信）
    partner = db.query(User).filter_by(login_name="partner").first()
    db.add(AuditReport(project_id=project_id, version="v1", status="已签发", signed_by=partner.id))
    ws = db.get(Worksheet, worksheet_id)
    ws.status = "复核通过"
    db.commit()
    run_no = _qc_run(client, project_id)["run_no"]
    assert client.post(f"/api/v1/worksheets/ai-runs/{run_no}/review",
                       json={"conclusion": "采信"}, headers=manager_h).status_code == 200
    resp = client.post("/api/v1/quality/archive", json={"project_id": project_id}, headers=qc_h)
    assert resp.status_code == 200, resp.text
    old_hash = resp.json()["archive_package_hash"]

    # 登记权限负向：经理/审计员 → 403（§15-16 差错登记质控专员专属）
    for h in (manager_h, auditor_h):
        assert _register(h).status_code == 403
    # 受影响底稿不存在 → 404 QU-1002
    resp = client.post("/api/v1/quality/corrections", json={
        "project_id": project_id, "description": "x", "affected_worksheet_ids": ["no-such"],
    }, headers=qc_h)
    assert resp.status_code == 404 and "QU-1002" in resp.text

    # 质控登记 → 201 待批准
    resp = _register(qc_h)
    assert resp.status_code == 201, resp.text
    corr = resp.json()
    assert corr["status"] == "待批准" and corr["code"].endswith("-COR001")
    cid = corr["id"]

    # 在途单重复登记 → 409 QU-1010（防交织修订）
    resp = _register(qc_h)
    assert resp.status_code == 409 and "QU-1010" in resp.text

    # 未批准先修订 → 409 QU-1010
    resp = client.post(f"/api/v1/quality/corrections/{cid}/revisions", json={
        "worksheet_id": worksheet_id, "file_key": "corrections/rev1.xlsx", "change_summary": "s",
    }, headers=manager_h)
    assert resp.status_code == 409 and "QU-1010" in resp.text

    # 未批准先上传修订产物 → 409 QU-1010（经理权限通过，状态门拦截）
    resp = client.post(f"/api/v1/quality/corrections/{cid}/artifacts?worksheet_id={worksheet_id}"
                       f"&filename=rev.xlsx", content=b"x", headers=manager_h)
    assert resp.status_code == 409 and "QU-1010" in resp.text

    # 批准负向：经理 → 403；合伙人驳回无原因 → 422 QU-1007
    assert client.post(f"/api/v1/quality/corrections/{cid}/approve",
                       json={"approve": True}, headers=manager_h).status_code == 403
    resp = client.post(f"/api/v1/quality/corrections/{cid}/approve",
                       json={"approve": False}, headers=partner_h)
    assert resp.status_code == 422 and "QU-1007" in resp.text
    # 合伙人批准 → 修订中（解锁副本修订，记版本基线）
    resp = client.post(f"/api/v1/quality/corrections/{cid}/approve",
                       json={"approve": True}, headers=partner_h)
    assert resp.status_code == 200 and resp.json()["status"] == "修订中"

    # 修订负向：底稿不在受影响清单 → 422；产物对象缺失 → 422
    resp = client.post(f"/api/v1/quality/corrections/{cid}/revisions", json={
        "worksheet_id": "no-such", "file_key": "k", "change_summary": "s",
    }, headers=manager_h)
    assert resp.status_code == 422 and "QU-1007" in resp.text
    resp = client.post(f"/api/v1/quality/corrections/{cid}/revisions", json={
        "worksheet_id": worksheet_id, "file_key": "corrections/missing.xlsx", "change_summary": "s",
    }, headers=manager_h)
    assert resp.status_code == 422 and "QU-1007" in resp.text

    # 上传负向（修订中）：底稿不在清单/非 xlsx/空内容 → 422 QU-1007；质控（VIEW）→ 403
    art_url = f"/api/v1/quality/corrections/{cid}/artifacts"
    resp = client.post(f"{art_url}?worksheet_id=no-such&filename=rev.xlsx",
                       content=b"x", headers=manager_h)
    assert resp.status_code == 422 and "QU-1007" in resp.text
    resp = client.post(f"{art_url}?worksheet_id={worksheet_id}&filename=rev.json",
                       content=b"x", headers=manager_h)
    assert resp.status_code == 422 and "QU-1007" in resp.text
    resp = client.post(f"{art_url}?worksheet_id={worksheet_id}&filename=rev.xlsx",
                       content=b"", headers=manager_h)
    assert resp.status_code == 422 and "QU-1007" in resp.text
    assert client.post(f"{art_url}?worksheet_id={worksheet_id}&filename=rev.xlsx",
                       content=b"x", headers=qc_h).status_code == 403

    # 未登记修订先提交复核 → 422 QU-1007
    resp = client.post(f"/api/v1/quality/corrections/{cid}/submit-review", headers=qc_h)
    assert resp.status_code == 422 and "QU-1007" in resp.text

    # 登记修订 v1（受控上传→内容寻址键→凭键登记版本链）→ 提交复核 → 质控退回（无意见 422 → 附意见回修订中）
    def _xlsx(tag: str) -> bytes:
        wb = Workbook()
        wb.active["A1"] = tag
        buf = _io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    rev1 = _xlsx("差错修订第一版")
    resp = client.post(f"{art_url}?worksheet_id={worksheet_id}&filename=rev1.xlsx",
                       content=rev1, headers=manager_h)
    assert resp.status_code == 201, resp.text
    art1 = resp.json()
    import hashlib as _hashlib

    assert art1["sha256"] == _hashlib.sha256(rev1).hexdigest()
    assert storage.load_bytes(art1["file_key"]) == rev1  # 落存字节与上传一致
    # 内容寻址键：同内容重传同键（幂等），对象未覆盖复制
    assert client.post(f"{art_url}?worksheet_id={worksheet_id}&filename=again.xlsx",
                       content=rev1, headers=manager_h).json()["file_key"] == art1["file_key"]
    resp = client.post(f"/api/v1/quality/corrections/{cid}/revisions", json={
        "worksheet_id": worksheet_id, "file_key": art1["file_key"],
        "change_summary": "补记银行未达账项调节",
    }, headers=manager_h)
    assert resp.status_code == 201 and resp.json()["version_no"] == "v1"
    resp = client.post(f"/api/v1/quality/corrections/{cid}/submit-review", headers=qc_h)
    assert resp.status_code == 200 and resp.json()["status"] == "待质控复核"
    assert client.post(f"/api/v1/quality/corrections/{cid}/qc-review",
                       json={"conclusion": "通过"}, headers=manager_h).status_code == 403
    resp = client.post(f"/api/v1/quality/corrections/{cid}/qc-review",
                       json={"conclusion": "退回"}, headers=qc_h)
    assert resp.status_code == 422 and "QU-1007" in resp.text
    resp = client.post(f"/api/v1/quality/corrections/{cid}/qc-review",
                       json={"conclusion": "退回", "opinion": "调节表缺复核签字"}, headers=qc_h)
    assert resp.status_code == 200 and resp.json()["status"] == "修订中"

    # 第二版修订（退回后再上传异内容 → 异键不覆盖）→ v2 → 提交 → 通过 → 复核通过
    rev2 = _xlsx("差错修订第二版（补签字）")
    resp = client.post(f"{art_url}?worksheet_id={worksheet_id}&filename=rev2.xlsx",
                       content=rev2, headers=manager_h)
    assert resp.status_code == 201
    art2 = resp.json()
    assert art2["file_key"] != art1["file_key"] and storage.load_bytes(art1["file_key"]) == rev1
    resp = client.post(f"/api/v1/quality/corrections/{cid}/revisions", json={
        "worksheet_id": worksheet_id, "file_key": art2["file_key"],
        "change_summary": "补复核签字",
    }, headers=manager_h)
    assert resp.status_code == 201 and resp.json()["version_no"] == "v2"
    assert client.post(f"/api/v1/quality/corrections/{cid}/submit-review",
                       headers=qc_h).json()["status"] == "待质控复核"
    resp = client.post(f"/api/v1/quality/corrections/{cid}/qc-review",
                       json={"conclusion": "通过"}, headers=qc_h)
    assert resp.status_code == 200 and resp.json()["status"] == "复核通过"

    # 重新归档：审计员 → 403；质控 → 200 双哈希链
    assert client.post(f"/api/v1/quality/corrections/{cid}/rearchive",
                       headers=auditor_h).status_code == 403
    resp = client.post(f"/api/v1/quality/corrections/{cid}/rearchive", headers=qc_h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    new_hash = body["archive_package_hash"]
    assert new_hash != old_hash and body["previous_package_hash"] == old_hash
    assert body["status"] == "已重新归档"

    # 项目哈希指向新包；旧包 WORM 不动（v(n)/v(n+1) 双版本均可查）
    db.expire_all()
    assert db.get(AuditProject, project_id).archive_package_hash == new_hash
    assert storage.exists(f"archives/{project_id}/{old_hash}.zip")

    # 新包 manifest：包库自证 + supersedes + 差错单引用 + 修订产物字节（v2）
    resp = client.get(f"/api/v1/quality/archive/{project_id}/package", headers=qc_h)
    with zipfile.ZipFile(_io.BytesIO(resp.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["package_hash"] == new_hash
        assert manifest["supersedes"] == old_hash
        assert manifest["correction"]["code"] == corr["code"]
        ws_member = next(n for n in zf.namelist() if n.startswith("worksheets/"))
        assert ws_member.endswith("/v2.xlsx")
        assert zf.read(ws_member) == rev2

    # 新包防篡改校验通过；重复重新归档 → 409 QU-1010（幂等拒绝）
    resp = client.post(f"/api/v1/quality/archive/{project_id}/verify", headers=qc_h)
    assert resp.status_code == 200 and resp.json()["ok"] is True
    resp = client.post(f"/api/v1/quality/corrections/{cid}/rearchive", headers=qc_h)
    assert resp.status_code == 409 and "QU-1010" in resp.text

    # 差错单清单（archive.access ≥ VIEW）：双哈希留痕可查
    rows = client.get(f"/api/v1/quality/corrections?project_id={project_id}",
                      headers=auditor_h).json()
    assert len(rows) == 1
    assert rows[0]["previous_package_hash"] == old_hash
    assert rows[0]["rearchive_package_hash"] == new_hash
    assert rows[0]["status"] == "已重新归档"

    assert verify_chain(db), "差错处理链全程留痕须可整链验证（硬约束 4）"
