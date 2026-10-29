/** 后端 DTO 类型（对齐 backend/app/schemas；仅前端消费字段，详情以 OpenAPI 为准）。 */

export interface UserOut {
  id: string
  name: string
  login_name: string
  global_role: string // 合伙人/项目经理/审计员/质控专员
}

export interface MeOut {
  user: UserOut
  permissions: Record<string, string> // 权限码 -> 等级（NONE/VIEW/OPERATE/PROPOSE/APPROVE）
}

export interface TokenOut {
  access_token: string
  token_type: string
}

export interface ClientOut {
  id: string
  name: string
  industry?: string | null
  data_level?: string | null
}

/** 权限等级序（前端兜底判断用；权限以后端为准，AGENTS.md 硬约束 5） */
export const LEVEL_ORDER = ['NONE', 'VIEW', 'OPERATE', 'PROPOSE', 'APPROVE'] as const

export interface ProjectOut {
  id: string
  project_no?: string
  name: string
  client_id: string
  business_type: string
  period: string
  status: string
  approval_summary?: Record<string, unknown>
  fee_amount?: number | null
  total_assets?: number | null
  [key: string]: unknown
}

export interface IndustryTemplateOut {
  key: string
  label: string
  materials: string[]
  worksheets: { key: string; title: string }[]
}

export interface TeamMemberOut {
  member_id: string
  user: string
  project_role: string
  budget_hours: number
  actual_hours: number
  deviation_pct: number
}

export interface TeamOverviewOut {
  project_id: string
  members: TeamMemberOut[]
  budget_total: number
  actual_total: number
  deviation_pct: number
  over_threshold: boolean
}

export interface IntakeEvaluation {
  independence_ok: boolean
  client_risk_ok: boolean
  competence_ok: boolean
  engagement_letter_ok: boolean
  notes?: string
}

export interface IntakeMember {
  user_id: string
  project_role: '编制' | '复核' | '查看'
}

export interface IntakeContract {
  total_assets: number
  fee_amount: number
}

export interface IntakeSubmitBody {
  name: string
  client_id: string
  business_type: string
  period: string
  evaluation: IntakeEvaluation
  members: IntakeMember[]
  contract?: IntakeContract
}

// ---------------------------------------------------------------------------
// 底稿域（WS-1xxx）与复核工作台域（RV-1xxx）
// ---------------------------------------------------------------------------

export interface WorksheetOut {
  id: string
  project_id: string
  code: string
  title: string
  status: string // 草稿/待复核/复核中/复核通过/终审通过/已归档
  current_version: string
  file_key?: string | null
  is_important?: boolean | null
  template_id?: string | null
  rule_anchor_id: string
}

export interface AnchorOut {
  id: string
  standard_no: string
  clause: string
  purpose: string
}

export interface TemplateOptionOut {
  id: string
  type: string
  version: string
}

export interface ProjectOptionOut {
  id: string
  project_no: string
  name: string
  status: string
}

export interface AiDraftOut {
  run_no: string
  module: string
  model_version: string
  output_digest?: string | null
  output_key?: string | null
  status: string // 线索/已采信/已退回 等（AI 复核标记结果）
}

export interface WorksheetCreateBody {
  title: string
  template_id: string
  rule_anchor_id: string
  is_important: boolean
}

/** AI 复核标记（红线 1/R2 第三段；不采信必附原因分类 §15-16） */
export interface AiReviewBody {
  conclusion: '采信' | '部分采信' | '不采信'
  rejection_reason?: '事实错误' | '口径错误' | '幻觉' | '其他' | null
}

export interface ReviewBody {
  conclusion: '通过' | '退回'
  opinion?: string | null
  revision_requirement?: string | null
}

export interface CrossRefEntry {
  code: string
  note: string
}

export interface CrossRefsOut {
  worksheet_id: string
  cross_refs: CrossRefEntry[]
  broken_refs: string[] // 断链清单（引用编号在本项目不存在，R7）
  ok: boolean
}

export interface PendingReviewOut {
  worksheet_id: string
  code: string
  title: string
  is_important: boolean
  current_version: string
  status: string // 待复核/复核中
  project_id: string
  prepared_by_name?: string | null
  next_round_no: number
  can_take: boolean // R1 前置标注：编制人=当前用户 → false
}

export interface ReviewRecordOut {
  round_no: number
  reviewer_name?: string | null
  conclusion?: string | null
  opinion?: string | null
  revision_requirement?: string | null
  signature?: Record<string, string> | null // 电子签名=复核人+日期+证书号
  reviewed_date?: string | null
}

export interface ReviewTimelineOut {
  worksheet_id: string
  code: string
  title: string
  status: string
  current_version: string
  records: ReviewRecordOut[]
}

// ---------------------------------------------------------------------------
// 质控域（QU-1xxx）：FR-06 检查 / 阶段门 / 归档 / 检查应对
// ---------------------------------------------------------------------------

export interface GateOut {
  id: string
  stage: string
  gate_status: string // 未开始/进行中/已通过/跳过
  conditions_snapshot?: Record<string, boolean> | null
  passed_at?: string | null
  skip_partner_id?: string | null
  skip_qc_id?: string | null
}

export interface QcCheckOut {
  run_no: string
  status: string
  output_digest?: string | null
  checks: Record<string, boolean>
  failed_items: QcFailedItem[]
  passed: boolean
}

export interface QcFailedItem {
  item: string
  target: string
  detail: string
}

export interface QcRunOut {
  run_no: string
  status: string
  output_digest?: string | null
  output_key?: string | null
  triggered_at?: string | null
  reviewed_by?: string | null
  reviewed_at?: string | null
}

export interface AnchorMapOut {
  worksheet_code: string
  title: string
  status: string
  standard_no: string
  clause: string
  purpose: string
}

export interface InspectionPackOut {
  project_id: string
  project_no: string
  anchor_map: AnchorMapOut[]
  report: Record<string, unknown>
  package_digest: string
  generated_at: string
}

export interface PrecheckOut {
  blocked: boolean
  blockers: Record<string, unknown>[]
}

export interface ArchiveOut {
  project_id: string
  status: string
  stage: string
  archive_package_hash: string
  archive_object_key?: string | null
  archived_worksheets: number
  ai_runs: number
  knowledge_proposals: number
}

/** 归档实包防篡改校验结果（FR-06-04：成员哈希 + 包库自证） */
export interface ArchiveVerifyOut {
  project_id: string
  ok: boolean
  object_key: string
  package_hash: string
  members: number
  zip_sha256: string
  verified_at: string
}

// ---------------------------------------------------------------------------
// 差错归档 v(n+1)（§15-16）：登记→合伙人批准→副本修订→质控复核→重新归档
// ---------------------------------------------------------------------------

/** 差错单（新旧包哈希随单留痕，双版本可查） */
export interface CorrectionOut {
  id: string
  project_id: string
  code: string
  description: string
  affected_worksheet_ids: string[]
  status: string // 待批准/已驳回/修订中/待质控复核/复核通过/已重新归档
  registered_by: string
  approved_by?: string | null
  approved_at?: string | null
  rejection_reason?: string | null
  qc_reviewed_by?: string | null
  qc_reviewed_at?: string | null
  qc_opinion?: string | null
  previous_package_hash?: string | null
  rearchive_package_hash?: string | null
  rearchived_at?: string | null
  created_at: string
}

/** 修订产物上传回执（内容寻址键已落存，凭 file_key 登记版本链） */
export interface CorrectionArtifactOut {
  file_key: string
  size: number
  sha256: string
}

/** 副本修订登记回执（版本链追加 v(n+1)，原稿版本锁定保留） */
export interface CorrectionRevisionOut {
  worksheet_id: string
  version_no: string
  file_key: string
  change_summary?: string | null
}

/** 重新归档 v(n+1) 回执：新旧整包哈希 + supersedes 链 */
export interface RearchiveOut {
  correction_id: string
  correction_code: string
  status: string
  project_id: string
  previous_package_hash: string
  archive_package_hash: string
  archive_object_key: string
  zip_sha256: string
  zip_members: number
  missing_objects: number
}

/** 重要性水平（R8：整体=基准×比例，实际执行=整体 50–75%，明显微小=整体 5%） */
export interface MaterialityOut {
  project_id: string
  benchmark_type: string // 营业收入/费用总额/总资产
  basis_amount: string
  ratio: string
  overall_materiality: string
  execution_ratio: string // 百分比数值，如 60
  trivial_ratio: string
  change_log?: { at: string; by: string; from: Record<string, unknown> }[] | null
  approved_by?: string | null
  approved_at?: string | null
}

// ---------------------------------------------------------------------------
// 知识域（KN-1xxx）：FR-07-01 准则 RAG 问答（硬约束 6：无引用不出答案）
// ---------------------------------------------------------------------------

export interface CitationOut {
  source: string
  ref: string
  score: number
  standard_no?: string | null
  clause?: string | null
  purpose?: string | null
  content?: string | null
}

export interface QaOut {
  run_no: string
  question: string
  answer: string
  citations: CitationOut[]
  covered: boolean // false = 未覆盖明示（不强行作答，硬约束 6）
}

export interface QaHistoryOut {
  question: string
  answer: string
  citations: Record<string, unknown>[]
  covered: boolean
  run_no?: string | null
  operator: string
  asked_at?: string | null
}

// ---------------------------------------------------------------------------
// 函证域（CF-1xxx）：FR-04 台账 / 发出 / 回函自动核对 / 替代程序 / 闭环（八状态 §4.4）
// ---------------------------------------------------------------------------

export interface ConfirmationOut {
  id: string
  project_id: string
  type: '银行' | '往来'
  counterparty: string
  amount: number
  status: string // 台账已生成/已发出/已回函/超期未回/核对一致/差异处理/替代程序/闭环
  issued_at?: string | null
  planned_reply_date?: string | null
  waybill_no?: string | null
  logistics_status?: string | null
  difference?: Record<string, number> | null // 差异三元组：回函金额/台账金额/差异
  alt_test_record?: string | null
  remind_count: number // 超期提醒计数（上限 2 次，§15-7）
}

export interface CfLedgerItemBody {
  type: '银行' | '往来'
  counterparty: string
  amount: number
}

export interface CfTrailOut {
  action: string
  operator: string
  before_snapshot?: Record<string, unknown> | null
  after_snapshot?: Record<string, unknown> | null
  created_at: string
}

// ---------------------------------------------------------------------------
// 报告域（RP-1xxx）：FR-05-03 附注 AI（线索三段式）+ FR-05-04 审批流（出稿→会签→签发）
// ---------------------------------------------------------------------------

export interface ReportOut {
  id: string
  project_id: string
  version: string
  status: string // 草稿（出稿后仍为草稿，以 file_key 判空区分）/会签/已签发
  notes_draft_key?: string | null // 附注采信后落库；空 = 附注待复核（线索）
  template_key?: string | null // 已登记模板键（FR-05-04 出稿）
  file_key?: string | null // 出稿产物对象键（保真写回+指纹校验通过后落键）；空 = 待出稿
  countersigned_by?: string | null
  countersigned_at?: string | null
  signed_by?: string | null
  signed_at?: string | null
}

/** 已登记报告模板（FR-05-04 出稿下拉数据源） */
export interface ReportTemplateOut {
  key: string
  label: string
  version: string
  file: string
}

export interface ReportTrailOut {
  action: string
  operator: string
  before_snapshot?: Record<string, unknown> | null
  after_snapshot?: Record<string, unknown> | null
  created_at: string
}

export interface NoteDraftOut {
  report_id: string
  version: string
  run_no: string
  status: string // 线索
}
