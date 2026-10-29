"""FR-01-04 资料自动分类集成测试：关键词线索 → 审计员确认 → R6 重命名归档。

三段式（红线 1/R2）：线索态不可直接归档；确认 digest 防篡改 + 一次性；
不采信必附原因（§15-16）；采信后 FR-01-03 命名引擎接线（R6 系统重命名）；
清单到料匹配用原始文件名，重命名不影响 FR-01-05 口径。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.project import Client
from app.models.user import User
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


def _setup(client, db) -> str:
    """建客户 + 立项（商贸）+ 审批建档 + 登记 3 份原始材料，返回 project_id。"""
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    db.add(Client(
        name="商洛鸿瑞怡园", uscc="91610100MA0000000X",
        industry="商贸", data_level="重要",
    ))
    db.commit()
    manager_h = _login(client, "manager")
    project_id = client.post("/api/v1/projects", json={
        "name": "鸿瑞怡园 2024 年报审计", "client_id": db.query(Client).first().id,
        "business_type": "商贸", "period": "2024 年度",
        "evaluation": {
            "independence_ok": True, "client_risk_ok": True,
            "competence_ok": True, "engagement_letter_ok": True,
        },
    }, headers=manager_h).json()["id"]
    resp = client.post(f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner"))
    assert resp.status_code == 200, resp.text
    for name in ("业务约定书.pdf", "进销存台账.xlsx", "无法识别资料.dat"):
        assert client.post("/api/v1/materials", json={
            "project_id": project_id, "level": "customer", "file_key": name,
        }, headers=_login(client, "auditor")).status_code == 201
    return project_id


def test_material_classification_flow(client, db):
    """线索 → 防篡改拒绝 → 篡改类别拒绝 → 采信 R6 归档 → 一次性 → 不采信留痕。"""
    project_id = _setup(client, db)
    manager_h, auditor_h, qc_h = (
        _login(client, "manager"), _login(client, "auditor"), _login(client, "qc"),
    )

    # 审计员触发分类（ai_output.confirm ≥ 提交待复核）：3 份材料全部产出线索
    resp = client.post("/api/v1/materials/classify",
                       json={"project_id": project_id}, headers=auditor_h)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    run_no = data["run_no"]
    entries = data["entries"]
    by_reason = {e["material_id"]: e for e in entries}
    assert len(entries) == 3
    assert [e["catalog"] for e in entries].count("QT") == 2, "台账/无法识别均归 QT 待人工改判"

    # 未确认不得归档（红线 1）：材料 file_key 未被重命名
    mats = client.get(f"/api/v1/materials?project_id={project_id}", headers=manager_h).json()
    assert all(m["catalog"] is None for m in mats)

    # 质控（ai_output.confirm=抽查 < OPERATE）不可确认
    assert client.post("/api/v1/materials/classify/confirm", json={
        "run_no": run_no, "entries": entries, "conclusion": "采信",
    }, headers=qc_h).status_code == 403

    # 防篡改：改类别后确认 → 封印校验拒绝
    tampered = [dict(e, catalog="BG") for e in entries]
    assert client.post("/api/v1/materials/classify/confirm", json={
        "run_no": run_no, "entries": tampered, "conclusion": "采信",
    }, headers=manager_h).status_code == 409

    # 采信确认：R6 重命名归档（FR-01-03 接线），目录类别回写
    resp = client.post("/api/v1/materials/classify/confirm", json={
        "run_no": run_no, "entries": entries, "conclusion": "采信",
    }, headers=manager_h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "已采信"
    mats = {m["file_key"]: m for m in
            client.get(f"/api/v1/materials?project_id={project_id}", headers=manager_h).json()}
    renamed = list(mats)
    assert any("-CJ" in k for k in renamed), "约定书按 R6 命名含 CJ 类别"
    assert any("-QT" in k for k in renamed)
    assert all(k.split("-")[-1].split(".")[0] in
               {"CJ", "QT"} or "-CJ" in k or "-QT" in k for k in renamed)
    by_origin = {m["origin_name"]: m for m in mats.values()}
    # R6 全格式校验：项目编号-委托方-年份-类别，扩展名保留（系统生成人工不可改写）
    project_no = next(
        p for p in client.get("/api/v1/projects", headers=manager_h).json()
        if p["id"] == project_id
    )["project_no"]
    assert by_origin["业务约定书.pdf"]["file_key"] == f"{project_no}-商洛鸿瑞怡园-2024-CJ.pdf"
    assert by_origin["无法识别资料.dat"]["file_key"].endswith("-QT.dat")

    # 确认一次性（append-only）：重复确认 409
    assert client.post("/api/v1/materials/classify/confirm", json={
        "run_no": run_no, "entries": entries, "conclusion": "采信",
    }, headers=manager_h).status_code == 409

    # 不采信必附原因（§15-16）：新登记材料 → 新线索 → 不采信无原因 422 / 附原因通过
    assert client.post("/api/v1/materials", json={
        "project_id": project_id, "level": "customer", "file_key": "存货盘点表.xlsx",
    }, headers=auditor_h).status_code == 201
    entries2 = client.post("/api/v1/materials/classify",
                           json={"project_id": project_id}, headers=auditor_h
                           ).json()["entries"]
    assert entries2[0]["catalog"] == "JH", "盘点表命中计划类关键词"
    assert client.post("/api/v1/materials/classify/confirm", json={
        "run_no": run_no, "entries": entries2, "conclusion": "不采信",
    }, headers=manager_h).status_code == 409  # 旧 run 已确认，一次性拦截
    run_no2 = client.post("/api/v1/materials/classify",
                          json={"project_id": project_id}, headers=auditor_h).json()["run_no"]
    assert client.post("/api/v1/materials/classify/confirm", json={
        "run_no": run_no2, "entries": entries2, "conclusion": "不采信",
    }, headers=manager_h).status_code == 422, "不采信未附原因"
    resp = client.post("/api/v1/materials/classify/confirm", json={
        "run_no": run_no2, "entries": entries2, "conclusion": "不采信",
        "rejection_reason": "其他",  # §15-16 原因分类枚举
    }, headers=manager_h)
    assert resp.status_code == 200 and resp.json()["rejection_reason"] == "其他"

    assert verify_chain(db), "分类线索与确认留痕须可整链验证（硬约束 4）"
