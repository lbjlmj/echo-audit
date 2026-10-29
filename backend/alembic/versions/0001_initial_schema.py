"""initial schema — TECH_DESIGN §3.2 全量 23 实体（手写迁移，不 autogenerate）

Revision ID: 0001
Revises:
Create Date: 2026-10-27

约定：
- JSONB 仅 PostgreSQL（生产）；tenant_id 全表贯通（§3.3.4，托管形态启用 RLS）
- 状态 CHECK 与 engine/state_machines.py 枚举严格一致（硬约束 2）
- audit_trails 结构冻结 append-only；库端回收 UPDATE/DELETE 权限（§4.3.2，运维层执行）
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

JSONB = pg.JSONB


def _in(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # ---- 基础实体：用户 / 客户 / 模板 / 准则锚点（无外键依赖） ----
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(50), nullable=False, comment="姓名"),
        sa.Column("login_name", sa.String(50), nullable=False, unique=True, comment="登录名"),
        sa.Column("password_hash", sa.String(200), nullable=False, comment="密码哈希（argon2）"),
        sa.Column("global_role", sa.String(20), nullable=False, comment="全局角色（PRD §3）"),
        sa.Column("license_no", sa.String(50), comment="执业证书号"),
        sa.Column("status", sa.String(10), nullable=False, comment="账号状态"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("global_role IN ('合伙人','项目经理','审计员','质控专员')", name="ck_user_role"),
        sa.CheckConstraint("status IN ('启用','停用')", name="ck_user_status"),
    )
    op.create_table(
        "clients",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(200), nullable=False, comment="客户名称"),
        sa.Column("uscc", sa.String(18), nullable=False, comment="统一社会信用代码"),
        sa.Column("industry", sa.String(50), nullable=False, comment="行业"),
        sa.Column("data_level", sa.String(10), nullable=False, comment="数据等级（财政部三级）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("data_level IN ('核心','重要','一般')", name="ck_client_data_level"),
        sa.UniqueConstraint("tenant_id", "uscc", name="uq_client_tenant_uscc"),
    )
    op.create_table(
        "templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("type", sa.String(50), nullable=False, comment="行业类型"),
        sa.Column("version", sa.String(20), nullable=False, comment="模板版本"),
        sa.Column("skeleton_key", sa.String(200), nullable=False, comment="底稿骨架对象键"),
        sa.Column("program_mapping", JSONB, comment="程序包默认映射"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("type", "version", name="uq_template_type_version"),
    )
    op.create_table(
        "rule_anchors",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("standard_no", sa.String(20), nullable=False, comment="准则号"),
        sa.Column("clause", sa.String(50), nullable=False, comment="条款"),
        sa.Column("purpose", sa.String(200), nullable=False, comment="程序目的"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("standard_no", "clause", name="uq_anchor_std_clause"),
    )

    # ---- 项目域 ----
    op.create_table(
        "audit_projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_no", sa.String(32), nullable=False, unique=True, comment="项目编号（命名引擎）"),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True),
        sa.Column("business_type", sa.String(20), nullable=False, comment="业务类型（§3.2 十类）"),
        sa.Column("template_id", sa.String(36), sa.ForeignKey("templates.id")),
        sa.Column("current_stage", sa.String(10), nullable=False, comment="当前八阶段"),
        sa.Column("status", sa.String(10), nullable=False, comment="项目状态（四状态）"),
        sa.Column("period", sa.String(20), nullable=False, comment="审计期间"),
        sa.Column("approval_summary", JSONB, comment="立项评价结论"),
        sa.Column("paused_at", sa.DateTime(timezone=True), comment="暂停时间（§15-17）"),
        sa.Column("archive_package_hash", sa.String(64), comment="归档包哈希（FR-06-04，出证依据）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"current_stage IN {_in(('承接','计划','风险评估','内控','实质性','完成','报告','归档'))}", name="ck_project_stage"),
        sa.CheckConstraint(f"status IN {_in(('进行中','暂停','已归档','终止'))}", name="ck_project_status"),
        sa.CheckConstraint(f"business_type IN {_in(('一般企业年报','小企业准则','事业单位','房地产','建安','商贸','科技型中小企业','非上市','专项','经责'))}", name="ck_project_biz_type"),
    )
    op.create_table(
        "project_members",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("project_role", sa.String(10), nullable=False, comment="项目角色（编制/复核/查看）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "user_id", name="uq_member_project_user"),
        sa.CheckConstraint("project_role IN ('编制','复核','查看')", name="ck_member_role"),
    )
    op.create_table(
        "stage_gates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("stage", sa.String(10), nullable=False, comment="阶段"),
        sa.Column("gate_status", sa.String(10), nullable=False, comment="门状态"),
        sa.Column("conditions_snapshot", JSONB, comment="通过条件快照"),
        sa.Column("passed_by", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("passed_at", sa.DateTime(timezone=True)),
        sa.Column("skip_partner_id", sa.String(36), sa.ForeignKey("users.id"), comment="跳过双批：合伙人（R3）"),
        sa.Column("skip_qc_id", sa.String(36), sa.ForeignKey("users.id"), comment="跳过双批：质控专员（R3）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"stage IN {_in(('承接','计划','风险评估','内控','实质性','完成','报告','归档'))}", name="ck_gate_stage"),
        sa.CheckConstraint("gate_status IN ('未开始','进行中','已通过','跳过')", name="ck_gate_status"),
    )

    # ---- 资料域 ----
    op.create_table(
        "materials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("level", sa.String(10), nullable=False, comment="层级（customer/firm/rule）"),
        sa.Column("file_key", sa.String(200), nullable=False, comment="对象键"),
        sa.Column("parse_status", sa.String(10), nullable=False, comment="解析状态"),
        sa.Column("vector_ref", sa.String(200), comment="向量库引用"),
        sa.Column("gap_flag", sa.Boolean(), nullable=False, comment="缺口标志"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("level IN ('customer','firm','rule')", name="ck_material_level"),
        sa.CheckConstraint("parse_status IN ('待解析','解析中','已解析','失败')", name="ck_material_parse_status"),
    )
    op.create_table(
        "import_field_mappings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False, index=True),
        sa.Column("source_system", sa.String(100), nullable=False, comment="客户/来源系统"),
        sa.Column("mapping_type", sa.String(10), nullable=False, comment="映射类型（科目余额表/明细账，§15-6）"),
        sa.Column("field_mapping", JSONB, nullable=False, comment="源字段→标准字段映射"),
        sa.CheckConstraint("mapping_type IN ('科目余额表','明细账')", name="ck_mapping_type"),
        sa.Column("mapped_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False, comment="映射人"),
        sa.Column("reuse_scope", sa.String(10), nullable=False, comment="复用范围（本客户/全所）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ---- 风险与程序域 ----
    op.create_table(
        "risk_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("source", sa.String(10), nullable=False, comment="来源（ai/人工）"),
        sa.Column("description", sa.String(500), nullable=False, comment="财报层风险描述"),
        sa.Column("assertion_mapping", sa.String(200), comment="认定层次映射"),
        sa.Column("anchor_id", sa.String(36), sa.ForeignKey("rule_anchors.id"), comment="准则锚点（AI 项必带）"),
        sa.Column("recommended_programs", JSONB, comment="AI 推荐程序组合"),
        sa.Column("risk_level", sa.String(5), nullable=False, comment="风险等级"),
        sa.Column("status", sa.String(10), nullable=False, comment="确认状态"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('ai','人工')", name="ck_risk_source"),
        sa.CheckConstraint("risk_level IN ('高','中','低')", name="ck_risk_level"),
        sa.CheckConstraint("status IN ('待确认','已确认','已应对')", name="ck_risk_status"),
    )
    op.create_table(
        "audit_programs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("risk_id", sa.String(36), sa.ForeignKey("risk_items.id"), nullable=False, index=True),
        sa.Column("program_code", sa.String(32), nullable=False, comment="程序代码"),
        sa.Column("package_type", sa.String(20), nullable=False, comment="程序包类型（R5）"),
        sa.Column("min_standard_note", sa.String(200), comment="简化包必带：准则最低底线标注"),
        sa.Column("worksheet_requirement", sa.String(100), comment="关联底稿需求"),
        sa.Column("exec_status", sa.String(10), nullable=False, comment="执行状态"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("package_type IN ('高风险全程序','低风险简化程序包')", name="ck_program_pkg"),
        sa.CheckConstraint("exec_status IN ('未执行','执行中','已完成')", name="ck_program_exec"),
    )
    op.create_table(
        "materialities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, unique=True, index=True),
        sa.Column("benchmark_type", sa.String(10), nullable=False, comment="基准类型"),
        sa.Column("benchmark_amount", sa.Numeric(18, 2), nullable=False, comment="基准金额"),
        sa.Column("ratio", sa.Numeric(8, 4), nullable=False, comment="计算比例"),
        sa.Column("overall_materiality", sa.Numeric(18, 2), nullable=False, comment="整体重要性"),
        sa.Column("execution_ratio", sa.Numeric(5, 2), nullable=False, comment="实际执行比例（默认 60）"),
        sa.Column("trivial_ratio", sa.Numeric(5, 2), nullable=False, comment="明显微小比例（默认 5）"),
        sa.Column("change_log", JSONB, comment="修改留痕"),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id"), comment="批准合伙人（R8）"),
        sa.Column("approved_at", sa.DateTime(timezone=True), comment="批准时间"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("benchmark_type IN ('营业收入','费用总额','总资产')", name="ck_mat_benchmark"),
    )

    # ---- 知识图谱（底稿外键依赖） ----
    op.create_table(
        "rule_relations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("rule_from_id", sa.String(36), sa.ForeignKey("rule_anchors.id"), nullable=False, index=True),
        sa.Column("rule_to_id", sa.String(36), sa.ForeignKey("rule_anchors.id"), nullable=False, index=True),
        sa.Column("relation_type", sa.String(10), nullable=False, comment="关系类型"),
        sa.Column("graph_version", sa.String(20), nullable=False, comment="图谱版本"),
        sa.CheckConstraint("relation_type IN ('上下位','引用','关联')", name="ck_relation_type"),
    )

    # ---- 底稿域 ----
    op.create_table(
        "worksheets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("code", sa.String(32), nullable=False, comment="底稿编号（R6）"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("template_id", sa.String(36), sa.ForeignKey("templates.id")),
        sa.Column("rule_anchor_id", sa.String(36), sa.ForeignKey("rule_anchors.id"), nullable=False, comment="准则锚点（不可空，红线 2）"),
        sa.Column("current_version", sa.String(20), nullable=False, comment="当前版本号"),
        sa.Column("status", sa.String(10), nullable=False, comment="六状态（PRD §4.3）"),
        sa.Column("file_key", sa.String(200), comment="当前文件对象键"),
        sa.Column("is_important", sa.Boolean(), comment="重要底稿（触发终审，§15-11）"),
        sa.Column("cross_refs", JSONB, comment="交叉索引引用（R7）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN {_in(('草稿','待复核','复核中','复核通过','终审通过','已归档'))}", name="ck_ws_status"),
    )
    op.create_table(
        "worksheet_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("worksheet_id", sa.String(36), sa.ForeignKey("worksheets.id"), nullable=False, index=True),
        sa.Column("version_no", sa.String(20), nullable=False, comment="版本号"),
        sa.Column("file_key", sa.String(200), nullable=False, comment="文件对象键"),
        sa.Column("change_summary", sa.String(500), comment="变更摘要"),
        sa.Column("prepared_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False, comment="编制人"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "worksheet_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("worksheet_id", sa.String(36), sa.ForeignKey("worksheets.id"), nullable=False, index=True),
        sa.Column("version_id", sa.String(36), sa.ForeignKey("worksheet_versions.id"), nullable=False, comment="复核针对的版本"),
        sa.Column("round_no", sa.Integer(), nullable=False, comment="复核轮次"),
        sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False, comment="复核人"),
        sa.Column("opinion", sa.Text(), comment="复核意见（退回必附）"),
        sa.Column("revision_requirement", sa.Text(), comment="修订要求"),
        sa.Column("signature", JSONB, comment="电子签名（复核人+日期+证书号）"),
        sa.Column("conclusion", sa.String(10), comment="结论（通过/退回）"),
        sa.Column("reviewed_date", sa.Date(), comment="复核日期"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("conclusion IN ('通过','退回')", name="ck_review_conclusion"),
    )

    # ---- 函证域 ----
    op.create_table(
        "confirmations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("type", sa.String(10), nullable=False, comment="类型（银行/往来）"),
        sa.Column("counterparty", sa.String(200), nullable=False, comment="对方单位"),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False, comment="函证金额（不出站）"),
        sa.Column("status", sa.String(10), nullable=False, comment="八状态（PRD §4.4）"),
        sa.Column("issued_at", sa.DateTime(timezone=True), comment="发出时间"),
        sa.Column("planned_reply_date", sa.Date(), comment="计划回函日期"),
        sa.Column("waybill_no", sa.String(50), comment="快递单号（适配器回写）"),
        sa.Column("logistics_status", sa.String(50), comment="物流状态"),
        sa.Column("difference", JSONB, comment="回函差异"),
        sa.Column("alt_test_record", sa.Text(), comment="替代测试记录"),
        sa.Column("remind_count", sa.Integer(), nullable=False, comment="超期提醒计数（上限 2）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN {_in(('台账已生成','已发出','已回函','超期未回','核对一致','差异处理','替代程序','闭环'))}", name="ck_cf_status"),
        sa.CheckConstraint("type IN ('银行','往来')", name="ck_cf_type"),
    )

    # ---- 问题与报告域 ----
    op.create_table(
        "findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("worksheet_id", sa.String(36), sa.ForeignKey("worksheets.id"), nullable=False, comment="来源底稿"),
        sa.Column("description", sa.Text(), nullable=False, comment="问题描述"),
        sa.Column("source", sa.String(10), nullable=False, comment="来源（AI 归集/人工）"),
        sa.Column("status", sa.String(10), nullable=False, comment="六状态（PRD §4.5）"),
        sa.Column("responsible_manager_id", sa.String(36), sa.ForeignKey("users.id"), comment="责任项目经理"),
        sa.Column("escalated", sa.Boolean(), nullable=False, server_default=sa.false(), comment="升级合伙人裁决（FR-05-05）"),
        sa.Column("escalation_note", sa.Text(), comment="分歧说明（升级登记时填写）"),
        sa.Column("escalation_ref", sa.String(100), comment="分歧裁决引用（FR-05-05）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"status IN {_in(('已归集','已确认','处理中','已解决','已关闭','已排除'))}", name="ck_fd_status"),
        sa.CheckConstraint("source IN ('AI 归集','人工')", name="ck_fd_source"),
    )
    op.create_table(
        "audit_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("version", sa.String(20), nullable=False, comment="报告版本"),
        sa.Column("notes_draft_key", sa.String(200), comment="附注草稿对象键（复核采信后进入报告稿，FR-05-03）"),
        sa.Column("template_key", sa.String(200), comment="报告模板对象键（FR-05-04 模板出稿）"),
        sa.Column("status", sa.String(10), nullable=False, comment="状态（草稿/会签/已签发）"),
        sa.Column("countersigned_by", sa.String(36), sa.ForeignKey("users.id"), comment="会签质控（FR-05-04）"),
        sa.Column("countersigned_at", sa.DateTime(timezone=True), comment="会签时间"),
        sa.Column("signed_by", sa.String(36), sa.ForeignKey("users.id"), comment="签发合伙人"),
        sa.Column("signed_at", sa.DateTime(timezone=True), comment="签发时间"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('草稿','会签','已签发')", name="ck_report_status"),
    )

    # ---- 留痕域（结构冻结） ----
    op.create_table(
        "audit_trails",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("entity_type", sa.String(50), nullable=False, comment="实体类型"),
        sa.Column("entity_id", sa.String(36), nullable=False, index=True, comment="实体 id（弱关联）"),
        sa.Column("action", sa.String(50), nullable=False, comment="动作"),
        sa.Column("operator", sa.String(50), nullable=False, comment="操作人"),
        sa.Column("before_snapshot", JSONB, comment="变更前快照"),
        sa.Column("after_snapshot", JSONB, comment="变更后快照"),
        sa.Column("prev_hash", sa.String(64), nullable=False, comment="前条哈希（哈希链）"),
        sa.Column("entry_hash", sa.String(64), nullable=False, comment="本条哈希（append-only 封印）"),
        sa.Column("ts", sa.Float(), nullable=False, comment="纪元时间戳（哈希复算依据）"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        comment="全量操作留痕：结构冻结、append-only；库端回收 UPDATE/DELETE 权限（§4.3.2）",
    )
    op.create_table(
        "ai_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("run_no", sa.String(40), nullable=False, unique=True, comment="留痕编号（全局唯一）"),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("module", sa.String(20), nullable=False, comment="AI 模块"),
        sa.Column("skill_version", sa.String(20), nullable=False, comment="Skill 版本"),
        sa.Column("model_version", sa.String(50), nullable=False, comment="模型版本"),
        sa.Column("triggered_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False, comment="触发人"),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, comment="触发时间"),
        sa.Column("input_material_ids", JSONB, nullable=False, comment="输入材料 id 数组（弱关联）"),
        sa.Column("input_hashes", JSONB, nullable=False, comment="输入哈希"),
        sa.Column("output_digest", sa.String(64), comment="输出摘要哈希"),
        sa.Column("output_key", sa.String(200), comment="输出对象键"),
        sa.Column("status", sa.String(10), nullable=False, comment="线索/复核/采信状态"),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id"), comment="复核人（第三段）"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), comment="复核日期（第三段）"),
        sa.Column("rejection_reason", sa.String(10), comment="不采信原因分类（§15-16）"),
        sa.Column("related_type", sa.String(20), comment="关联对象类型（底稿/函证/问题）"),
        sa.Column("related_id", sa.String(36), comment="关联对象 id"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"module IN {_in(('ai_risk','ai_anomaly','ai_confirm','ai_finding','ai_notes','ai_qc','rag'))}", name="ck_airun_module"),
        sa.CheckConstraint(f"status IN {_in(('线索','已复核','已采信','部分采信','不采信'))}", name="ck_airun_status"),
        sa.CheckConstraint("rejection_reason IN ('事实错误','口径错误','幻觉','其他') OR rejection_reason IS NULL", name="ck_airun_reject"),
    )

    # ---- 知识资产域 ----
    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("item_type", sa.String(20), nullable=False, comment="类型（§3.2 五类）"),
        sa.Column("content", sa.Text(), nullable=False, comment="内容"),
        sa.Column("vector_ref", sa.String(200), comment="向量引用"),
        sa.Column("rule_anchor_id", sa.String(36), sa.ForeignKey("rule_anchors.id"), comment="关联准则锚点"),
        sa.Column("source_project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), comment="来源项目（经验回流）"),
        sa.Column("status", sa.String(20), nullable=False, comment="入库审批状态"),
        sa.Column("proposed_by", sa.String(36), sa.ForeignKey("users.id"), comment="提案人"),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id"), comment="审核人（质控）"),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id"), comment="批准人（合伙人）"),
        sa.Column("reject_reason", sa.String(200), comment="驳回理由"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("item_type IN ('准则','应用指南','监管规定','中注协底稿指南','本所沉淀经验')", name="ck_kn_type"),
        sa.CheckConstraint("status IN ('提案','质控审核中','待合伙人批准','已发布','已驳回')", name="ck_kn_status"),
    )
    op.create_table(
        "skill_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False, comment="Skill 名称（audit-*/ai-*）"),
        sa.Column("version", sa.String(20), nullable=False, comment="版本"),
        sa.Column("zip_key", sa.String(200), nullable=False, comment="zip 对象键"),
        sa.Column("review_status", sa.String(10), nullable=False, comment="审核状态"),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id"), comment="审核人"),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id"), comment="批准人"),
        sa.Column("scan_result", JSONB, comment="静态扫描结果"),
        sa.Column("rollback_chain", JSONB, comment="回滚版本链"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_skill_name_version"),
        sa.CheckConstraint("review_status IN ('待审核','已发布','已下线')", name="ck_skill_status"),
    )
    op.create_table(
        "voucher_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("voucher_no", sa.String(50), nullable=False, comment="凭证号"),
        sa.Column("entry_date", sa.Date(), comment="记账日期"),
        sa.Column("summary", sa.String(200), comment="摘要"),
        sa.Column("account_code", sa.String(30), nullable=False, index=True, comment="科目编码"),
        sa.Column("account_name", sa.String(100), comment="科目名称"),
        sa.Column("debit", sa.Numeric(18, 2), comment="借方金额"),
        sa.Column("credit", sa.Numeric(18, 2), comment="贷方金额"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "voucher_samples",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("audit_projects.id"), nullable=False, index=True),
        sa.Column("voucher_line_id", sa.String(36), sa.ForeignKey("voucher_lines.id"), nullable=False, unique=True),
        sa.Column("score", sa.Float(), comment="异常评分（人工调整行为空）"),
        sa.Column("source", sa.String(20), nullable=False, comment="入清单来源"),
        sa.Column("exec_status", sa.String(10), nullable=False, comment="执行状态"),
        sa.Column("exec_note", sa.String(500), comment="回填执行说明"),
        sa.Column("executed_by", sa.String(36), sa.ForeignKey("users.id"), comment="执行人"),
        sa.Column("executed_at", sa.DateTime(timezone=True), comment="执行时间"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("source IN ('评分Top10%','科目Top5','保底20笔','人工调整')", name="ck_sample_source"),
        sa.CheckConstraint("exec_status IN ('未执行','已执行')", name="ck_sample_exec_status"),
    )


def downgrade() -> None:
    # 逆序删除（外键依赖倒序）
    for table in (
        "voucher_samples", "voucher_lines", "skill_assets", "knowledge_items",
        "ai_runs", "audit_trails",
        "audit_reports", "findings", "confirmations", "worksheet_reviews",
        "worksheet_versions", "worksheets", "rule_relations", "materialities",
        "audit_programs", "risk_items", "import_field_mappings", "materials",
        "stage_gates", "project_members", "audit_projects", "rule_anchors",
        "templates", "clients", "users",
    ):
        op.drop_table(table)
