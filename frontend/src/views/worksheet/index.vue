<script setup lang="ts">
/**
 * 底稿工作台（PRD §12.3）——三层级材料投喂区 + AI 侧栏 + 准则锚点。
 * 双页签：审计底稿（FR-03）+ 函证台账（FR-04，§5.4 与底稿编制同阶段并行）。
 * 本期范围：底稿列表（FR-03-01）+ 创建（FR-03-03 锚点强制带出）+
 * AI 初稿三段式（FR-03-02：生成→线索→人工复核标记，红线 1/R2）+
 * 提交复核（§4.3 六状态）+ 交叉索引（FR-03-05/R7）+ 重要底稿终审入口（§15-11）。
 * 权限以后端为准：按钮显隐按 /auth/me 兜底，越权由后端 403/409 拦截。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errText } from '@/api/client'
import * as api from '@/api/worksheet'
import ConfirmLedger from './ConfirmLedger.vue'
import { useSessionStore } from '@/stores/session'
import type {
  AiDraftOut,
  AnchorOut,
  CrossRefsOut,
  ProjectOptionOut,
  TemplateOptionOut,
  WorksheetOut,
} from '@/api/types'

const session = useSessionStore()

const tab = ref<'ws' | 'cf'>('ws')
const projects = ref<ProjectOptionOut[]>([])
const projectId = ref('')
const worksheets = ref<WorksheetOut[]>([])
const anchors = ref<AnchorOut[]>([])
const templates = ref<TemplateOptionOut[]>([])
const current = ref<WorksheetOut | null>(null)
const loading = ref(false)

const canEdit = computed(() => session.can('worksheet.edit', 'OPERATE'))
const canFinal = computed(() => session.can('worksheet.review_sign', 'APPROVE'))

const STATUS_TAG: Record<string, 'info' | 'warning' | 'success' | 'danger'> = {
  草稿: 'info',
  待复核: 'warning',
  复核中: 'warning',
  复核通过: 'success',
  终审通过: 'success',
  已归档: 'info',
}

async function loadWorksheets() {
  if (!projectId.value) return
  loading.value = true
  try {
    worksheets.value = await api.listWorksheets(projectId.value)
  } catch (err) {
    ElMessage.error(errText(err))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------------------
// 创建底稿（FR-03-03：准则锚点强制带出，红线 2）
// ---------------------------------------------------------------------------
const createVisible = ref(false)
const createForm = reactive({ title: '', template_id: '', rule_anchor_id: '', is_important: false })

function openCreate() {
  Object.assign(createForm, { title: '', template_id: '', rule_anchor_id: '', is_important: false })
  createVisible.value = true
}

async function submitCreate() {
  if (!createForm.title || !createForm.template_id || !createForm.rule_anchor_id) {
    ElMessage.warning('请填写底稿标题并选择模板与准则锚点')
    return
  }
  try {
    const ws = await api.createWorksheet(projectId.value, { ...createForm })
    ElMessage.success(`底稿 ${ws.code} 已创建（锚点随创建强制带出，红线 2）`)
    createVisible.value = false
    await loadWorksheets()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// AI 初稿三段式（FR-03-02）：生成 → 线索 → 人工复核标记（红线 1/R2）
// ---------------------------------------------------------------------------
const runsVisible = ref(false)
const runs = ref<AiDraftOut[]>([])
const confirmRun = ref<AiDraftOut | null>(null)
const aiReviewForm = reactive({
  conclusion: '采信' as '采信' | '部分采信' | '不采信',
  rejection_reason: '' as string,
})

async function openRuns(row: WorksheetOut) {
  current.value = row
  try {
    runs.value = await api.listAiRuns(row.id)
    runsVisible.value = true
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function generateDraft(row: WorksheetOut) {
  try {
    const run = await api.generateAiDraft(row.id)
    ElMessage.success(`AI 初稿已生成（run ${run.run_no}），产物为线索态，须经人工复核标记`)
    await openRuns(row)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

function openConfirm(run: AiDraftOut) {
  confirmRun.value = run
  aiReviewForm.conclusion = '采信'
  aiReviewForm.rejection_reason = ''
}

async function submitAiReview() {
  if (!confirmRun.value) return
  if (aiReviewForm.conclusion === '不采信' && !aiReviewForm.rejection_reason) {
    ElMessage.warning('不采信必须附带原因分类（§15-16）')
    return
  }
  try {
    await api.reviewAiRun(confirmRun.value.run_no, {
      conclusion: aiReviewForm.conclusion,
      rejection_reason:
        aiReviewForm.conclusion === '不采信'
          ? (aiReviewForm.rejection_reason as '事实错误' | '口径错误' | '幻觉' | '其他')
          : null,
    })
    ElMessage.success(
      aiReviewForm.conclusion === '不采信'
        ? '已标记不采信，AI 内容不入版本链'
        : '复核标记完成，采信内容已落版本链（红线 1/R2 第三段）',
    )
    confirmRun.value = null
    if (current.value) runs.value = await api.listAiRuns(current.value.id)
    await loadWorksheets()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 提交复核（草稿→待复核）与终审（复核通过→终审通过，仅重要底稿 §15-11）
// ---------------------------------------------------------------------------
async function submitForReview(row: WorksheetOut) {
  try {
    await ElMessageBox.confirm(
      `提交底稿「${row.title}」进入复核？提交前 AI 内容须完成复核标记（R2）。`,
      '提交复核',
    )
  } catch {
    return
  }
  try {
    await api.submitWorksheet(row.id)
    ElMessage.success('已提交复核（草稿→待复核）')
    await loadWorksheets()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function doFinalApprove(row: WorksheetOut) {
  try {
    await ElMessageBox.confirm(
      `对重要底稿「${row.title}」执行终审？复核通过→终审通过（§15-11）。`,
      '底稿终审',
    )
  } catch {
    return
  }
  try {
    await api.finalApprove(row.id)
    ElMessage.success('终审通过（复核通过→终审通过）')
    await loadWorksheets()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 下载当前版本产物（FR-03-02 收口：AI 初稿采信后的模板保真写回 xlsx）
// ---------------------------------------------------------------------------
async function doDownload(row: WorksheetOut) {
  try {
    const blob = await api.downloadWorksheetFile(row.id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `worksheet-${row.code}-${row.current_version}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 交叉索引（FR-03-05/R7：草稿态全量覆盖保存，自动校验断链）
// ---------------------------------------------------------------------------
const refsVisible = ref(false)
const refRows = ref<{ code: string; note: string }[]>([])
const brokenRefs = ref<string[]>([])

async function openRefs(row: WorksheetOut) {
  current.value = row
  refRows.value = []
  brokenRefs.value = []
  refsVisible.value = true
}

function addRef() {
  refRows.value.push({ code: '', note: '' })
}

async function saveRefs() {
  if (!current.value) return
  const refs = refRows.value.filter((r) => r.code)
  try {
    const out: CrossRefsOut = await api.updateCrossRefs(current.value.id, refs)
    brokenRefs.value = out.broken_refs
    if (out.ok) {
      ElMessage.success('交叉索引已保存，无断链（R7）')
      refsVisible.value = false
    } else {
      ElMessage.warning(`存在 ${out.broken_refs.length} 条断链引用，请修正后重试`)
    }
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

onMounted(async () => {
  try {
    ;[projects.value, anchors.value, templates.value] = await Promise.all([
      api.listProjectOptions(),
      api.listAnchors(),
      api.listTemplates(),
    ])
  } catch (err) {
    ElMessage.error(errText(err))
  }
})
</script>

<template>
  <div class="worksheet-view">
    <div class="toolbar">
      <h2>底稿工作台</h2>
      <el-space>
        <el-select
          v-model="projectId"
          placeholder="选择项目"
          style="width: 280px"
          @change="loadWorksheets"
        >
          <el-option
            v-for="p in projects"
            :key="p.id"
            :label="`${p.project_no} ${p.name}`"
            :value="p.id"
          />
        </el-select>
        <el-button :loading="loading" :disabled="!projectId" @click="loadWorksheets">刷新</el-button>
        <el-button v-if="canEdit && tab === 'ws'" type="primary" :disabled="!projectId" @click="openCreate">
          新建底稿
        </el-button>
      </el-space>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="审计底稿" name="ws">
        <el-table v-if="projectId" :data="worksheets" v-loading="loading" size="default">
          <el-table-column prop="code" label="底稿编号" width="180" />
          <el-table-column prop="title" label="标题" min-width="200" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="STATUS_TAG[row.status] ?? 'info'" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="current_version" label="版本" width="80" />
          <el-table-column label="重要" width="72">
            <template #default="{ row }">
              <el-tag v-if="row.is_important" type="danger" size="small">重要</el-tag>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" min-width="300">
            <template #default="{ row }">
              <el-button
                v-if="canEdit && row.status === '草稿'"
                link
                type="primary"
                @click="generateDraft(row)"
              >
                AI 初稿
              </el-button>
              <el-button link type="primary" @click="openRuns(row)">AI 线索</el-button>
              <el-button
                v-if="row.file_key?.endsWith('.xlsx')"
                link
                type="primary"
                @click="doDownload(row)"
              >
                下载
              </el-button>
              <el-button
                v-if="canEdit && row.status === '草稿'"
                link
                type="primary"
                @click="openRefs(row)"
              >
                交叉索引
              </el-button>
              <el-button
                v-if="canEdit && row.status === '草稿'"
                link
                type="success"
                @click="submitForReview(row)"
              >
                提交复核
              </el-button>
              <el-button
                v-if="canFinal && row.status === '复核通过' && row.is_important"
                link
                type="warning"
                @click="doFinalApprove(row)"
              >
                终审
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="选择项目后查看底稿" :image-size="72" />
      </el-tab-pane>

      <el-tab-pane label="函证台账" name="cf" lazy>
        <ConfirmLedger :project-id="projectId" :can-edit="canEdit" />
      </el-tab-pane>
    </el-tabs>

    <!-- 新建底稿（FR-03-03：模板 + 准则锚点必选，红线 2） -->
    <el-dialog v-model="createVisible" title="新建底稿" width="520px">
      <el-form label-width="96px">
        <el-form-item label="底稿标题" required>
          <el-input v-model="createForm.title" placeholder="如：货币资金审计表" />
        </el-form-item>
        <el-form-item label="底稿模板" required>
          <el-select v-model="createForm.template_id" placeholder="选择模板" style="width: 100%">
            <el-option
              v-for="t in templates"
              :key="t.id"
              :label="`${t.type}（${t.version}）`"
              :value="t.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="准则锚点" required>
          <el-select v-model="createForm.rule_anchor_id" placeholder="选择准则锚点" style="width: 100%">
            <el-option
              v-for="a in anchors"
              :key="a.id"
              :label="`${a.standard_no} ${a.clause}`"
              :value="a.id"
            >
              <span>{{ a.standard_no }} {{ a.clause }}</span>
              <span class="anchor-purpose">{{ a.purpose }}</span>
            </el-option>
          </el-select>
        </el-form-item>
        <el-form-item label="重要底稿">
          <el-switch v-model="createForm.is_important" />
          <span class="hint">勾选后复核通过须合伙人终审（§15-11）</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" @click="submitCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- AI 运行线索列表（FR-03-02 第三段确认；线索态醒目，未确认不可归档） -->
    <el-drawer v-model="runsVisible" :title="`AI 运行线索 · ${current?.title ?? ''}`" size="520px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="AI 产物为线索态，采信后才落版本链（红线 1/R2）；不采信须附原因分类（§15-16）"
      />
      <el-table :data="runs" size="small" class="runs-table">
        <el-table-column prop="run_no" label="运行号" width="120" show-overflow-tooltip />
        <el-table-column prop="module" label="模块" width="90" />
        <el-table-column prop="model_version" label="模型版本" width="100" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === '线索' ? 'warning' : 'success'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button v-if="canEdit && row.status === '线索'" link type="primary" @click="openConfirm(row)">
              确认
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!runs.length" description="暂无 AI 运行记录（草稿态可生成 AI 初稿）" :image-size="56" />
    </el-drawer>

    <!-- AI 复核标记确认（红线 1/R2 第三段） -->
    <el-dialog
      :model-value="!!confirmRun"
      title="AI 输出人工复核标记"
      width="440px"
      :show-close="false"
      @close="confirmRun = null"
    >
      <template v-if="confirmRun">
        <el-form label-width="96px">
          <el-form-item label="运行号">{{ confirmRun.run_no }}</el-form-item>
          <el-form-item label="复核结论" required>
            <el-radio-group v-model="aiReviewForm.conclusion">
              <el-radio value="采信">采信</el-radio>
              <el-radio value="部分采信">部分采信</el-radio>
              <el-radio value="不采信">不采信</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item v-if="aiReviewForm.conclusion === '不采信'" label="原因分类" required>
            <el-select v-model="aiReviewForm.rejection_reason" placeholder="必选原因分类" style="width: 100%">
              <el-option label="事实错误" value="事实错误" />
              <el-option label="口径错误" value="口径错误" />
              <el-option label="幻觉" value="幻觉" />
              <el-option label="其他" value="其他" />
            </el-select>
          </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="confirmRun = null">取消</el-button>
        <el-button type="primary" @click="submitAiReview">提交标记</el-button>
      </template>
    </el-dialog>

    <!-- 交叉索引编辑（FR-03-05：保存即断链校验，R7） -->
    <el-dialog v-model="refsVisible" :title="`交叉索引 · ${current?.code ?? ''}`" width="560px">
      <el-alert
        v-if="brokenRefs.length"
        type="error"
        :closable="false"
        show-icon
        :title="`断链引用：${brokenRefs.join('、')}（引用编号在本项目不存在）`"
      />
      <el-table :data="refRows" size="small">
        <el-table-column label="被引用底稿编号" width="200">
          <template #default="{ row }">
            <el-input v-model="row.code" placeholder="如 A2026-0001-WS002" />
          </template>
        </el-table-column>
        <el-table-column label="引用说明">
          <template #default="{ row }">
            <el-input v-model="row.note" placeholder="选填，≤200 字" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ $index }">
            <el-button link type="danger" @click="refRows.splice($index, 1)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-button link type="primary" class="add-ref" @click="addRef">+ 添加引用</el-button>
      <div class="hint block-hint">仅草稿态可编辑；保存为全量覆盖并自动校验断链（R7）</div>
      <template #footer>
        <el-button @click="refsVisible = false">取消</el-button>
        <el-button type="primary" @click="saveRefs">保存</el-button>
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
.anchor-purpose {
  float: right;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 12px;
}
.runs-table {
  margin-top: 12px;
}
.add-ref {
  margin-top: 8px;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: 8px;
}
.block-hint {
  display: block;
  margin-left: 0;
  margin-top: 8px;
}
</style>
