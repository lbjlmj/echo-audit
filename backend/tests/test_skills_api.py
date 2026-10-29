"""FR-07-03 Skill 统一管理集成测试：提案→质控审核→合伙人批准发布→版本回滚。

含 FR-07-05 构建期拦截负向用例（违规路径/凭证即拒绝提案）与
「未审核版本对无权限者不可见」权限矩阵断言（skill.publish：批准|提案|无|审核）。
"""
from __future__ import annotations

from app.core.audit_middleware import verify_chain
from app.models.knowledge import SkillAsset
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


def test_skill_publish_chain_and_scan_gate(client, db):
    """发布链：提案(经理)→质控审核→合伙人批准；先审后批；回滚恢复链上一版本。"""
    _mkuser(db, "manager", "项目经理")
    _mkuser(db, "partner", "合伙人")
    _mkuser(db, "auditor", "审计员")
    _mkuser(db, "qc", "质控专员")
    manager_h, partner_h, auditor_h, qc_h = (
        _login(client, "manager"), _login(client, "partner"),
        _login(client, "auditor"), _login(client, "qc"),
    )

    # FR-07-05 构建期拦截：违规 manifest（IP + 凭证）直接拒绝提案
    resp = client.post("/api/v1/knowledge/skills", json={
        "name": "ai-notes", "version": "1.1.0", "zip_key": "skills/ai-notes-1.1.0.zip",
        "manifest": "read source \\\\192.168.1.5\\share\\data.xlsx; password=secret66",
    }, headers=manager_h)
    assert resp.status_code == 422, resp.text
    assert "FR-07-05" in resp.text and "IP 地址" in resp.text and "凭证关键词" in resp.text
    assert db.query(SkillAsset).count() == 0, "违规 Skill 不得入库"

    # 审计员（skill.publish=无）提案 403；清单对无权限者不可见 403（PRD FR-07-03）
    resp = client.post("/api/v1/knowledge/skills", json={
        "name": "ai-notes", "version": "1.0.0", "zip_key": "skills/ai-notes-1.0.0.zip",
    }, headers=auditor_h)
    assert resp.status_code == 403
    assert client.get("/api/v1/knowledge/skills", headers=auditor_h).status_code == 403

    # 经理提案 v1.0.0 → 待审核，扫描通过随行留档
    resp = client.post("/api/v1/knowledge/skills", json={
        "name": "ai-notes", "version": "1.0.0", "zip_key": "skills/ai-notes-1.0.0.zip",
        "manifest": "steps: 取数-重分类-函证核对",
    }, headers=manager_h)
    assert resp.status_code == 201, resp.text
    v1 = resp.json()
    assert v1["review_status"] == "待审核" and v1["scan_result"]["passed"] is True
    assert v1["rollback_chain"] == []

    # 先审后批：未经质控审核直接批准 → 409
    resp = client.post(f"/api/v1/knowledge/skills/{v1['id']}/publish", headers=partner_h)
    assert resp.status_code == 409 and "先审后批" in resp.text

    # 质控审核（经理无 OPERATE 等级 → 403）→ 合伙人批准发布
    assert client.post(f"/api/v1/knowledge/skills/{v1['id']}/review", headers=manager_h).status_code == 403
    assert client.post(f"/api/v1/knowledge/skills/{v1['id']}/review", headers=qc_h).status_code == 200
    resp = client.post(f"/api/v1/knowledge/skills/{v1['id']}/publish", headers=partner_h)
    assert resp.status_code == 200 and resp.json()["review_status"] == "已发布"
    assert resp.json()["rollback_chain"][0]["version"] == "1.0.0"

    # 重复发布 → 409；重复提案同版本 → 409（唯一约束前置校验）
    assert client.post(f"/api/v1/knowledge/skills/{v1['id']}/publish", headers=partner_h).status_code == 409
    resp = client.post("/api/v1/knowledge/skills", json={
        "name": "ai-notes", "version": "1.0.0", "zip_key": "skills/ai-notes-1.0.0.zip",
    }, headers=manager_h)
    assert resp.status_code == 409

    # v1.1.0 提案→审→发布后回滚：v1.1.0 下线、v1.0.0 恢复发布（版本化回滚）
    resp = client.post("/api/v1/knowledge/skills", json={
        "name": "ai-notes", "version": "1.1.0", "zip_key": "skills/ai-notes-1.1.0.zip",
    }, headers=manager_h)
    v2 = resp.json()
    client.post(f"/api/v1/knowledge/skills/{v2['id']}/review", headers=qc_h)
    assert client.post(f"/api/v1/knowledge/skills/{v2['id']}/publish", headers=partner_h).status_code == 200
    resp = client.post(f"/api/v1/knowledge/skills/{v2['id']}/rollback", headers=qc_h)
    assert resp.status_code == 200 and resp.json()["review_status"] == "已下线"
    rows = {r["version"]: r for r in client.get("/api/v1/knowledge/skills", headers=partner_h).json()}
    assert rows["1.1.0"]["review_status"] == "已下线"
    assert rows["1.0.0"]["review_status"] == "已发布", "回滚应恢复链上一版本"
    assert rows["1.0.0"]["rollback_chain"][-1]["approved_by"].startswith("回滚恢复")
    assert verify_chain(db), "Skill 发布链留痕须可整链验证（硬约束 4）"
