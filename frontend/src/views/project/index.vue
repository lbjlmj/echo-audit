<script setup lang="ts">
/**
 * 项目工作台（PRD §12.2）——本期范围：项目面板 + 立项（FR-01-01/02/06）+
 * 行业模板圈定（FR-02-04）+ 团队工时对比（FR-02-05）+ 执行看板（阶段门进度、
 * 函证台账状态、报告签发状态、底稿进度，均为既有端点客户端聚合，只读）。
 * 权限以后端为准：按钮显隐按 /auth/me 权限矩阵兜底，越权由后端 403 拦截。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errText } from '@/api/client'
import * as api from '@/api/project'
import { listUsers } from '@/api/auth'
import * as confirmApi from '@/api/confirm'
import * as reportApi from '@/api/report'
import * as qualityApi from '@/api/quality'
import * as worksheetApi from '@/api/worksheet'
import { useSessionStore } from '@/stores/session'
import type {
  ClientOut,
  ConfirmationOut,
  GateOut,
  IndustryTemplateOut,
  IntakeMember,
  IntakeSubmitBody,
  MaterialityOut,
  ProjectOut,
  ReportOut,
  TeamOverviewOut,
  UserOut,
  WorksheetOut,
} from '@/api/types'

const session = useSessionStore()

const projects = ref<ProjectOut[]>([])
const clients = ref<ClientOut[]>([])
const users = ref<UserOut[]>([])
const templates = ref<IndustryTemplateOut[]>([])
const selected = ref<ProjectOut | null>(null)
const team = ref<TeamOverviewOut | null>(null)
const loading = ref(false)

// 执行看板数据（只读聚合；未审批项目各域无数据时静默为空）
const gates = ref<GateOut[]>([])
const confirmations = ref<ConfirmationOut[]>([])
const reports = ref<ReportOut[]>([])
const worksheets = ref<WorksheetOut[]>([])
const materiality = ref<MaterialityOut | null>(null)

const canIntake = computed(() => session.can('project.approve', 'PROPOSE'))
const canApprove = computed(() => session.can('project.approve', 'APPROVE'))
const canPlan = computed(() => session.can('plan.confirm', 'OPERATE'))
const canMatView = computed(() => session.can('plan.confirm', 'VIEW'))
const canApproveMat = computed(() => session.can('plan.confirm', 'APPROVE'))

async function refresh() {
  loading.value = true
  try {
    projects.value = await api.listProjects()
    // 已选中项目时联动刷新看板聚合
    if (selected.value) {
      const current = projects.value.find((p) => p.id === selected.value?.id) ?? selected.value
      await pick(current)
    }
  } catch (err) {
    ElMessage.error(errText(err))
  } finally {
    loading.value = false
  }
}

async function pick(row: ProjectOut) {
  selected.value = row
  team.value = null
  gates.value = []
  confirmations.value = []
  reports.value = []
  worksheets.value = []
  materiality.value = null
  // 看板聚合（§12.2）：任一域无数据（如未审批项目）静默降级，不影响其他卡
  const [teamRes, gateRes, cfRes, rpRes, wsRes, matRes] = await Promise.allSettled([
    api.teamOverview(row.id),
    qualityApi.listGates(row.id),
    confirmApi.listConfirmations(row.id),
    reportApi.listReports(row.id),
    worksheetApi.listWorksheets(row.id),
    api.getMateriality(row.id),
  ])
  team.value = teamRes.status === 'fulfilled' ? teamRes.value : null // 无团队数据（如未审批）静默
  if (gateRes.status === 'fulfilled') gates.value = gateRes.value
  if (cfRes.status === 'fulfilled') confirmations.value = cfRes.value
  if (rpRes.status === 'fulfilled') reports.value = rpRes.value
  if (wsRes.status === 'fulfilled') worksheets.value = wsRes.value
  // 重要性水平（R8）：未设置/无权限均静默为空，区块内提示
  if (matRes.status === 'fulfilled') materiality.value = matRes.value
}

// ---------------------------------------------------------------------------
// 执行看板（§12.2 看板要素：阶段门进度条、函证台账状态、报告状态；客户端聚合）
// ---------------------------------------------------------------------------
const gatePassed = computed(() => gates.value.filter((g) => g.gate_status === '已通过').length)

function gateTagType(status: string): 'success' | 'primary' | 'warning' | 'info' {
  if (status === '已通过') return 'success'
  if (status === '进行中') return 'primary'
  if (status === '跳过') return 'warning'
  return 'info'
}

/** 函证台账状态卡：总数/闭环/在途/超期未回（超期提醒 §15-7） */
const cfStats = computed(() => {
  const overdue = confirmations.value.filter((c) => c.status === '超期未回')
  const inFlight = confirmations.value.filter(
    (c) => c.status !== '台账已生成' && c.status !== '闭环' && c.status !== '超期未回',
  ).length
  return {
    total: confirmations.value.length,
    closed: confirmations.value.filter((c) => c.status === '闭环').length,
    inFlight,
    overdue,
  }
})

/** 报告签发卡：最新版本报告状态 + 报告门（门 7）状态 */
const reportCard = computed(() => ({
  latest: reports.value.length ? reports.value[reports.value.length - 1] : null,
  reportGate: gates.value.find((g) => g.stage === '报告') ?? null,
}))

/** 底稿进度卡：复核通过/终审通过计数 + 待复核在途 */
const wsStats = computed(() => ({
  total: worksheets.value.length,
  reviewedPass: worksheets.value.filter((w) => w.status === '复核通过').length,
  finalPass: worksheets.value.filter((w) => ['终审通过', '已归档'].includes(w.status)).length,
  pending: worksheets.value.filter((w) => ['待复核', '复核中'].includes(w.status)).length,
}))

// ---------------------------------------------------------------------------
// 立项申请（FR-01-01/02/06）
// ---------------------------------------------------------------------------
const intakeVisible = ref(false)
const intakeForm = reactive({
  name: '',
  client_id: '',
  business_type: '',
  period: '',
  evaluation: {
    independence_ok: false,
    client_risk_ok: false,
    competence_ok: false,
    engagement_letter_ok: false,
    notes: '',
  },
  members: [] as IntakeMember[],
  use_contract: false,
  total_assets: 0,
  fee_amount: 0,
})

function openIntake() {
  Object.assign(intakeForm, {
    name: '', client_id: '', business_type: '', period: '',
    evaluation: {
      independence_ok: false, client_risk_ok: false,
      competence_ok: false, engagement_letter_ok: false, notes: '',
    },
    members: [], use_contract: false, total_assets: 0, fee_amount: 0,
  })
  intakeVisible.value = true
}

function addMember() {
  intakeForm.members.push({ user_id: '', project_role: '编制' })
}

async function submitIntake() {
  const body: IntakeSubmitBody = {
    name: intakeForm.name,
    client_id: intakeForm.client_id,
    business_type: intakeForm.business_type,
    period: intakeForm.period,
    evaluation: { ...intakeForm.evaluation },
    members: intakeForm.members.filter((m) => m.user_id),
  }
  if (intakeForm.use_contract) {
    body.contract = {
      total_assets: intakeForm.total_assets,
      fee_amount: intakeForm.fee_amount,
    }
  }
  try {
    await api.submitIntake(body)
    ElMessage.success('立项申请已提交，待合伙人审批')
    intakeVisible.value = false
    await refresh()
  } catch (err) {
    // 收费校验不通过（FR-01-06）：差异项提示阻断
    ElMessage.error(errText(err))
  }
}

async function approve(row: ProjectOut) {
  try {
    await ElMessageBox.confirm(
      `确认通过「${row.name}」的立项申请？将通过后初始化八门与拟任团队。`,
      '立项审批',
    )
  } catch {
    return
  }
  try {
    await api.approveProject(row.id)
    ElMessage.success('立项已审批（FR-01-02）')
    await refresh()
    if (selected.value?.id === row.id) await pick(row)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 行业模板圈定（FR-02-04）
// ---------------------------------------------------------------------------
async function selectTemplate(industryTemplate: string) {
  if (!selected.value) return
  try {
    const updated = await api.selectIndustryTemplate(selected.value.id, industryTemplate)
    ElMessage.success('模板已圈定，资料清单与底稿模板范围已同步（FR-01-05/FR-02-04）')
    selected.value = updated
    await refresh()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 重要性水平（R8，FR-02-01）：engine 单点计算；经理可改（留痕+重置报批），合伙人批准
// ---------------------------------------------------------------------------
const matVisible = ref(false)
const matForm = reactive({
  benchmark_type: '营业收入',
  basis_amount: 0,
  exec_ratio_pct: 60,
  trivial_ratio_pct: 5,
})
const MAT_HINT: Record<string, string> = {
  营业收入: '盈利主体 · 默认比例 0.5%',
  费用总额: '成本费用化主体 · 默认比例 1%',
  总资产: '资产基础型主体 · 默认比例 0.5%',
}
const fmtMoney = (v: string | number) =>
  `¥${Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

function openMateriality() {
  if (materiality.value) {
    matForm.benchmark_type = materiality.value.benchmark_type
    matForm.basis_amount = Number(materiality.value.basis_amount)
    matForm.exec_ratio_pct = Number(materiality.value.execution_ratio)
    matForm.trivial_ratio_pct = Number(materiality.value.trivial_ratio)
  } else {
    Object.assign(matForm, { benchmark_type: '营业收入', basis_amount: 0, exec_ratio_pct: 60, trivial_ratio_pct: 5 })
  }
  matVisible.value = true
}

async function submitMateriality() {
  if (!selected.value) return
  if (!matForm.basis_amount || matForm.basis_amount <= 0) {
    ElMessage.warning('请填写正数基准金额')
    return
  }
  try {
    materiality.value = await api.saveMateriality(selected.value.id, { ...matForm })
    ElMessage.warning(
      materiality.value.approved_by
        ? '重要性水平已保存（engine R8 计算）'
        : '重要性水平已保存（engine R8 计算）；修改已重置报批，须合伙人重新确认',
    )
    matVisible.value = false
    await refresh()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function doApproveMateriality() {
  if (!selected.value) return
  try {
    await ElMessageBox.confirm(
      '确认批准该项目的重要性水平？将联动置位计划门 materiality_confirmed（R8/§15-5）。',
      '重要性水平报批',
    )
  } catch {
    return
  }
  try {
    materiality.value = await api.approveMateriality(selected.value.id)
    ElMessage.success('重要性水平已批准（计划门条件已置位）')
    await refresh()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 团队工时（FR-02-05）
// ---------------------------------------------------------------------------
const hoursVisible = ref(false)
const hoursForm = reactive({
  user_id: '',
  budget_hours: 0,
  actual_hours: 0,
})

async function saveHours() {
  if (!selected.value) return
  try {
    team.value = await api.configureTeamHours(selected.value.id, [
      {
        user_id: hoursForm.user_id,
        budget_hours: hoursForm.budget_hours,
        actual_hours: hoursForm.actual_hours,
      },
    ])
    ElMessage.warning(
      team.value.over_threshold ? `偏差 ${team.value.deviation_pct}%，超 10% 阈值告警` : '工时已更新',
    )
    ElMessage.success('工时配置已保存（FR-02-05）')
    hoursVisible.value = false
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

function feeCheckOf(row: ProjectOut): string | null {
  const check = (row.approval_summary as Record<string, unknown> | undefined)?.fee_check as
    | { tier_label?: string; min_fee?: number }
    | undefined
  if (!check) return null
  return `${check.tier_label}：基准不低于 ${check.min_fee} 元`
}

onMounted(async () => {
  await refresh()
  try {
    ;[clients.value, users.value, templates.value] = await Promise.all([
      api.listClients(),
      listUsers(),
      api.listIndustryTemplates(),
    ])
  } catch (err) {
    ElMessage.error(errText(err))
  }
})
</script>

<template>
  <div class="project-view">
    <div class="toolbar">
      <h2>项目工作台</h2>
      <el-space>
        <el-button :loading="loading" @click="refresh">刷新</el-button>
        <el-button v-if="canIntake" type="primary" @click="openIntake">立项申请</el-button>
      </el-space>
    </div>

    <el-table
      :data="projects"
      v-loading="loading"
      highlight-current-row
      @current-change="pick"
    >
      <el-table-column prop="project_no" label="项目编号" width="140" />
      <el-table-column prop="name" label="项目名称" min-width="200" />
      <el-table-column prop="business_type" label="业务类型" width="110" />
      <el-table-column prop="period" label="审计期间" width="110" />
      <el-table-column prop="status" label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.status === '进行中' ? 'success' : 'info'" size="small">
            {{ row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="收费校验" width="200">
        <template #default="{ row }">{{ feeCheckOf(row) ?? '—' }}</template>
      </el-table-column>
      <el-table-column v-if="canApprove" label="操作" width="100">
        <template #default="{ row }">
          <el-button
            v-if="row.status === '待审批'"
            link
            type="primary"
            @click.stop="approve(row)"
          >
            审批
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <template v-if="selected">
      <el-card class="detail-card">
        <template #header>
          {{ selected.name }}（{{ selected.project_no }}）· 立项与计划
        </template>

        <el-form v-if="canPlan" inline label-width="92px">
          <el-form-item label="行业模板">
            <el-select
              :model-value="selected.business_type"
              placeholder="圈定行业模板"
              style="width: 220px"
              @change="selectTemplate"
            >
              <el-option
                v-for="t in templates"
                :key="t.key"
                :label="t.label"
                :value="t.key"
              />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-button @click="hoursVisible = true">配置工时</el-button>
          </el-form-item>
        </el-form>

        <el-divider v-if="canMatView">重要性水平（R8 · 随计划报合伙人审批）</el-divider>
        <template v-if="canMatView">
          <template v-if="materiality">
            <el-descriptions :column="3" border size="small">
              <el-descriptions-item label="基准">
                {{ materiality.benchmark_type }} {{ fmtMoney(materiality.basis_amount) }}
              </el-descriptions-item>
              <el-descriptions-item label="整体重要性">
                {{ fmtMoney(materiality.overall_materiality) }}
              </el-descriptions-item>
              <el-descriptions-item label="实际执行重要性">
                {{ fmtMoney(Number(materiality.overall_materiality) * Number(materiality.execution_ratio) / 100) }}
                （{{ materiality.execution_ratio }}%）
              </el-descriptions-item>
              <el-descriptions-item label="明显微小错报">
                {{ fmtMoney(Number(materiality.overall_materiality) * Number(materiality.trivial_ratio) / 100) }}
                （{{ materiality.trivial_ratio }}%）
              </el-descriptions-item>
              <el-descriptions-item label="报批状态" :span="2">
                <el-tag v-if="materiality.approved_by" type="success" size="small">
                  已批准 {{ materiality.approved_at?.slice(0, 10) }}
                </el-tag>
                <el-tag v-else type="warning" size="small">待合伙人批准</el-tag>
              </el-descriptions-item>
            </el-descriptions>
            <el-space class="gap-top">
              <el-button v-if="canPlan" plain size="small" @click="openMateriality">修改</el-button>
              <el-button
                v-if="canApproveMat && !materiality.approved_by"
                type="primary"
                size="small"
                @click="doApproveMateriality"
              >
                批准（计划门联动）
              </el-button>
            </el-space>
          </template>
          <el-space v-else>
            <span class="muted">尚未设置重要性水平（底稿写回将带「未经批准」占位）</span>
            <el-button v-if="canPlan" type="primary" plain size="small" @click="openMateriality">
              设置重要性水平
            </el-button>
          </el-space>
        </template>

        <el-alert
          v-if="team?.over_threshold"
          type="warning"
          :closable="false"
          show-icon
          class="gap-top"
          :title="`工时偏差 ${team.deviation_pct}%，超过 10% 阈值（FR-02-05 面板告警）`"
        />
        <el-table v-if="team?.members?.length" :data="team.members" size="small">
          <el-table-column prop="user" label="成员" width="120" />
          <el-table-column prop="project_role" label="项目角色" width="100" />
          <el-table-column prop="budget_hours" label="预算工时" width="100" />
          <el-table-column prop="actual_hours" label="实际工时" width="100" />
          <el-table-column prop="deviation_pct" label="偏差 %">
            <template #default="{ row }">
              <span :class="{ over: row.deviation_pct > 10 }">{{ row.deviation_pct }}%</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-else
          description="暂无团队工时数据（立项审批后由项目经理配置）"
          :image-size="48"
        />
      </el-card>

      <!-- 执行看板（§12.2：阶段门进度条 + 函证台账状态 + 报告状态 + 底稿进度，只读聚合） -->
      <el-card class="detail-card">
        <template #header>{{ selected.name }} · 执行看板</template>

        <div class="kanban-title">
          阶段门进度
          <el-tag size="small" type="info" style="margin-left: 8px">
            {{ gatePassed }}/{{ gates.length || 8 }} 已通过
          </el-tag>
        </div>
        <div class="gate-row">
          <el-tag
            v-for="(g, i) in gates"
            :key="g.id"
            :type="gateTagType(g.gate_status)"
            effect="plain"
          >
            门{{ i + 1 }} {{ g.stage }} · {{ g.gate_status }}
          </el-tag>
          <span v-if="!gates.length" class="muted">立项审批后初始化八门</span>
        </div>

        <el-row :gutter="16" class="stat-cards">
          <el-col :span="8">
            <div class="stat-card">
              <div class="kanban-title">函证台账状态</div>
              <div class="stat-line">
                共 <b>{{ cfStats.total }}</b> 张 · 闭环
                <b class="ok">{{ cfStats.closed }}</b>
                <template v-if="cfStats.inFlight"> · 在途 {{ cfStats.inFlight }}</template>
              </div>
              <el-alert
                v-if="cfStats.overdue.length"
                type="warning"
                :closable="false"
                show-icon
                :title="`超期未回 ${cfStats.overdue.length} 张：${cfStats.overdue.map((c) => c.counterparty).join('、')}`"
              />
              <div v-else-if="cfStats.total" class="muted">无超期未回（全闭环为门 5 准入条件）</div>
              <div v-else class="muted">暂无函证台账</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-card">
              <div class="kanban-title">报告签发</div>
              <template v-if="reportCard.latest">
                <div class="stat-line">
                  v{{ reportCard.latest.version }}
                  <el-tag
                    size="small"
                    :type="reportCard.latest.status === '已签发' ? 'success' : 'warning'"
                  >
                    {{ reportCard.latest.status }}
                  </el-tag>
                </div>
                <div class="muted">
                  报告门（门 7）：{{ reportCard.reportGate?.gate_status ?? '—' }}
                  <template v-if="reportCard.latest.signed_at">
                    · 签发于 {{ reportCard.latest.signed_at.slice(0, 10) }}
                  </template>
                </div>
              </template>
              <div v-else class="muted">尚未出稿（完成门通过后发起附注 AI 与出稿）</div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-card">
              <div class="kanban-title">底稿进度</div>
              <div class="stat-line">
                共 <b>{{ wsStats.total }}</b> 张 · 复核通过
                <b class="ok">{{ wsStats.reviewedPass }}</b> · 终审通过
                <b class="ok">{{ wsStats.finalPass }}</b>
              </div>
              <div v-if="wsStats.pending" class="muted">待复核/复核中 {{ wsStats.pending }} 张</div>
              <div v-else-if="wsStats.total" class="muted">无待复核底稿</div>
              <div v-else class="muted">暂无底稿</div>
            </div>
          </el-col>
        </el-row>
      </el-card>
    </template>

    <!-- 立项申请对话框（FR-01-01：承接评价四项 + 拟任团队 + 合同收费 FR-01-06） -->
    <el-dialog v-model="intakeVisible" title="立项申请（业务承接评价）" width="640px">
      <el-form label-width="108px">
        <el-form-item label="项目名称" required>
          <el-input v-model="intakeForm.name" placeholder="如：鸿瑞怡园 2024 年报审计" />
        </el-form-item>
        <el-form-item label="委托方" required>
          <el-select v-model="intakeForm.client_id" placeholder="选择客户" style="width: 100%">
            <el-option
              v-for="c in clients"
              :key="c.id"
              :label="`${c.name}（${c.data_level ?? '—'}）`"
              :value="c.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="业务类型" required>
          <el-select v-model="intakeForm.business_type" placeholder="选择行业" style="width: 100%">
            <el-option
              v-for="t in templates"
              :key="t.key"
              :label="t.label"
              :value="t.key"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="审计期间" required>
          <el-input v-model="intakeForm.period" placeholder="如：2024 年度" />
        </el-form-item>

        <el-divider>承接评价（任一不通过即阻断）</el-divider>
        <el-form-item label="独立性">
          <el-switch v-model="intakeForm.evaluation.independence_ok" />
        </el-form-item>
        <el-form-item label="客户风险">
          <el-switch v-model="intakeForm.evaluation.client_risk_ok" />
        </el-form-item>
        <el-form-item label="胜任力">
          <el-switch v-model="intakeForm.evaluation.competence_ok" />
        </el-form-item>
        <el-form-item label="业务约定书">
          <el-switch v-model="intakeForm.evaluation.engagement_letter_ok" />
        </el-form-item>
        <el-form-item label="评价备注">
          <el-input v-model="intakeForm.evaluation.notes" type="textarea" :rows="2" />
        </el-form-item>

        <el-divider>拟任团队（一人一项目单角色）</el-divider>
        <el-form-item v-for="(m, i) in intakeForm.members" :key="i" :label="`成员 ${i + 1}`">
          <el-select v-model="m.user_id" placeholder="选择人员" style="width: 240px">
            <el-option
              v-for="u in users"
              :key="u.id"
              :label="`${u.name}（${u.global_role}）`"
              :value="u.id"
            />
          </el-select>
          <el-select v-model="m.project_role" style="width: 110px; margin-left: 8px">
            <el-option label="编制" value="编制" />
            <el-option label="复核" value="复核" />
            <el-option label="查看" value="查看" />
          </el-select>
          <el-button link type="danger" style="margin-left: 8px" @click="intakeForm.members.splice(i, 1)">
            移除
          </el-button>
        </el-form-item>
        <el-form-item>
          <el-button link type="primary" @click="addMember">+ 添加成员</el-button>
        </el-form-item>

        <el-divider>合同与收费（FR-01-06：低于档位基准将阻断立项）</el-divider>
        <el-form-item label="录入合同">
          <el-switch v-model="intakeForm.use_contract" />
        </el-form-item>
        <template v-if="intakeForm.use_contract">
          <el-form-item label="资产总额(元)">
            <el-input-number v-model="intakeForm.total_assets" :min="0" :controls="false" style="width: 200px" />
          </el-form-item>
          <el-form-item label="合同收费(元)">
            <el-input-number v-model="intakeForm.fee_amount" :min="0" :controls="false" style="width: 200px" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="intakeVisible = false">取消</el-button>
        <el-button type="primary" @click="submitIntake">提交申请</el-button>
      </template>
    </el-dialog>

    <!-- 工时配置对话框（FR-02-05 upsert） -->
    <el-dialog v-model="hoursVisible" title="成员工时配置" width="480px">
      <el-form label-width="96px">
        <el-form-item label="成员" required>
          <el-select v-model="hoursForm.user_id" style="width: 100%">
            <el-option
              v-for="u in users"
              :key="u.id"
              :label="`${u.name}（${u.global_role}）`"
              :value="u.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="预算工时">
          <el-input-number v-model="hoursForm.budget_hours" :min="0" :controls="false" />
        </el-form-item>
        <el-form-item label="实际工时">
          <el-input-number v-model="hoursForm.actual_hours" :min="0" :controls="false" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="hoursVisible = false">取消</el-button>
        <el-button type="primary" @click="saveHours">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重要性水平设置（R8，FR-02-01：engine 单点计算，经理可改、合伙人批准） -->
    <el-dialog v-model="matVisible" title="重要性水平（R8）" width="520px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="整体重要性 = 基准 × 比例（engine 单点计算）；修改将全量留痕并重置合伙人报批"
      />
      <el-form label-width="120px" class="gap-top">
        <el-form-item label="基准类型" required>
          <el-select v-model="matForm.benchmark_type" style="width: 100%">
            <el-option v-for="(hint, k) in MAT_HINT" :key="k" :label="`${k}（${hint}）`" :value="k" />
          </el-select>
        </el-form-item>
        <el-form-item label="基准金额" required>
          <el-input-number
            v-model="matForm.basis_amount"
            :min="0.01"
            :controls="false"
            :precision="2"
            placeholder="如 100000000"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item label="实际执行比例%">
          <el-input-number v-model="matForm.exec_ratio_pct" :min="50" :max="75" :controls="false" style="width: 100%" />
          <span class="hint-text">默认 60%，区间 50%–75%（越界由 engine 拒绝）</span>
        </el-form-item>
        <el-form-item label="明显微小比例%">
          <el-input-number v-model="matForm.trivial_ratio_pct" :min="0.1" :max="9.9" :controls="false" style="width: 100%" />
          <span class="hint-text">默认 5%</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="matVisible = false">取消</el-button>
        <el-button type="primary" @click="submitMateriality">保存（engine 计算）</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.toolbar h2 {
  margin: 0;
}
.detail-card {
  margin-top: 16px;
}
.kanban-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 8px;
}
.gate-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}
.stat-cards {
  margin-top: 4px;
}
.stat-card {
  height: 100%;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}
.stat-line {
  font-size: 14px;
  margin-bottom: 6px;
}
.ok {
  color: var(--el-color-success);
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.over {
  color: var(--el-color-warning);
  font-weight: 600;
}
.gap-top {
  margin-top: 12px;
}
.hint-text {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 8px;
}
</style>
