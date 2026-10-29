"""实体落位冒烟（TECH_DESIGN §3.2）。

覆盖：26 实体注册完整性（23 基线 + FR-03-04 凭证池/抽凭清单 + §15-16 差错归档单）、
默认值、状态 CHECK 与 engine 状态机严格一致（硬约束 2）、R1 唯一约束、红线 2 准则锚点不可空外键。
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable

import app.models as m
from app.engine.state_machines import ALL_MACHINES
from app.models.base import Base

EXPECTED_TABLES = {
    "users", "project_members", "clients", "audit_projects", "stage_gates",
    "materials", "import_field_mappings", "risk_items", "audit_programs",
    "materialities", "worksheets", "worksheet_versions", "worksheet_reviews",
    "confirmations", "findings", "audit_reports", "audit_trails", "ai_runs",
    "templates", "rule_anchors", "rule_relations", "knowledge_items", "skill_assets",
    "voucher_lines", "voucher_samples", "archive_corrections",
}


def _machine_states(machine) -> set[str]:
    """状态全集 = 流转起点 ∪ 流转终点（与 PRD §4 枚举一致）。"""
    states = set(machine.transitions)
    for targets in machine.transitions.values():
        states |= {t.target for t in targets}
    return states


def test_26_entities_registered():
    assert EXPECTED_TABLES == set(Base.metadata.tables)


def test_status_constraints_match_engine():
    """状态字段 CHECK 必须覆盖状态机全部状态（禁止自创状态名，硬约束 2）。"""
    mapping = {
        "audit_projects": ALL_MACHINES["project"],
        "worksheets": ALL_MACHINES["worksheet"],
        "confirmations": ALL_MACHINES["confirmation"],
        "findings": ALL_MACHINES["finding"],
        "archive_corrections": ALL_MACHINES["correction"],
    }
    for table_name, machine in mapping.items():
        ddl = str(
            CreateTable(Base.metadata.tables[table_name]).compile(dialect=postgresql.dialect())
        )
        for state in _machine_states(machine):
            assert f"'{state}'" in ddl, f"{table_name} 状态 CHECK 缺少「{state}」"


def _seed(db):
    """最小可用链路：客户→用户→模板→准则锚点→项目。"""
    client = m.Client(name="示例公司", uscc="91110000MA01X000XX", industry="商贸", data_level="一般")
    user = m.User(name="张三", login_name="zhangsan", password_hash="x", global_role="审计员")
    tpl = m.Template(type="商贸", version="v1", skeleton_key="tpl/1.xlsx")
    anchor = m.RuleAnchor(standard_no="1312", clause="第十条", purpose="函证程序设计")
    db.add_all([client, user, tpl, anchor])
    db.flush()
    project = m.AuditProject(
        project_no="2026-SM-001", name="示例公司年报审计", client_id=client.id,
        business_type="商贸", template_id=tpl.id, period="2025 年度",
    )
    db.add(project)
    db.flush()
    return client, user, tpl, anchor, project


def test_defaults_and_tenant(db):
    _seed(db)
    ws = m.Worksheet(code="MM-1", title="货币资金", project_id="p1", rule_anchor_id="a1")
    confirm = m.Confirmation(type="银行", counterparty="示例银行", amount=Decimal("1000.00"), project_id="p1")
    run = m.AIRun(run_no="AI-20261027-TEST", project_id="p1", module="ai_risk",
                  skill_version="v1", model_version="local", triggered_by="u1")
    db.add_all([ws, confirm, run])
    db.flush()  # 列默认值（status/tenant_id/remind_count 等）在 flush 时应用
    assert ws.status == "草稿"
    assert confirm.status == "台账已生成" and confirm.remind_count == 0
    assert run.status == "线索" and run.input_material_ids == []
    for entity in (ws, confirm, run):
        assert entity.tenant_id == "default"


def test_worksheet_requires_rule_anchor(db):
    """红线 2：底稿准则锚点不可空外键，缺失即拒绝落库。"""
    _seed(db)
    db.add(m.Worksheet(code="MM-2", title="无锚点底稿", project_id="p1", rule_anchor_id=None))
    with pytest.raises(IntegrityError):
        db.flush()


def test_member_one_role_per_project(db):
    """R1：一人一项目仅一个角色行，编制与复核不得同一人（库端唯一约束）。"""
    _, user, _, _, project = _seed(db)
    db.add(m.ProjectMember(project_id=project.id, user_id=user.id, project_role="编制"))
    db.flush()
    db.add(m.ProjectMember(project_id=project.id, user_id=user.id, project_role="复核"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_user_role_check_rejects_unknown_role(db):
    _seed(db)
    db.add(m.User(name="李四", login_name="lisi", password_hash="x", global_role="超级管理员"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_full_business_chain(db):
    """最小业务链贯通：成员登记→材料→风险→程序→重要性→版本→复核→函证→问题→留痕。"""
    _, user, _, anchor, project = _seed(db)
    db.add_all([
        m.ProjectMember(project_id=project.id, user_id=user.id, project_role="编制"),
        m.Material(project_id=project.id, level="customer", file_key="mat/1.zip"),
        m.RiskItem(project_id=project.id, source="ai", description="收入确认风险", risk_level="高"),
        m.Materiality(project_id=project.id, benchmark_type="营业收入",
                      benchmark_amount=Decimal("10000000"), ratio=Decimal("0.005"),
                      overall_materiality=Decimal("50000")),
    ])
    db.flush()
    program = m.AuditProgram(risk_id=db.query(m.RiskItem).first().id,
                             program_code="P-01", package_type="高风险全程序", exec_status="执行中")
    db.add(program)
    ws = m.Worksheet(project_id=project.id, code="SR-1", title="收入", rule_anchor_id=anchor.id,
                     cross_refs=[{"ref": "MM-1"}])
    db.add(ws)
    db.flush()
    version = m.WorksheetVersion(worksheet_id=ws.id, version_no="v1", file_key="ws/sr-1-v1.xlsx",
                                 prepared_by=user.id)
    db.add(version)
    db.flush()
    db.add_all([
        m.WorksheetReview(worksheet_id=ws.id, version_id=version.id, reviewer_id=user.id,
                          conclusion="通过", signature={"signer": "张三", "date": "2026-10-27", "license": "1100001"}),
        m.Finding(project_id=project.id, worksheet_id=ws.id, description="经营范围与实际销售不符",
                  source="AI 归集"),
        m.AuditTrail(entity_type="worksheet", entity_id=ws.id, action="创建", operator="张三",
                     before_snapshot=None, after_snapshot={"code": "SR-1"},
                     prev_hash="0" * 64, entry_hash="a" * 64, ts=time.time()),
    ])
    db.flush()
    assert db.query(m.Finding).first().status == "已归集"
    assert db.query(m.AuditTrail).first().entry_hash == "a" * 64
