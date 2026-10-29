<script setup lang="ts">
/**
 * 报告签发（FR-05，PRD §5.5 复核流程与报告生成）——附注 AI 三段式 + 审批流三步。
 * 发起附注（FR-05-03，完成门通过后 plan.confirm≥OPERATE）→ 线索态采信（复用 ai-runs review）→
 * 模板出稿（FR-05-04）→ 质控会签（report.sign≥OPERATE）→ 合伙人签发（APPROVE；
 * 升级未裁决分歧阻签 FR-05-05）。签发后报告门（门 7）在质控驾驶舱串联。
 * 按钮显隐按权限矩阵兜底，权威拦截在后端 403/409/422（AGENTS.md 硬约束 5）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errText } from '@/api/client'
import * as api from '@/api/report'
import * as wsApi from '@/api/worksheet'
import { listProjectOptions } from '@/api/worksheet'
import type { AiDraftOut, ProjectOptionOut, ReportOut, ReportTemplateOut, ReportTrailOut } from '@/api/types'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()

const projects = ref<ProjectOptionOut[]>([])
const projectId = ref('')
const reports = ref<ReportOut[]>([])
const loading = ref(false)

// 发起/出稿：plan.confirm ≥ OPERATE；会签：report.sign ≥ OPERATE；签发：report.sign=APPROVE
const canNotes = computed(() => session.can('plan.confirm', 'OPERATE'))
const canCountersign = computed(() => session.can('report.sign', 'OPERATE'))
const canSign = computed(() => session.can('report.sign', 'APPROVE'))

const STATUS_TAG: Record<string, 'primary' | 'success' | 'warning' | 'danger'> = {
  草稿: 'warning',
  会签: 'primary',
  已签发: 'success',
}

/** 行内所处审批环节：出稿后状态仍为草稿，以出稿产物 file_key 判空区分（§5.5 三步流） */
function stageOf(r: ReportOut): string {
  if (r.status === '已签发') return '已签发'
  if (r.status === '会签') return '待合伙人签发'
  if (!r.notes_draft_key) return '附注待复核（线索态）'
  if (!r.file_key) return '待模板出稿'
  return '待质控会签'
}

async function load() {
  if (!projectId.value) return
  loading.value = true
  try {
    reports.value = await api.listReports(projectId.value)
  } catch (err) {
    ElMessage.error(errText(err))
  } finally {
    loading.value = false
  }
}

watch(projectId, load)

// ---------------------------------------------------------------------------
// 发起附注 AI（FR-05-03）：完成门通过后可发起，产物为线索态（硬约束 3）
// ---------------------------------------------------------------------------
async function doNotesDraft() {
  try {
    await ElMessageBox.confirm(
      '发起附注 AI 生成？完成门须已通过；产物为线索态，人工复核采信前不进报告稿（红线 1/R2）。',
      '发起附注 AI',
    )
  } catch {
    return
  }
  try {
    const out = await api.generateNoteDraft(projectId.value)
    ElMessage.success(`报告 ${out.version} 已建档，附注线索 ${out.run_no} 待人工复核采信`)
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 附注线索复核（红线 1/R2 第三段）：采信复用底稿域 ai-runs review，分派落 notes_draft_key
// ---------------------------------------------------------------------------
const runsVisible = ref(false)
const runsTarget = ref<ReportOut | null>(null)
const runs = ref<AiDraftOut[]>([])

async function openRuns(row: ReportOut) {
  runsTarget.value = row
  runs.value = []
  runsVisible.value = true
  try {
    runs.value = await api.listReportAiRuns(row.id)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function approveNote(run: AiDraftOut) {
  try {
    await wsApi.reviewAiRun(run.run_no, { conclusion: '采信', rejection_reason: null })
    ElMessage.success('附注已采信，进入报告稿（红线 1/R2 第三段收口）')
    runsVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 模板出稿 → 质控会签 → 合伙人签发（FR-05-04 审批流三步；出稿走保真写回+指纹校验）
// ---------------------------------------------------------------------------
const templates = ref<ReportTemplateOut[]>([])
const finalizeVisible = ref(false)
const finalizeTarget = ref<ReportOut | null>(null)
const finalizeForm = ref('')

function openFinalize(row: ReportOut) {
  finalizeTarget.value = row
  finalizeForm.value = ''
  finalizeVisible.value = true
}

async function submitFinalize() {
  if (!finalizeTarget.value) return
  if (!finalizeForm.value) {
    ElMessage.warning('请选择已登记报告模板（FR-05-04 模板出稿）')
    return
  }
  try {
    await api.finalizeReport(finalizeTarget.value.id, finalizeForm.value)
    ElMessage.success('模板保真出稿完成（结构指纹校验通过），可在操作列下载产物，待质控会签')
    finalizeVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

/** 下载出稿产物（模板保真写回的 xlsx） */
async function doDownload(row: ReportOut) {
  try {
    const blob = await api.downloadReportFile(row.id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `audit-report-${row.version}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

const csVisible = ref(false)
const csTarget = ref<ReportOut | null>(null)
const csForm = ref('')

function openCountersign(row: ReportOut) {
  csTarget.value = row
  csForm.value = ''
  csVisible.value = true
}

async function submitCountersign() {
  if (!csTarget.value) return
  if (csForm.value.trim().length < 2) {
    ElMessage.warning('会签意见必填（留痕）')
    return
  }
  try {
    await api.countersignReport(csTarget.value.id, csForm.value.trim())
    ElMessage.success('质控会签完成，待合伙人签发')
    csVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function doSign(row: ReportOut) {
  try {
    await ElMessageBox.confirm(
      `确认签发报告 ${row.version}？须已会签；存在升级未裁决分歧时将被阻签（FR-05-05）。签发为终态。`,
      '合伙人签发',
    )
  } catch {
    return
  }
  try {
    await api.signReport(row.id)
    ElMessage.success('报告已签发（FR-05-04），可在质控驾驶舱通过报告门（门 7）')
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 留痕时间线（所见即所查：附注两步 + 审批三步全程）
// ---------------------------------------------------------------------------
const trailsVisible = ref(false)
const trailTarget = ref<ReportOut | null>(null)
const trails = ref<ReportTrailOut[]>([])

async function openTrails(row: ReportOut) {
  trailTarget.value = row
  trails.value = []
  trailsVisible.value = true
  try {
    trails.value = await api.listReportTrails(row.id)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

onMounted(async () => {
  try {
    projects.value = await listProjectOptions()
  } catch (err) {
    ElMessage.error(errText(err))
  }
  // 已登记报告模板（出稿下拉数据源）；加载失败不阻塞主流程，出稿时后端仍强校验
  try {
    templates.value = await api.listReportTemplates()
  } catch {
    /* 模板清单拉取失败时下拉为空，出稿提交由后端 RP-1007 兜底 */
  }
})
</script>

<template>
  <div class="report-sign">
    <div class="rs-head">
      <el-select
        v-model="projectId"
        placeholder="选择项目"
        style="width: 280px"
      >
        <el-option
          v-for="p in projects"
          :key="p.id"
          :label="`${p.project_no} ${p.name}`"
          :value="p.id"
        />
      </el-select>
      <el-button :loading="loading" :disabled="!projectId" @click="load">刷新</el-button>
      <el-button
        v-if="canNotes"
        type="primary"
        :disabled="!projectId"
        @click="doNotesDraft"
      >
        发起附注 AI
      </el-button>
    </div>

    <el-table v-if="projectId" :data="reports" v-loading="loading">
      <el-table-column prop="version" label="报告版本" width="96" />
      <el-table-column label="审批环节" min-width="180">
        <template #default="{ row }">{{ stageOf(row) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="96">
        <template #default="{ row }">
          <el-tag :type="STATUS_TAG[row.status] ?? 'info'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="template_key" label="模板引用" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">{{ row.template_key ?? '—' }}</template>
      </el-table-column>
      <el-table-column prop="countersigned_at" label="会签时间" width="170">
        <template #default="{ row }">{{ row.countersigned_at ?? '—' }}</template>
      </el-table-column>
      <el-table-column prop="signed_at" label="签发时间" width="170">
        <template #default="{ row }">{{ row.signed_at ?? '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" min-width="200" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="!row.notes_draft_key && row.status === '草稿'"
            link
            type="warning"
            @click="openRuns(row)"
          >
            AI 线索
          </el-button>
          <el-button
            v-if="canNotes && row.status === '草稿' && row.notes_draft_key && !row.file_key"
            link
            type="primary"
            @click="openFinalize(row)"
          >
            模板出稿
          </el-button>
          <el-button
            v-if="row.file_key"
            link
            type="success"
            @click="doDownload(row)"
          >
            下载
          </el-button>
          <el-button
            v-if="canCountersign && row.status === '草稿' && row.file_key"
            link
            type="primary"
            @click="openCountersign(row)"
          >
            质控会签
          </el-button>
          <el-button
            v-if="canSign && row.status === '会签'"
            link
            type="success"
            @click="doSign(row)"
          >
            签发
          </el-button>
          <el-button link type="info" @click="openTrails(row)">留痕</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else description="选择项目后查看报告审批流" :image-size="72" />

    <!-- 附注 AI 线索（FR-05-03 第三段：采信才进报告稿；线索态醒目） -->
    <el-drawer v-model="runsVisible" :title="`附注 AI 线索 · ${runsTarget?.version ?? ''}`" size="520px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="附注产物为线索态：采信前 notes_draft_key 不落库、永不进报告稿（红线 1/R2）"
      />
      <el-table :data="runs" size="small" class="gap-top">
        <el-table-column prop="run_no" label="运行号" width="130" show-overflow-tooltip />
        <el-table-column prop="module" label="模块" width="90" />
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="row.status === '线索' ? 'warning' : 'success'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button v-if="canNotes && row.status === '线索'" link type="primary" @click="approveNote(row)">
              采信
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!runs.length" description="暂无附注线索" :image-size="56" />
    </el-drawer>

    <!-- 模板出稿（FR-05-04 第一步）：模板下拉取自已登记矩阵，出稿走保真写回+结构指纹校验 -->
    <el-dialog v-model="finalizeVisible" :title="`模板出稿 · ${finalizeTarget?.version ?? ''}`" width="480px">
      <el-form label-width="110px">
        <el-form-item label="报告模板" required>
          <el-select v-model="finalizeForm" placeholder="选择已登记报告模板" style="width: 100%">
            <el-option
              v-for="t in templates"
              :key="t.key"
              :label="`${t.label}（${t.version}）`"
              :value="t.key"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="保真说明">
          <el-text size="small" type="info">
            仅填充白名单数据单元格，公式区/下拉框零触碰；写回后结构指纹校验，不一致即回滚（硬约束 8）
          </el-text>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="finalizeVisible = false">取消</el-button>
        <el-button type="primary" @click="submitFinalize">出稿</el-button>
      </template>
    </el-dialog>

    <!-- 质控会签（FR-05-04 第二步，意见必填留痕） -->
    <el-dialog v-model="csVisible" :title="`质控会签 · ${csTarget?.version ?? ''}`" width="480px">
      <el-form label-width="96px">
        <el-form-item label="会签意见" required>
          <el-input v-model="csForm" type="textarea" :rows="3" placeholder="会签意见必填并留痕" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="csVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCountersign">会签</el-button>
      </template>
    </el-dialog>

    <!-- 留痕时间线 -->
    <el-drawer v-model="trailsVisible" :title="`报告留痕 · ${trailTarget?.version ?? ''}`" size="480px">
      <el-timeline v-if="trails.length">
        <el-timeline-item
          v-for="t in trails"
          :key="t.created_at + t.action"
          :timestamp="`${t.created_at} · ${t.operator}`"
          placement="top"
        >
          <b>{{ t.action }}</b>
          <div v-if="t.after_snapshot" class="trail-snap">{{ JSON.stringify(t.after_snapshot) }}</div>
        </el-timeline-item>
      </el-timeline>
      <el-empty v-else description="暂无留痕记录" :image-size="56" />
    </el-drawer>
  </div>
</template>

<style scoped>
.rs-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.gap-top {
  margin-top: 12px;
}
.trail-snap {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  word-break: break-all;
  margin-top: 4px;
}
</style>
