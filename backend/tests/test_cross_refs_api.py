"""FR-03-05 交叉索引写入端点集成测试：保存时自动校验断链（R7）。

引用规范 [{"code": 底稿编号}]；断链实时提示不阻断保存；与质控检查器同口径
（FR-06-01 第 2 检查项复用断链清单）；复核链上（待复核起）锁定不可改。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.knowledge import RuleAnchor
from app.models.project import AuditProject, Client
from app.models.user import User
from app.models.worksheet import Worksheet
from app.services.auth import hash_password


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


def _setup(client, db) -> dict[str, str]:
    """项目 + 两张草稿底稿（货币资金表 / 抽凭表），返回 ids。"""
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    anchor = RuleAnchor(standard_no="1301", clause="第十章", purpose="函证程序底线要求")
    client_row = Client(
        name="商洛鸿瑞怡园", uscc="91610100MA0000000X",
        industry="商贸", data_level="重要",
    )
    db.add_all([anchor, client_row])
    db.commit()
    project = AuditProject(
        project_no="A2024-0001", name="鸿瑞怡园 2024 年报审计",
        client_id=client_row.id,
        business_type="商贸", period="2024 年度", status="进行中",
        approval_summary={},
    )
    db.add(project)
    db.flush()
    cash = Worksheet(
        project_id=project.id, code="A2024-0001-WS001", title="货币资金表",
        rule_anchor_id=anchor.id, status="草稿", current_version="v0",
    )
    sampling = Worksheet(
        project_id=project.id, code="A2024-0001-WS002", title="银行函证抽凭表",
        rule_anchor_id=anchor.id, status="草稿", current_version="v0",
    )
    db.add_all([cash, sampling])
    db.commit()
    return {"project": project.id, "cash": cash.id, "sampling": sampling.id,
            "cash_code": cash.code}


def test_cross_refs_write_and_broken_links(client, db):
    """写入 + 断链提示 + 状态锁定 + 权限矩阵 + 留痕整链。"""
    ids = _setup(client, db)
    auditor_h, qc_h = _login(client, "auditor"), _login(client, "qc")

    # 写入：引用存在底稿 + 不存在编号 → 200 + broken 实时提示（不阻断保存）
    resp = client.put(f"/api/v1/worksheets/{ids['cash']}/cross-refs", json={
        "refs": [
            {"code": ids["cash_code"], "note": "函证底稿见 WS002"},  # 自引用存在 ✓
            {"code": "A2024-0001-WS099", "note": "笔误编号"},        # 不存在 ✗
        ],
    }, headers=auditor_h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["broken_refs"] == ["A2024-0001-WS099"] and body["ok"] is False
    assert len(body["cross_refs"]) == 2, "断链引用仍保存（提示不阻断，质控检查器终验）"

    # 与质控检查器同口径：断链将进 FR-06-01 第 2 检查项（此处仅验数据持久化）
    ws = db.get(Worksheet, ids["cash"])
    db.refresh(ws)
    assert [r["code"] for r in ws.cross_refs] == [ids["cash_code"], "A2024-0001-WS099"]

    # 修正断链：清空重写为有效引用 → ok=True
    resp = client.put(f"/api/v1/worksheets/{ids['cash']}/cross-refs", json={
        "refs": [{"code": ids["cash_code"], "note": "函证底稿见 WS002"}],
    }, headers=auditor_h)
    assert resp.status_code == 200 and resp.json()["ok"] is True

    # 权限：质控（worksheet.edit=查看）403
    assert client.put(f"/api/v1/worksheets/{ids['sampling']}/cross-refs",
                      json={"refs": [{"code": ids["cash_code"]}]},
                      headers=qc_h).status_code == 403

    # 复核链锁定：底稿离开草稿态（模拟提交流转）后不可改引用（须先退回草稿）
    ws = db.get(Worksheet, ids["cash"])
    ws.status = "待复核"
    db.commit()
    assert client.put(f"/api/v1/worksheets/{ids['cash']}/cross-refs", json={
        "refs": [],
    }, headers=auditor_h).status_code == 409

    assert verify_chain(db), "交叉索引更新留痕须可整链验证（硬约束 4）"
