"""FR-01-05 资料清单前置集成测试：立项 scope 快照生成清单 + 到料一一对应 + 一次性补齐。

清单项与客户层材料 file_key（R6 命名「类别」）子串匹配；缺口实时可见；
批量补齐逐项登记留痕并回显到料进度；非高频行业清单为空注记。
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


def _setup(client, db, business_type: str = "商贸", new_users: bool = True) -> str:
    """建客户 + 立项（商贸）+ 审批建档（FR-02-04 scope 圈定），返回 project_id。"""
    if new_users:
        _mkuser(db, "manager", "项目经理")
        _mkuser(db, "partner", "合伙人")
        _mkuser(db, "auditor", "审计员")
    if db.query(Client).count() == 0:
        db.add(Client(
            name="商洛鸿瑞怡园", uscc="91610100MA0000000X",
            industry="商贸", data_level="重要",
        ))
        db.commit()
    manager_h = _login(client, "manager")
    project_id = client.post("/api/v1/projects", json={
        "name": "鸿瑞怡园 2024 年报审计", "client_id": db.query(Client).first().id,
        "business_type": business_type, "period": "2024 年度",
        "evaluation": {
            "independence_ok": True, "client_risk_ok": True,
            "competence_ok": True, "engagement_letter_ok": True,
        },
    }, headers=manager_h).json()["id"]
    resp = client.post(f"/api/v1/projects/{project_id}/approve", headers=_login(client, "partner"))
    assert resp.status_code == 200, resp.text
    return project_id


def test_checklist_generation_and_batch_arrive(client, db):
    """清单随建档生成全缺 → 批量补齐部分到料 → 缺口实时收缩 → 空补齐 422。"""
    project_id = _setup(client, db)
    manager_h, auditor_h = _login(client, "manager"), _login(client, "auditor")

    # 清单随立项自动可见（scope 快照 6 项全缺）；审计员（worksheet.edit ≥ 查看口径）可查
    resp = client.get(f"/api/v1/materials/checklist?project_id={project_id}", headers=auditor_h)
    assert resp.status_code == 200, resp.text
    checklist = resp.json()
    assert checklist["in_matrix"] is True and checklist["industry_template"] == "商贸"
    assert checklist["total"] == 5 and checklist["arrived_count"] == 0
    assert checklist["complete"] is False and len(checklist["missing"]) == 5
    assert "进销存台账" in checklist["missing"]

    # 客户按清单一次性补齐 2 项（R6 命名：类别=清单项名）
    resp = client.post("/api/v1/materials/checklist/batch-arrive", json={
        "project_id": project_id,
        "entries": [
            {"item": "进销存台账", "file_key": "A2024-0001_鸿瑞怡园_2024_进销存台账.xlsx"},
            {"item": "主要购销合同", "file_key": "A2024-0001_鸿瑞怡园_2024_主要购销合同.pdf"},
        ],
    }, headers=manager_h)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert len(data["registered"]) == 2 and data["registered"][0]["material_id"]
    # 到料实时回显：清单项与到料状态一一对应，缺口收缩为 3
    cl = data["checklist"]
    assert cl["arrived_count"] == 2 and len(cl["missing"]) == 3
    arrived_row = next(r for r in cl["items"] if r["item"] == "进销存台账")
    assert arrived_row["arrived"] is True and arrived_row["matched_file_keys"]
    assert "进销存台账" not in cl["missing"] and "库存盘点表" in cl["missing"]

    # 客户层缺口登记（未到位）不进到料口径；补齐后清零
    assert client.post("/api/v1/materials", json={
        "project_id": project_id, "level": "customer",
        "file_key": "A2024-0001_鸿瑞怡园_2024_库存盘点表.xlsx", "required": True,
    }, headers=manager_h).status_code == 201
    assert client.get(f"/api/v1/materials/checklist?project_id={project_id}",
                      headers=manager_h).json()["missing"].count("库存盘点表") == 1

    # 负向：空补齐项 422；项目不存在 404
    assert client.post("/api/v1/materials/checklist/batch-arrive",
                       json={"project_id": project_id, "entries": []},
                       headers=manager_h).status_code == 422
    assert client.get("/api/v1/materials/checklist?project_id=NOPE",
                      headers=manager_h).status_code == 404

    # 非高频矩阵行业：清单为空注记，不阻断
    project2 = _setup(client, db, business_type="专项", new_users=False)
    cl2 = client.get(f"/api/v1/materials/checklist?project_id={project2}",
                     headers=manager_h).json()
    assert cl2["in_matrix"] is False and cl2["total"] == 0 and "人工圈定" in cl2["note"]

    assert verify_chain(db), "清单生成与批量补齐留痕须可整链验证（硬约束 4）"
