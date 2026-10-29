"""FR-03-04 全量异常 AI 集成测试：确定性评分 / 抽样规则 / 调整下限 / 执行回填锁定。

算法单点 engine/anomaly（硬约束 10）；AI 线索采信后才落抽凭清单（硬约束 3）；全程哈希链留痕（硬约束 4）。
"""
from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

from app.core.audit_middleware import verify_chain
from app.models.material import VoucherLine, VoucherSample
from app.models.user import User
from app.services.auth import hash_password

_EVAL_OK = {
    "independence_ok": True,
    "client_risk_ok": True,
    "competence_ok": True,
    "engagement_letter_ok": True,
}

BIG_DEBIT = Decimal("500000.00")  # 巨额离群凭证


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


def _setup(client, db) -> str:
    """五角色 + 客户 + 已批准项目，返回 project_id。"""
    from app.models.project import Client

    client_row = Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    db.add(client_row)
    db.commit()
    for login, role in (("manager", "项目经理"), ("partner", "合伙人"),
                        ("auditor", "审计员"), ("qc", "质控专员")):
        _mkuser(db, login, role)
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


def _add_vouchers(db, project_id: str) -> str:
    """确定性凭证池：1001 科目 24 笔小额 + 1 笔巨额离群；2202 科目 5 笔中等额。返回巨额笔 id。"""
    rng = random.Random(7)
    lines = [
        VoucherLine(
            project_id=project_id, voucher_no=f"记-{i:04d}", entry_date=date(2025, 12, 1),
            summary="货款", account_code="1001", account_name="库存现金",
            debit=Decimal(str(round(rng.uniform(800, 2000), 2))),
        )
        for i in range(24)
    ]
    big = VoucherLine(
        project_id=project_id, voucher_no="记-9999", entry_date=date(2025, 12, 31),
        summary="年末大额往来", account_code="1001", account_name="库存现金", debit=BIG_DEBIT,
    )
    lines += [
        VoucherLine(
            project_id=project_id, voucher_no=f"记-8{i:03d}", entry_date=date(2025, 12, 15),
            summary="应付暂估", account_code="2202", account_name="应付账款",
            credit=Decimal(str(round(rng.uniform(3000, 6000), 2))),
        )
        for i in range(5)
    ]
    db.add_all([*lines, big])
    db.commit()
    return big.id


def _scan(client, project_id: str, who: str) -> dict:
    resp = client.post(f"/api/v1/projects/{project_id}/anomaly-scan", headers=_login(client, who))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_scan_requires_data_and_permission(client, db):
    """无凭证数据禁止空转评分（422）；权限：审计员可发起，质控不可（PROPOSE < PROPOSE 档差）。"""
    project_id = _setup(client, db)
    auditor_h = _login(client, "auditor")

    resp = client.post(f"/api/v1/projects/{project_id}/anomaly-scan", headers=auditor_h)
    assert resp.status_code == 422 and "WS-1004" in resp.text  # 无数据

    big_id = _add_vouchers(db, project_id)
    assert big_id
    # 质控 ai_output.confirm=查看（VIEW < PROPOSE）→ 403
    assert client.post(
        f"/api/v1/projects/{project_id}/anomaly-scan", headers=_login(client, "qc")
    ).status_code == 403

    run = _scan(client, project_id, "auditor")
    assert run["module"] == "ai_anomaly" and run["status"] == "线索"
    assert "iforest" in run["model_version"]  # 评分版本号记入 AI 留痕（§15-8）
    # 线索未采信 → 抽凭清单不落库（硬约束 3）
    assert client.get(f"/api/v1/projects/{project_id}/samples", headers=auditor_h).json() == []
    assert verify_chain(db)


def test_scan_deterministic_and_sampling_rules(client, db):
    """固定种子两次评分摘要一致（§15-8 可复算）；采信后清单 ≥20 笔且来源三分支齐备。"""
    project_id = _setup(client, db)
    big_id = _add_vouchers(db, project_id)
    auditor_h, manager_h = _login(client, "auditor"), _login(client, "manager")

    run1 = _scan(client, project_id, "auditor")
    run2 = _scan(client, project_id, "auditor")
    assert run1["output_digest"] == run2["output_digest"], "同批数据评分必须可复现（§15-8）"

    # 线索采信（复用 /ai-runs/{run_no}/review 第三段）→ 抽凭清单落库
    resp = client.post(f"/api/v1/worksheets/ai-runs/{run2['run_no']}/review", json={
        "conclusion": "采信",
    }, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["status"] == "已采信"

    samples = client.get(f"/api/v1/projects/{project_id}/samples", headers=auditor_h).json()
    assert len(samples) >= 20, "默认抽取规则：合计不少于 20 笔（FR-03-04）"
    sources = {s["source"] for s in samples}
    assert {"评分Top10%", "科目Top5", "保底20笔"} <= sources, "三分支来源齐备"
    assert len({s["voucher_line_id"] for s in samples}) == len(samples), "无重复入清单"

    # 巨额离群笔必须入清单且评分为全场最高
    by_line = {s["voucher_line_id"]: s for s in samples}
    assert big_id in by_line
    max_score = max(s["score"] for s in by_line.values())
    assert by_line[big_id]["score"] == max_score
    assert verify_chain(db)


def test_adjust_lower_bound_permission_and_trace(client, db):
    """项目经理调整：追加标记「人工调整」；低于 5% 下限 422；审计员（plan.confirm=无）403。"""
    from sqlalchemy import func

    project_id = _setup(client, db)
    _add_vouchers(db, project_id)
    run = _scan(client, project_id, "auditor")
    assert client.post(f"/api/v1/worksheets/ai-runs/{run['run_no']}/review", json={
        "conclusion": "采信",
    }, headers=_login(client, "manager")).status_code == 200

    total = db.query(func.count(VoucherLine.id)).filter(
        VoucherLine.project_id == project_id
    ).scalar()
    sampled_lines = {s.voucher_line_id for s in db.query(VoucherSample).all()}
    unsampled = (
        db.query(VoucherLine.id).filter(
            VoucherLine.project_id == project_id, ~VoucherLine.id.in_(sampled_lines)
        ).first()[0]
    )
    sample_ids = [s.id for s in db.query(VoucherSample).all()]

    # 审计员 plan.confirm=无 → 403
    assert client.post(f"/api/v1/projects/{project_id}/samples/adjust", json={
        "add_ids": [unsampled],
    }, headers=_login(client, "auditor")).status_code == 403

    # 项目经理追加 → 人工调整来源，评分留空
    resp = client.post(f"/api/v1/projects/{project_id}/samples/adjust", json={
        "add_ids": [unsampled],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == len(sample_ids) + 1
    manual = [r for r in rows if r["voucher_line_id"] == unsampled][0]
    assert manual["source"] == "人工调整" and manual["score"] is None

    # 低于下限（5% 向上取整）→ 422；正常移除 → 200
    resp = client.post(f"/api/v1/projects/{project_id}/samples/adjust", json={
        "remove_ids": sample_ids,  # 全移 → 仅剩人工追加 1 笔 < 下限 2 笔
    }, headers=_login(client, "manager"))
    assert resp.status_code == 422 and "WS-1007" in resp.text

    resp = client.post(f"/api/v1/projects/{project_id}/samples/adjust", json={
        "remove_ids": sample_ids[:1],
    }, headers=_login(client, "manager"))
    assert resp.status_code == 200 and len(resp.json()) == len(sample_ids)
    assert verify_chain(db), "调整留痕须续哈希链（硬约束 4）"


def test_execute_backfill_lock_and_chain(client, db):
    """审计员回填执行结果留痕；说明必填；同一笔禁止重复执行；已执行不可移除。"""
    project_id = _setup(client, db)
    _add_vouchers(db, project_id)
    run = _scan(client, project_id, "auditor")
    assert client.post(f"/api/v1/worksheets/ai-runs/{run['run_no']}/review", json={
        "conclusion": "采信",
    }, headers=_login(client, "manager")).status_code == 200

    sample_id = db.query(VoucherSample).first().id
    qc_h = _login(client, "qc")
    # 质控 worksheet.edit=查看 → 403
    assert client.post(f"/api/v1/projects/samples/{sample_id}/execute", json={
        "exec_note": "已核对原始凭证无误",
    }, headers=qc_h).status_code == 403

    auditor_h = _login(client, "auditor")
    resp = client.post(f"/api/v1/projects/samples/{sample_id}/execute", json={
        "exec_note": "已核对原始凭证无误，附件齐备",
    }, headers=auditor_h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["exec_status"] == "已执行" and body["executed_by"]
    # 已执行 → 移除被拒 + 重复执行被拒
    assert client.post(f"/api/v1/projects/{project_id}/samples/adjust", json={
        "remove_ids": [sample_id],
    }, headers=_login(client, "manager")).status_code == 409
    assert client.post(f"/api/v1/projects/samples/{sample_id}/execute", json={
        "exec_note": "重复回填应被拒",
    }, headers=auditor_h).status_code == 409
    assert verify_chain(db)
