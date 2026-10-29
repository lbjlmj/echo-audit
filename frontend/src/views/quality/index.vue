<script setup lang="ts">
/**
 * 质控驾驶舱（PRD §12.5）——健康度 / 卡控告警 / AI 留痕抽查。
 * 本期范围：八门状态清单（FR-06-03：手动过门仅 实质性/完成/报告，其余业务域自动串联；
 * 跳过须合伙人+质控双批 R3）+ 质控 AI 检查（FR-06-01 线索态 + 留痕清单）+
 * 检查应对材料包（FR-06-05 锚点反查）+ 归档前校验与一键归档（FR-06-02/04）。
 * 权限以后端为准：archive.access/project.approve 等由后端 403/422 拦截。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadFile, UploadUserFile } from 'element-plus'
import { errText } from '@/api/client'
import * as api from '@/api/quality'
import { listWorksheets } from '@/api/worksheet'
import { listUsers } from '@/api/auth'
import { useSessionStore } from '@/stores/session'
import type {
  ArchiveOut,
  ArchiveVerifyOut,
  CorrectionOut,
  GateOut,
  InspectionPackOut,
  PrecheckOut,
  ProjectOptionOut,
  QcCheckOut,
  QcRunOut,
  RearchiveOut,
  UserOut,
  WorksheetOut,
} from '@/api/types'

const session = useSessionStore()

const STAGES = ['承接', '计划', '风险评估', '内控', '实质性', '完成', '报告', '归档'] as const

// 手动过门仅三段（engine GATE_READY_STAGES）：前端按矩阵兜底显隐，权威拦截在后端
const GATE_PERMS: Record<string, { perm: string; level: 'OPERATE' | 'APPROVE' }> = {
  实质性: { perm: 'plan.confirm', level: 'OPERATE' },
  完成: { perm: 'archive.access', level: 'OPERATE' },
  报告: { perm: 'report.sign', level: 'APPROVE' },
}

const projects = ref<ProjectOptionOut[]>([])
const projectId = ref('')
const gates = ref<GateOut[]>([])
const qcRuns = ref<QcRunOut[]>([])
const qcResult = ref<QcCheckOut | null>(null)
const pack = ref<InspectionPackOut | null>(null)
const precheck = ref<PrecheckOut | null>(null)
const archiveResult = ref<ArchiveOut | null>(null)
const verifyResult = ref<ArchiveVerifyOut | null>(null)
const users = ref<UserOut[]>([])
const loading = ref(false)

// 差错归档 v(n+1)（§15-16）
const corrections = ref<CorrectionOut[]>([])
const worksheets = ref<WorksheetOut[]>([])
const rearchiveResult = ref<RearchiveOut | null>(null)

const canQc = computed(() => session.can('archive.access', 'OPERATE'))
const canSkip = computed(() => session.can('project.approve', 'APPROVE'))
const qcUsers = computed(() => users.value.filter((u) => u.global_role === '质控专员'))
/** 项目已归档（归档门已通过或本次会话归档成功）→ 实包下载/防篡改校验入口 */
const archived = computed(
  () =>
    archiveResult.value?.status === '已归档' ||
    gates.value.some((g) => g.stage === '归档' && g.gate_status === '已通过'),
)

const GATE_TAG: Record<string, 'info' | 'warning' | 'success' | 'danger'> = {
  未开始: 'info',
  进行中: 'warning',
  已通过: 'success',
  跳过: 'danger',
}

const orderedGates = computed(() =>
  [...gates.value].sort((a, b) => STAGES.indexOf(a.stage as never) - STAGES.indexOf(b.stage as never)),
)

function canPass(g: GateOut): boolean {
  const rule = GATE_PERMS[g.stage]
  if (!rule) return false // 非手动过门段（承接/计划/风险评估/内控/归档）
  return session.can(rule.perm, rule.level)
}

async function loadProject() {
  if (!projectId.value) return
  loading.value = true
  qcResult.value = null
  pack.value = null
  precheck.value = null
  archiveResult.value = null
  verifyResult.value = null
  rearchiveResult.value = null
  try {
    ;[gates.value, qcRuns.value, corrections.value] = await Promise.all([
      api.listGates(projectId.value),
      api.listQcChecks(projectId.value),
      api.listCorrections(projectId.value),
    ])
  } catch (err) {
    ElMessage.error(errText(err))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------------------
// 阶段门：通过（engine 准入裁决，422 时展示缺条件清单）/ 跳过（R3 双批）
// ---------------------------------------------------------------------------
async function doPass(g: GateOut) {
  try {
    await ElMessageBox.confirm(`确认通过阶段门「${g.stage}」？准入条件由 engine 裁决并落快照。`, '阶段门通过')
  } catch {
    return
  }
  try {
    await api.passGate(g.stage, projectId.value)
    ElMessage.success(`阶段门「${g.stage}」已通过`)
    await loadProject()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

const skipVisible = ref(false)
const skipTarget = ref<GateOut | null>(null)
const skipQcId = ref('')

function openSkip(g: GateOut) {
  skipTarget.value = g
  skipQcId.value = ''
  skipVisible.value = true
}

async function submitSkip() {
  if (!skipTarget.value) return
  if (!skipQcId.value) {
    ElMessage.warning('跳过须选择质控专员会批（R3 双批）')
    return
  }
  try {
    await api.skipGate(skipTarget.value.stage, projectId.value, skipQcId.value)
    ElMessage.success(`阶段门「${skipTarget.value.stage}」已跳过（合伙人+质控双批已落库）`)
    skipVisible.value = false
    await loadProject()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 质控 AI 检查（FR-06-01）：线索态产物；第三段采信走底稿域 AI 复核标记
// ---------------------------------------------------------------------------
async function doQcCheck() {
  try {
    qcResult.value = await api.runQcCheck(projectId.value)
    ElMessage.warning(
      qcResult.value.passed
        ? `检查通过（run ${qcResult.value.run_no}），产物为线索态，须人工复核标记后方可作为门 6 依据`
        : `检查存在 ${qcResult.value.failed_items.length} 项未通过（线索态）`,
    )
    qcRuns.value = await api.listQcChecks(projectId.value)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 检查应对材料包（FR-06-05）与归档（FR-06-02/04）
// ---------------------------------------------------------------------------
async function doPack() {
  try {
    pack.value = await api.buildInspectionPack(projectId.value)
    ElMessage.success(`材料包已生成（digest ${pack.value.package_digest.slice(0, 12)}…）`)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function doPrecheck() {
  try {
    precheck.value = await api.precheckArchive(projectId.value)
    ElMessage.warning(precheck.value.blocked ? `归档被阻断：${precheck.value.blockers.length} 项待整改` : '归档前校验通过')
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function doArchive() {
  try {
    await ElMessageBox.confirm(
      '确认一键归档？将通过完整性校验→封包哈希→项目与底稿双状态机收口「已归档」，归档后不可逆。',
      '一键归档',
    )
  } catch {
    return
  }
  try {
    archiveResult.value = await api.archiveProject(projectId.value)
    ElMessage.success('归档完成（FR-06-04），实包已封存（键含封包哈希），经验沉淀提案已自动生成')
    await loadProject()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

/** 下载归档实包（zip：底稿/报告产物+AI留痕+manifest+SHA256SUMS） */
async function doDownloadPackage() {
  try {
    const blob = await api.downloadArchivePackage(projectId.value)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `archive-${projectId.value}.zip`
    a.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

/** 归档包防篡改校验（成员哈希逐项比对+包库自证，QU-1009 失败 422） */
async function doVerifyPackage() {
  try {
    verifyResult.value = await api.verifyArchivePackage(projectId.value)
    ElMessage.success(
      `归档包完整性校验通过：${verifyResult.value.members} 个成员，包内封包哈希与库内登记一致`,
    )
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 差错归档 v(n+1)（§15-16）：登记→合伙人批准→副本修订→质控复核→重新归档
// 副本修订 = 版本链追加 v(n+1)，原稿版本与 v(n) 归档包 WORM 锁定保留
// ---------------------------------------------------------------------------
const CORRECTION_TAG: Record<string, 'info' | 'warning' | 'success' | 'danger'> = {
  待批准: 'warning',
  修订中: 'warning',
  待质控复核: 'warning',
  复核通过: 'success',
  已重新归档: 'success',
  已驳回: 'danger',
}

/** 登记=质控专员（archive.access≥OPERATE + 显式角色）；批准=合伙人（project.approve=APPROVE） */
const canRegisterCorrection = computed(
  () => canQc.value && session.globalRole === '质控专员',
)
const canApproveCorrection = computed(() => session.can('project.approve', 'APPROVE'))
const canReviseCorrection = computed(() => session.can('worksheet.edit', 'OPERATE'))
const canQcReviewCorrection = computed(
  () => session.can('worksheet.review_sign', 'OPERATE') && session.globalRole === '质控专员',
)

function userName(id?: string | null): string {
  if (!id) return '—'
  return users.value.find((u) => u.id === id)?.name ?? id.slice(0, 8)
}

function wsLabel(id: string): string {
  const w = worksheets.value.find((x) => x.id === id)
  return w ? `${w.code} ${w.title}` : id.slice(0, 8)
}

async function ensureWorksheets() {
  if (worksheets.value.length || !projectId.value) return
  worksheets.value = await listWorksheets(projectId.value)
}

// ---- 登记（质控专员，项目须已归档；在途单唯一 409 QU-1010） ----
const registerVisible = ref(false)
const regDescription = ref('')
const regWorksheetIds = ref<string[]>([])

async function openRegister() {
  try {
    await ensureWorksheets()
  } catch (err) {
    ElMessage.error(errText(err))
    return
  }
  regDescription.value = ''
  regWorksheetIds.value = []
  registerVisible.value = true
}

async function submitRegister() {
  if (!regDescription.value.trim()) {
    ElMessage.warning('请填写差错描述')
    return
  }
  if (!regWorksheetIds.value.length) {
    ElMessage.warning('请选择受影响底稿（至少一张）')
    return
  }
  try {
    const row = await api.registerCorrection(
      projectId.value,
      regDescription.value.trim(),
      regWorksheetIds.value,
    )
    ElMessage.success(`差错单 ${row.code} 已登记，待合伙人批准`)
    registerVisible.value = false
    await loadProject()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---- 批准/驳回（合伙人；驳回必附原因） ----
const approveVisible = ref(false)
const approveTarget = ref<CorrectionOut | null>(null)
const approveChoice = ref<'approve' | 'reject'>('approve')
const approveReason = ref('')

function openApprove(row: CorrectionOut) {
  approveTarget.value = row
  approveChoice.value = 'approve'
  approveReason.value = ''
  approveVisible.value = true
}

async function submitApprove() {
  if (!approveTarget.value) return
  const approve = approveChoice.value === 'approve'
  if (!approve && !approveReason.value.trim()) {
    ElMessage.warning('驳回必附原因（§15-16）')
    return
  }
  try {
    const row = await api.approveCorrection(
      approveTarget.value.id,
      approve,
      approve ? undefined : approveReason.value.trim(),
    )
    ElMessage.success(
      approve
        ? `已批准 ${row.code}：副本修订解锁（批准时点版本基线已落快照）`
        : `已驳回 ${row.code}`,
    )
    approveVisible.value = false
    await loadProject()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---- 修订登记（经理/审计员：受控上传产物 → 版本链追加 v(n+1)） ----
const reviseVisible = ref(false)
const reviseTarget = ref<CorrectionOut | null>(null)
const reviseWorksheetId = ref('')
const reviseFileList = ref<UploadUserFile[]>([])
const reviseSummary = ref('')

function onReviseChange(_file: UploadFile, files: UploadUserFile[]) {
  reviseFileList.value = files.slice(-1) // 仅保留最新一份修订稿
}

async function openRevise(row: CorrectionOut) {
  try {
    await ensureWorksheets()
  } catch (err) {
    ElMessage.error(errText(err))
    return
  }
  reviseTarget.value = row
  reviseWorksheetId.value = row.affected_worksheet_ids[0] ?? ''
  reviseFileList.value = []
  reviseSummary.value = ''
  reviseVisible.value = true
}

async function submitRevise() {
  if (!reviseTarget.value) return
  const file = reviseFileList.value[0]?.raw
  if (!reviseWorksheetId.value || !file || !reviseSummary.value.trim()) {
    ElMessage.warning('请完整选择底稿、修订稿（.xlsx）并填写修订说明')
    return
  }
  if (!file.name.toLowerCase().endsWith('.xlsx')) {
    ElMessage.warning('修订产物须为模板保真写回 xlsx')
    return
  }
  try {
    // 受控上传（内容寻址键落存）→ 凭 file_key 登记版本链，两步原子收口
    const art = await api.uploadCorrectionArtifact(
      reviseTarget.value.id,
      reviseWorksheetId.value,
      file.name,
      await file.arrayBuffer(),
    )
    const out = await api.registerCorrectionRevision(
      reviseTarget.value.id,
      reviseWorksheetId.value,
      art.file_key,
      reviseSummary.value.trim(),
    )
    ElMessage.success(`已登记修订：${wsLabel(out.worksheet_id)} 追加 ${out.version_no}（原稿版本锁定保留）`)
    reviseVisible.value = false
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---- 提交质控复核（逐稿校验已超批准时点版本基线） ----
async function doSubmitReview(row: CorrectionOut) {
  try {
    await ElMessageBox.confirm(
      `确认提交 ${row.code} 进入质控复核？将逐稿校验受影响底稿均已追加修订版本。`,
      '提交质控复核',
    )
  } catch {
    return
  }
  try {
    await api.submitCorrectionReview(row.id)
    ElMessage.success(`${row.code} 已提交，待质控复核`)
    await loadProject()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---- 质控复核（质控专员；退回必附意见） ----
const qcVisible = ref(false)
const qcTarget = ref<CorrectionOut | null>(null)
const qcConclusion = ref<'通过' | '退回'>('通过')
const qcOpinion = ref('')

function openQcReview(row: CorrectionOut) {
  qcTarget.value = row
  qcConclusion.value = '通过'
  qcOpinion.value = ''
  qcVisible.value = true
}

async function submitQcReview() {
  if (!qcTarget.value) return
  if (qcConclusion.value === '退回' && !qcOpinion.value.trim()) {
    ElMessage.warning('退回必附意见')
    return
  }
  try {
    const row = await api.qcReviewCorrection(
      qcTarget.value.id,
      qcConclusion.value,
      qcOpinion.value.trim() || undefined,
    )
    ElMessage.success(
      qcConclusion.value === '通过'
        ? `${row.code} 复核通过，可执行重新归档`
        : `${row.code} 已退回修订中`,
    )
    qcVisible.value = false
    await loadProject()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---- 重新归档 v(n+1)（新整包 supersedes 旧包哈希，旧包 WORM 不动） ----
async function doRearchive(row: CorrectionOut) {
  try {
    await ElMessageBox.confirm(
      `确认重新归档生成 v(n+1) 归档包？新包 manifest 将登记 supersedes 旧包哈希，旧包与底稿原版本 WORM 锁定保留，双哈希随差错单留痕。`,
      '重新归档 v(n+1)',
    )
  } catch {
    return
  }
  try {
    rearchiveResult.value = await api.rearchiveProject(row.id)
    ElMessage.success(`重新归档完成：${row.code} 已收口「已重新归档」，项目封包哈希已指向 v(n+1)`)
    await loadProject()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

onMounted(async () => {
  try {
    ;[projects.value, users.value] = await Promise.all([api.listArchiveProjects(), listUsers()])
  } catch (err) {
    ElMessage.error(errText(err))
  }
})
</script>

<template>
  <div class="quality-view">
    <div class="toolbar">
      <h2>质控驾驶舱</h2>
      <el-space>
        <el-select
          v-model="projectId"
          placeholder="选择项目"
          style="width: 280px"
          @change="loadProject"
        >
          <el-option
            v-for="p in projects"
            :key="p.id"
            :label="`${p.project_no} ${p.name}`"
            :value="p.id"
          />
        </el-select>
        <el-button :loading="loading" :disabled="!projectId" @click="loadProject">刷新</el-button>
      </el-space>
    </div>

    <template v-if="projectId">
      <!-- 八门状态（FR-06-03 验收口径：门状态与跳过记录全程可见） -->
      <el-card class="block-card" header="八阶段门（实质性/完成/报告支持手动过门；跳过须双批）">
        <el-table :data="orderedGates" size="small" v-loading="loading">
          <el-table-column prop="stage" label="阶段门" width="100" />
          <el-table-column label="门状态" width="96">
            <template #default="{ row }">
              <el-tag :type="GATE_TAG[row.gate_status] ?? 'info'" size="small">{{ row.gate_status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="准入条件快照" min-width="260">
            <template #default="{ row }">
              <span v-if="row.conditions_snapshot" class="cond">
                <template v-for="(v, k) in row.conditions_snapshot" :key="k">
                  <el-tag :type="v ? 'success' : 'danger'" size="small" effect="plain" class="cond-tag">
                    {{ v ? '✓' : '✗' }} {{ k }}
                  </el-tag>
                </template>
              </span>
              <span v-else class="cond">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="passed_at" label="通过时间" width="170">
            <template #default="{ row }">{{ row.passed_at ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="操作" width="150">
            <template #default="{ row }">
              <el-button
                v-if="canPass(row) && (row.gate_status === '未开始' || row.gate_status === '进行中')"
                link
                type="primary"
                @click="doPass(row)"
              >
                通过
              </el-button>
              <el-button
                v-if="canSkip && (row.gate_status === '未开始' || row.gate_status === '进行中')"
                link
                type="danger"
                @click="openSkip(row)"
              >
                跳过
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <div class="hint">承接/计划/风险评估/内控/归档门由对应业务域审批流自动串联（FR-06-03）；手动过门条件不满足时后端 422 返回缺口清单。</div>
      </el-card>

      <!-- 质控 AI 检查（FR-06-01） -->
      <el-card class="block-card" header="质控 AI 检查（底稿完整性/复核点清单/交叉索引/程序缺口；产物为线索态）">
        <el-button v-if="canQc" type="primary" :loading="loading" @click="doQcCheck">发起检查</el-button>
        <template v-if="qcResult">
          <el-alert
            :type="qcResult.passed ? 'success' : 'error'"
            :closable="false"
            show-icon
            class="gap-top"
            :title="qcResult.passed ? '全部检查项通过' : `未通过 ${qcResult.failed_items.length} 项`"
            :description="`run ${qcResult.run_no} · 线索态：采信后（AI 复核标记）方可作为门 6 准入依据（红线 1/R2）`"
          />
          <el-space wrap class="gap-top">
            <el-tag
              v-for="(v, k) in qcResult.checks"
              :key="k"
              :type="v ? 'success' : 'danger'"
              effect="plain"
            >
              {{ v ? '✓' : '✗' }} {{ k }}
            </el-tag>
          </el-space>
          <el-table v-if="qcResult.failed_items.length" :data="qcResult.failed_items" size="small" class="gap-top">
            <el-table-column prop="item" label="检查项" width="140" />
            <el-table-column prop="target" label="对象" width="200" />
            <el-table-column prop="detail" label="说明" min-width="280" />
          </el-table>
        </template>
        <el-divider>检查留痕清单（线索态透明，第三段复核标记经 /worksheets/ai-runs）</el-divider>
        <el-table :data="qcRuns" size="small">
          <el-table-column prop="run_no" label="运行号" min-width="180" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="96">
            <template #default="{ row }">
              <el-tag :type="row.status === '线索' ? 'warning' : 'success'" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="triggered_at" label="发起时间" width="170" />
          <el-table-column label="复核标记" width="170">
            <template #default="{ row }">{{ row.reviewed_by ? `已复核 ${row.reviewed_at ?? ''}` : '未复核' }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!qcRuns.length" description="暂无质控检查记录" :image-size="48" />
      </el-card>

      <!-- 检查应对材料包（FR-06-05） -->
      <el-card class="block-card" header="检查应对材料包（锚点反查「这条程序依据哪条准则」+ 质控报表）">
        <el-button v-if="canQc" type="primary" plain @click="doPack">一键出包</el-button>
        <template v-if="pack">
          <div class="hint gap-top">
            项目 {{ pack.project_no }} · 封包摘要 {{ pack.package_digest }} · 生成于 {{ pack.generated_at }}（同批可复算）
          </div>
          <el-table :data="pack.anchor_map" size="small" class="gap-top">
            <el-table-column prop="worksheet_code" label="底稿编号" width="170" />
            <el-table-column prop="title" label="底稿标题" min-width="160" />
            <el-table-column prop="status" label="状态" width="96" />
            <el-table-column label="准则依据（红线 2 库端反查）" min-width="280">
              <template #default="{ row }">
                {{ row.standard_no }} {{ row.clause }} · {{ row.purpose }}
              </template>
            </el-table-column>
          </el-table>
          <el-collapse class="gap-top">
            <el-collapse-item title="质控报表（原始数据）">
              <pre class="report-json">{{ JSON.stringify(pack.report, null, 2) }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </el-card>

      <!-- 归档（FR-06-02/04） -->
      <el-card class="block-card" header="归档（校验→封包哈希→实包封存→项目+底稿双状态机收口）">
        <el-space>
          <el-button plain @click="doPrecheck">归档前校验</el-button>
          <el-button v-if="canQc" type="danger" @click="doArchive">一键归档</el-button>
          <template v-if="archived">
            <el-button type="primary" plain @click="doDownloadPackage">下载归档包</el-button>
            <el-button plain @click="doVerifyPackage">完整性校验</el-button>
          </template>
        </el-space>
        <el-alert
          v-if="precheck?.blocked"
          type="error"
          :closable="false"
          show-icon
          class="gap-top"
          title="归档被阻断（FR-06-02）"
        >
          <div v-for="(b, i) in precheck.blockers" :key="i" class="blocker">
            {{ JSON.stringify(b) }}
          </div>
        </el-alert>
        <el-alert
          v-else-if="precheck && !precheck.blocked"
          type="success"
          :closable="false"
          show-icon
          class="gap-top"
          title="归档前校验通过，可执行一键归档"
        />
        <el-descriptions v-if="archiveResult" :column="2" border size="small" class="gap-top">
          <el-descriptions-item label="项目状态">{{ archiveResult.status }}</el-descriptions-item>
          <el-descriptions-item label="阶段">{{ archiveResult.stage }}</el-descriptions-item>
          <el-descriptions-item label="封包哈希" :span="2">{{ archiveResult.archive_package_hash }}</el-descriptions-item>
          <el-descriptions-item label="归档底稿数">{{ archiveResult.archived_worksheets }}</el-descriptions-item>
          <el-descriptions-item label="AI 留痕数">{{ archiveResult.ai_runs }}</el-descriptions-item>
          <el-descriptions-item label="经验沉淀提案" :span="2">{{ archiveResult.knowledge_proposals }} 条（FR-07-04 归档自动触发）</el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="verifyResult"
          type="success"
          :closable="false"
          show-icon
          class="gap-top"
          :title="`完整性校验通过：${verifyResult.members} 个成员逐项 sha256 比对一致，包内封包哈希与库内登记一致`"
          :description="`实包 ${verifyResult.object_key} · zip 整包 sha256 ${verifyResult.zip_sha256}`"
        />
      </el-card>

      <!-- 差错归档 v(n+1)（§15-16）：归档后差错→批准→副本修订→质控复核→重新归档 -->
      <el-card
        class="block-card"
        header="差错处理（§15-16 归档后差错：副本修订原稿锁定保留，重新归档 v(n+1) 双版本留痕）"
      >
        <el-space>
          <el-button
            v-if="canRegisterCorrection"
            type="primary"
            :disabled="!archived"
            @click="openRegister"
          >
            差错登记
          </el-button>
          <span v-if="!archived" class="hint">项目归档后方可登记差错单</span>
        </el-space>
        <el-table :data="corrections" size="small" class="gap-top" v-loading="loading">
          <el-table-column prop="code" label="差错单号" width="170" />
          <el-table-column prop="description" label="差错描述" min-width="200" show-overflow-tooltip />
          <el-table-column label="受影响底稿" min-width="180">
            <template #default="{ row }">
              <el-tag
                v-for="id in row.affected_worksheet_ids"
                :key="id"
                size="small"
                effect="plain"
                class="cond-tag"
              >
                {{ wsLabel(id) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="CORRECTION_TAG[row.status] ?? 'info'" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="登记/批准/复核" min-width="170">
            <template #default="{ row }">
              <div class="mini">登记 {{ userName(row.registered_by) }}</div>
              <div class="mini">批准 {{ userName(row.approved_by) }}</div>
              <div class="mini">复核 {{ userName(row.qc_reviewed_by) }}</div>
            </template>
          </el-table-column>
          <el-table-column label="新旧封包哈希（supersedes 链）" min-width="200">
            <template #default="{ row }">
              <template v-if="row.rearchive_package_hash">
                <div class="mini">v(n) {{ row.previous_package_hash?.slice(0, 16) }}…</div>
                <div class="mini">v(n+1) {{ row.rearchive_package_hash.slice(0, 16) }}…</div>
              </template>
              <span v-else class="mini">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="220" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="canApproveCorrection && row.status === '待批准'"
                link
                type="primary"
                @click="openApprove(row)"
              >
                批准/驳回
              </el-button>
              <el-button
                v-if="canReviseCorrection && row.status === '修订中'"
                link
                type="primary"
                @click="openRevise(row)"
              >
                修订登记
              </el-button>
              <el-button
                v-if="canQc && row.status === '修订中'"
                link
                type="warning"
                @click="doSubmitReview(row)"
              >
                提交复核
              </el-button>
              <el-button
                v-if="canQcReviewCorrection && row.status === '待质控复核'"
                link
                type="primary"
                @click="openQcReview(row)"
              >
                质控复核
              </el-button>
              <el-button
                v-if="canQc && row.status === '复核通过'"
                link
                type="danger"
                @click="doRearchive(row)"
              >
                重新归档
              </el-button>
              <el-tooltip v-if="row.status === '已驳回' && row.rejection_reason" :content="row.rejection_reason">
                <span class="mini danger-text">已驳回（悬停看原因）</span>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!corrections.length" description="暂无差错单" :image-size="48" />
        <el-alert
          v-if="rearchiveResult"
          type="success"
          :closable="false"
          show-icon
          class="gap-top"
          :title="`重新归档 v(n+1) 完成：${rearchiveResult.correction_code} · ${rearchiveResult.zip_members} 个成员`"
          :description="`新包 ${rearchiveResult.archive_package_hash}（supersedes ${rearchiveResult.previous_package_hash}）· 旧包 WORM 保留`"
        />
        <div class="hint">
          状态链：待批准 →（合伙人批准/驳回）→ 修订中 → 待质控复核 → 复核通过 → 已重新归档；
          修订走底稿版本链追加，原稿版本与 v(n) 归档包不可变（TECH_DESIGN §4.3.4）。
        </div>
      </el-card>
    </template>
    <el-empty v-else description="选择项目后查看质控视图" :image-size="72" />

    <!-- 门跳过对话框（R3 双批：合伙人批准 + 质控专员会批） -->
    <el-dialog v-model="skipVisible" :title="`跳过阶段门「${skipTarget?.stage ?? ''}」`" width="440px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="跳过为例外通道：须合伙人批准并由质控专员会批，双批身份落库留痕（R3）"
      />
      <el-form label-width="110px" class="gap-top">
        <el-form-item label="质控会批人" required>
          <el-select v-model="skipQcId" placeholder="选择质控专员" style="width: 100%">
            <el-option v-for="u in qcUsers" :key="u.id" :label="`${u.name}（${u.global_role}）`" :value="u.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="skipVisible = false">取消</el-button>
        <el-button type="danger" @click="submitSkip">确认跳过</el-button>
      </template>
    </el-dialog>

    <!-- 差错登记对话框（质控专员；受影响底稿限已归档） -->
    <el-dialog v-model="registerVisible" title="差错登记（§15-16 归档后差错）" width="560px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="登记后须合伙人批准方可解锁副本修订；同项目仅允许一张在途差错单"
      />
      <el-form label-width="110px" class="gap-top">
        <el-form-item label="差错描述" required>
          <el-input
            v-model="regDescription"
            type="textarea"
            :rows="3"
            maxlength="1000"
            show-word-limit
            placeholder="归档后发现的差错事实与影响范围"
          />
        </el-form-item>
        <el-form-item label="受影响底稿" required>
          <el-select v-model="regWorksheetIds" multiple placeholder="选择受影响底稿" style="width: 100%">
            <el-option
              v-for="w in worksheets"
              :key="w.id"
              :label="`${w.code} ${w.title}（${w.status}）`"
              :value="w.id"
              :disabled="w.status !== '已归档'"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="registerVisible = false">取消</el-button>
        <el-button type="primary" @click="submitRegister">登记</el-button>
      </template>
    </el-dialog>

    <!-- 差错批准/驳回对话框（合伙人；批准即解锁副本修订） -->
    <el-dialog v-model="approveVisible" :title="`差错审批「${approveTarget?.code ?? ''}」`" width="480px">
      <el-form label-width="110px">
        <el-form-item label="审批结论" required>
          <el-radio-group v-model="approveChoice">
            <el-radio value="approve">批准（解锁副本修订）</el-radio>
            <el-radio value="reject">驳回</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="approveChoice === 'reject'" label="驳回原因" required>
          <el-input v-model="approveReason" type="textarea" :rows="3" placeholder="驳回必附原因（留痕可查）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approveVisible = false">取消</el-button>
        <el-button :type="approveChoice === 'approve' ? 'primary' : 'danger'" @click="submitApprove">
          {{ approveChoice === 'approve' ? '确认批准' : '确认驳回' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 修订登记对话框（经理/审计员：受控上传修订稿 → 版本链追加 v(n+1)，原稿版本锁定保留） -->
    <el-dialog v-model="reviseVisible" :title="`修订登记「${reviseTarget?.code ?? ''}」`" width="560px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="修订稿直传对象存储（内容寻址键，sha256 落留痕），登记后版本链追加、原稿版本锁定保留"
      />
      <el-form label-width="110px" class="gap-top">
        <el-form-item label="受影响底稿" required>
          <el-select v-model="reviseWorksheetId" style="width: 100%">
            <el-option
              v-for="id in reviseTarget?.affected_worksheet_ids ?? []"
              :key="id"
              :label="wsLabel(id)"
              :value="id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="修订产物" required>
          <el-upload
            v-model:file-list="reviseFileList"
            :auto-upload="false"
            accept=".xlsx"
            :on-change="onReviseChange"
          >
            <el-button>选择修订稿（.xlsx）</el-button>
            <template #tip>
              <div class="hint">限模板保真写回 xlsx，单份 ≤ 20MB；同内容重传同键幂等</div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item label="修订说明" required>
          <el-input v-model="reviseSummary" type="textarea" :rows="3" placeholder="修订内容与依据（写入版本链摘要）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reviseVisible = false">取消</el-button>
        <el-button type="primary" @click="submitRevise">上传并登记修订</el-button>
      </template>
    </el-dialog>

    <!-- 质控复核对话框（质控专员；退回必附意见） -->
    <el-dialog v-model="qcVisible" :title="`差错质控复核「${qcTarget?.code ?? ''}」`" width="480px">
      <el-form label-width="110px">
        <el-form-item label="复核结论" required>
          <el-radio-group v-model="qcConclusion">
            <el-radio value="通过">通过（可重新归档）</el-radio>
            <el-radio value="退回">退回（回到修订中）</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="qcConclusion === '退回' ? '退回意见' : '复核意见'" :required="qcConclusion === '退回'">
          <el-input v-model="qcOpinion" type="textarea" :rows="3" placeholder="退回必附意见；通过可留复核说明" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="qcVisible = false">取消</el-button>
        <el-button :type="qcConclusion === '通过' ? 'primary' : 'danger'" @click="submitQcReview">
          确认{{ qcConclusion }}
        </el-button>
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
.block-card {
  margin-bottom: 16px;
}
.gap-top {
  margin-top: 12px;
}
.cond-tag {
  margin-right: 6px;
  margin-bottom: 4px;
}
.cond {
  display: inline-flex;
  flex-wrap: wrap;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-top: 8px;
}
.report-json {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}
.blocker {
  font-size: 13px;
  margin-top: 4px;
}
.mini {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
.danger-text {
  color: var(--el-color-danger);
}
</style>
