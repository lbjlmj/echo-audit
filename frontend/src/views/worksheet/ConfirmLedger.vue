<script setup lang="ts">
/**
 * 函证台账（FR-04，PRD §5.4 与底稿编制同阶段并行）——八状态全链操作面。
 * 生成台账（FR-04-01）→ 发出登记运单号（FR-04-02，P0 人工登记）→
 * 回函录入自动核对（FR-04-03：一致→核对一致；差异→差异处理）→
 * 超期未回/差异→替代程序（FR-04-04）→ 闭环（全闭环为门 5 准入条件）。
 * 按钮显隐按八状态机（engine 权威）+ worksheet.edit 权限兜底；越权由后端 403/409 拦截。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errText } from '@/api/client'
import * as api from '@/api/confirm'
import type { CfLedgerItemBody, CfTrailOut, ConfirmationOut } from '@/api/types'

const props = defineProps<{ projectId: string; canEdit: boolean }>()

const rows = ref<ConfirmationOut[]>([])
const loading = ref(false)

const STATUS_TAG: Record<string, 'primary' | 'success' | 'info' | 'warning' | 'danger'> = {
  台账已生成: 'info',
  已发出: 'primary',
  已回函: 'warning',
  超期未回: 'danger',
  核对一致: 'success',
  差异处理: 'danger',
  替代程序: 'warning',
  闭环: 'success',
}

const openCount = computed(() => rows.value.filter((r) => r.status !== '闭环').length)
const overdueCount = computed(() => rows.value.filter((r) => r.status === '超期未回').length)
const closedCount = computed(() => rows.value.filter((r) => r.status === '闭环').length)

async function load() {
  if (!props.projectId) return
  loading.value = true
  try {
    rows.value = await api.listConfirmations(props.projectId)
  } catch (err) {
    ElMessage.error(errText(err))
  } finally {
    loading.value = false
  }
}

// immediate：页签懒挂载时项目已选定，须立即加载首屏（否则 watch 不触发、台账恒空）
watch(() => props.projectId, load, { immediate: true })

// ---------------------------------------------------------------------------
// 生成台账（FR-04-01）：按账户/往来方逐行录入
// ---------------------------------------------------------------------------
const ledgerVisible = ref(false)
const ledgerItems = ref<CfLedgerItemBody[]>([])

function openLedger() {
  ledgerItems.value = [{ type: '银行', counterparty: '', amount: 0 }]
  ledgerVisible.value = true
}

function addLedgerItem() {
  ledgerItems.value.push({ type: '往来', counterparty: '', amount: 0 })
}

async function submitLedger() {
  const items = ledgerItems.value.filter((i) => i.counterparty.trim() && i.amount > 0)
  if (!items.length) {
    ElMessage.warning('请至少录入一行有效的台账项（对方单位 + 正数金额）')
    return
  }
  try {
    const created = await api.generateLedger(props.projectId, items)
    ElMessage.success(`函证台账已生成 ${created.length} 项（FR-04-01，进入「台账已生成」）`)
    ledgerVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 发出（FR-04-02）：P0 运单号人工登记（顺丰适配器沙箱联调后自动回写）
// ---------------------------------------------------------------------------
const issueVisible = ref(false)
const issueTarget = ref<ConfirmationOut | null>(null)
const issueForm = ref('')

function openIssue(row: ConfirmationOut) {
  issueTarget.value = row
  issueForm.value = ''
  issueVisible.value = true
}

async function submitIssue() {
  if (!issueTarget.value) return
  if (!issueForm.value.trim()) {
    ElMessage.warning('快递单号必填（FR-04-02）')
    return
  }
  try {
    await api.issueCf(issueTarget.value.id, issueForm.value.trim())
    ElMessage.success('函证已发出（台账已生成→已发出），物流状态待适配器回写')
    issueVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 回函录入并自动核对（FR-04-03）：金额一致→核对一致；差异→差异处理
// ---------------------------------------------------------------------------
const replyVisible = ref(false)
const replyTarget = ref<ConfirmationOut | null>(null)
const replyForm = ref({ reply_amount: 0, reply_doc: '' })

function openReply(row: ConfirmationOut) {
  replyTarget.value = row
  replyForm.value = { reply_amount: 0, reply_doc: '' }
  replyVisible.value = true
}

async function submitReply() {
  if (!replyTarget.value) return
  if (!replyForm.value.reply_doc.trim()) {
    ElMessage.warning('回函文件引用必填（扫描件对象键）')
    return
  }
  try {
    const out = await api.replyCf(replyTarget.value.id, {
      reply_amount: replyForm.value.reply_amount,
      reply_doc: replyForm.value.reply_doc.trim(),
    })
    if (out.status === '核对一致') {
      ElMessage.success('回函金额一致，已自动核对为「核对一致」（FR-04-03）')
    } else {
      ElMessage.warning('回函金额存在差异，已转入「差异处理」，请评估替代程序')
    }
    replyVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 替代程序（FR-04-04）与闭环（门 5 计数）
// ---------------------------------------------------------------------------
const altVisible = ref(false)
const altTarget = ref<ConfirmationOut | null>(null)
const altForm = ref('')

function openAlt(row: ConfirmationOut) {
  altTarget.value = row
  altForm.value = ''
  altVisible.value = true
}

async function submitAlt() {
  if (!altTarget.value) return
  if (altForm.value.trim().length < 2) {
    ElMessage.warning('替代测试记录/凭据必填')
    return
  }
  try {
    await api.altCf(altTarget.value.id, altForm.value.trim())
    ElMessage.success('替代程序已执行（记录已留痕），可执行闭环')
    altVisible.value = false
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function doClose(row: ConfirmationOut) {
  try {
    await ElMessageBox.confirm(
      `确认对「${row.counterparty}」函证闭环？全部函证闭环为实质性阶段门（门 5）准入条件。`,
      '函证闭环',
    )
  } catch {
    return
  }
  try {
    await api.closeCf(row.id)
    ElMessage.success('函证已闭环')
    await load()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 留痕时间线（所见即所查 §12；含 Beat 超期扫描记录）
// ---------------------------------------------------------------------------
const trailsVisible = ref(false)
const trailTarget = ref<ConfirmationOut | null>(null)
const trails = ref<CfTrailOut[]>([])

async function openTrails(row: ConfirmationOut) {
  trailTarget.value = row
  trails.value = []
  trailsVisible.value = true
  try {
    trails.value = await api.listCfTrails(row.id)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

function diffText(row: ConfirmationOut): string {
  if (!row.difference) return '—'
  const d = row.difference
  return `回函 ${d['回函金额']} / 台账 ${d['台账金额']} / 差异 ${d['差异']}`
}
</script>

<template>
  <div class="confirm-ledger">
    <div class="ledger-head">
      <el-space>
        <el-tag type="info" effect="plain">共 {{ rows.length }} 项</el-tag>
        <el-tag v-if="overdueCount" type="danger" effect="plain">超期未回 {{ overdueCount }}</el-tag>
        <el-tag :type="openCount ? 'warning' : 'success'" effect="plain">
          未闭环 {{ openCount }} / 已闭环 {{ closedCount }}
        </el-tag>
      </el-space>
      <el-button v-if="canEdit" type="primary" :disabled="!projectId" @click="openLedger">
        生成台账
      </el-button>
    </div>

    <el-table v-if="projectId" :data="rows" v-loading="loading" size="default">
      <el-table-column prop="type" label="类型" width="72" />
      <el-table-column prop="counterparty" label="对方单位/账户行" min-width="180" show-overflow-tooltip />
      <el-table-column prop="amount" label="函证金额" width="120" align="right" />
      <el-table-column label="状态" width="104">
        <template #default="{ row }">
          <el-tag :type="STATUS_TAG[row.status] ?? 'info'" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="waybill_no" label="运单号" width="130">
        <template #default="{ row }">
          <span v-if="row.waybill_no">{{ row.waybill_no }}</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="超期提醒" width="96">
        <template #default="{ row }">
          <el-tag v-if="row.remind_count" type="danger" size="small" effect="plain">
            已提醒 {{ row.remind_count }} 次
          </el-tag>
          <span v-else>—</span>
        </template>
      </el-table-column>
      <el-table-column label="差异（FR-04-03）" min-width="200">
        <template #default="{ row }">{{ diffText(row) }}</template>
      </el-table-column>
      <el-table-column label="操作" min-width="220" fixed="right">
        <template #default="{ row }">
          <el-button v-if="canEdit && row.status === '台账已生成'" link type="primary" @click="openIssue(row)">
            发出
          </el-button>
          <el-button
            v-if="canEdit && (row.status === '已发出' || row.status === '超期未回')"
            link
            type="primary"
            @click="openReply(row)"
          >
            回函录入
          </el-button>
          <el-button
            v-if="canEdit && (row.status === '超期未回' || row.status === '差异处理')"
            link
            type="warning"
            @click="openAlt(row)"
          >
            替代程序
          </el-button>
          <el-button
            v-if="canEdit && (row.status === '核对一致' || row.status === '替代程序')"
            link
            type="success"
            @click="doClose(row)"
          >
            闭环
          </el-button>
          <el-button link type="info" @click="openTrails(row)">留痕</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty v-else description="选择项目后查看函证台账" :image-size="72" />

    <!-- 生成台账（FR-04-01） -->
    <el-dialog v-model="ledgerVisible" title="生成函证台账" width="640px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="按银行账户/往来款逐项录入；金额不出站（§4.2.7），台账项生成后进入「台账已生成」"
      />
      <el-table :data="ledgerItems" size="small" class="gap-top">
        <el-table-column label="类型" width="110">
          <template #default="{ row }">
            <el-select v-model="row.type">
              <el-option label="银行" value="银行" />
              <el-option label="往来" value="往来" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="对方单位/账户行">
          <template #default="{ row }">
            <el-input v-model="row.counterparty" placeholder="如：工商银行示例支行" />
          </template>
        </el-table-column>
        <el-table-column label="函证金额(元)" width="170">
          <template #default="{ row }">
            <el-input-number v-model="row.amount" :min="0" :controls="false" style="width: 140px" />
          </template>
        </el-table-column>
        <el-table-column label="" width="80">
          <template #default="{ $index }">
            <el-button link type="danger" @click="ledgerItems.splice($index, 1)">移除</el-button>
          </template>
        </el-table-column>
      </el-table>
      <el-button link type="primary" class="gap-top" @click="addLedgerItem">+ 添加台账项</el-button>
      <template #footer>
        <el-button @click="ledgerVisible = false">取消</el-button>
        <el-button type="primary" @click="submitLedger">生成</el-button>
      </template>
    </el-dialog>

    <!-- 发出（FR-04-02） -->
    <el-dialog v-model="issueVisible" :title="`发出函证 · ${issueTarget?.counterparty ?? ''}`" width="480px">
      <el-form label-width="96px">
        <el-form-item label="快递单号" required>
          <el-input v-model="issueForm" placeholder="人工登记运单号（适配器沙箱联调后自动回写）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="issueVisible = false">取消</el-button>
        <el-button type="primary" @click="submitIssue">确认发出</el-button>
      </template>
    </el-dialog>

    <!-- 回函录入（FR-04-03：录入后自动核对，一次录入连转两态） -->
    <el-dialog v-model="replyVisible" :title="`回函录入 · ${replyTarget?.counterparty ?? ''}`" width="480px">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="录入后系统自动核对（容差 0.01 元）：一致→核对一致；差异→差异处理并落差异 JSON"
      />
      <el-form label-width="110px" class="gap-top">
        <el-form-item label="回函金额(元)" required>
          <el-input-number v-model="replyForm.reply_amount" :min="0" :controls="false" style="width: 180px" />
        </el-form-item>
        <el-form-item label="回函文件引用" required>
          <el-input v-model="replyForm.reply_doc" placeholder="扫描件对象键，如 replies/bank01.pdf" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="replyVisible = false">取消</el-button>
        <el-button type="primary" @click="submitReply">录入并核对</el-button>
      </template>
    </el-dialog>

    <!-- 替代程序（FR-04-04） -->
    <el-dialog v-model="altVisible" :title="`执行替代程序 · ${altTarget?.counterparty ?? ''}`" width="520px">
      <el-form label-width="110px">
        <el-form-item label="替代测试记录" required>
          <el-input
            v-model="altForm"
            type="textarea"
            :rows="3"
            placeholder="如：已执行期后收款核查并取得银行流水（替代测试）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="altVisible = false">取消</el-button>
        <el-button type="primary" @click="submitAlt">执行</el-button>
      </template>
    </el-dialog>

    <!-- 留痕时间线（所见即所查；哈希链字段不出站） -->
    <el-drawer v-model="trailsVisible" :title="`函证留痕 · ${trailTarget?.counterparty ?? ''}`" size="480px">
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
.ledger-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
