<script setup lang="ts">
/**
 * 复核工作台（PRD §12.4）——复核队列 + 意见流 + 双签 + 留痕时间线。
 * 本期范围：待复核/复核中队列（FR-05-01，can_take 为 R1 前置标注）+
 * 受理（R1 权威拦截在后端）+ 复核结论（通过/退回必附意见，FR-05-02）+
 * 时间线抽屉（全部轮次意见往返 + 电子签名可追溯）。
 * 复核动作走 /worksheets 复核端点（硬约束 2/9），本域仅只读视图。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { errText } from '@/api/client'
import * as reviewApi from '@/api/review'
import * as wsApi from '@/api/worksheet'
import { useSessionStore } from '@/stores/session'
import type { PendingReviewOut, ReviewTimelineOut } from '@/api/types'
import ReportSign from './ReportSign.vue'

const session = useSessionStore()
const route = useRoute()

// 支持 /review?tab=rpt 直达报告签发（audit-hub 命令面板「报告签发」触发词）
const tab = ref<'queue' | 'rpt'>(route.query.tab === 'rpt' ? 'rpt' : 'queue')

const queue = ref<PendingReviewOut[]>([])
const loading = ref(false)

const canReview = computed(() => session.can('worksheet.review_sign', 'OPERATE'))

async function refresh() {
  loading.value = true
  try {
    queue.value = await reviewApi.pendingQueue()
  } catch (err) {
    ElMessage.error(errText(err))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------------------
// 受理复核（待复核→复核中）：R1 前置标注禁用按钮，权威拦截在后端（403）
// ---------------------------------------------------------------------------
async function take(row: PendingReviewOut) {
  try {
    await ElMessageBox.confirm(
      `受理底稿「${row.title}」的复核（第 ${row.next_round_no} 轮）？受理后进入复核中。`,
      '受理复核',
    )
  } catch {
    return
  }
  try {
    await wsApi.takeReview(row.worksheet_id)
    ElMessage.success('已受理（待复核→复核中）')
    await refresh()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 复核结论（通过/退回）：退回必附意见（engine 强制，FR-05-02）
// ---------------------------------------------------------------------------
const concludeVisible = ref(false)
const concludeTarget = ref<PendingReviewOut | null>(null)
const concludeForm = ref({ conclusion: '通过' as '通过' | '退回', opinion: '', revision_requirement: '' })

function openConclude(row: PendingReviewOut) {
  concludeTarget.value = row
  concludeForm.value = { conclusion: '通过', opinion: '', revision_requirement: '' }
  concludeVisible.value = true
}

async function submitConclude() {
  if (!concludeTarget.value) return
  if (concludeForm.value.conclusion === '退回' && !concludeForm.value.opinion) {
    ElMessage.warning('退回必须附复核意见（FR-05-02）')
    return
  }
  try {
    await wsApi.concludeReview(concludeTarget.value.worksheet_id, {
      conclusion: concludeForm.value.conclusion,
      opinion: concludeForm.value.opinion || null,
      revision_requirement: concludeForm.value.revision_requirement || null,
    })
    ElMessage.success(
      concludeForm.value.conclusion === '通过'
        ? '复核通过，电子签名已落痕（FR-05-02）'
        : '已退回编制人整改（附意见）',
    )
    concludeVisible.value = false
    concludeTarget.value = null
    await refresh()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

// ---------------------------------------------------------------------------
// 时间线抽屉（FR-05-02：轮次升序 + 电子签名可追溯）
// ---------------------------------------------------------------------------
const timelineVisible = ref(false)
const timeline = ref<ReviewTimelineOut | null>(null)

async function openTimeline(row: PendingReviewOut) {
  try {
    timeline.value = await reviewApi.reviewTimeline(row.worksheet_id)
    timelineVisible.value = true
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

onMounted(refresh)
</script>

<template>
  <div class="review-view">
    <div class="toolbar">
      <h2>复核工作台</h2>
      <el-button :loading="loading" @click="refresh">刷新</el-button>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane label="复核队列" name="queue">
        <el-table :data="queue" v-loading="loading">
          <el-table-column prop="code" label="底稿编号" width="180" />
          <el-table-column prop="title" label="标题" min-width="180" />
          <el-table-column label="状态" width="92">
            <template #default="{ row }">
              <el-tag :type="row.status === '待复核' ? 'warning' : 'primary'" size="small">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="prepared_by_name" label="编制人" width="110" />
          <el-table-column prop="current_version" label="版本" width="76" />
          <el-table-column prop="next_round_no" label="轮次" width="68" />
          <el-table-column label="重要" width="72">
            <template #default="{ row }">
              <el-tag v-if="row.is_important" type="danger" size="small">重要</el-tag>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" min-width="220">
            <template #default="{ row }">
              <el-tooltip
                v-if="row.status === '待复核'"
                content="编制人与复核人须分离（R1）；无复核签发等级不可受理"
                :disabled="row.can_take"
              >
                <span>
                  <el-button
                    link
                    type="primary"
                    :disabled="!row.can_take || !canReview"
                    @click="take(row)"
                  >
                    受理
                  </el-button>
                </span>
              </el-tooltip>
              <el-button
                v-if="row.status === '复核中' && canReview"
                link
                type="success"
                @click="openConclude(row)"
              >
                出具结论
              </el-button>
              <el-button link @click="openTimeline(row)">时间线</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!queue.length && !loading" description="暂无待复核底稿" :image-size="72" />
      </el-tab-pane>
      <el-tab-pane label="报告签发" name="rpt" lazy>
        <ReportSign />
      </el-tab-pane>
    </el-tabs>

    <!-- 复核结论对话框（退回必附意见，engine 强制；通过落电子签名） -->
    <el-dialog v-model="concludeVisible" title="复核结论" width="480px">
      <template v-if="concludeTarget">
        <el-descriptions :column="2" size="small" border class="conclude-meta">
          <el-descriptions-item label="底稿">{{ concludeTarget.code }}</el-descriptions-item>
          <el-descriptions-item label="轮次">第 {{ concludeTarget.next_round_no }} 轮</el-descriptions-item>
        </el-descriptions>
        <el-form label-width="96px">
          <el-form-item label="结论" required>
            <el-radio-group v-model="concludeForm.conclusion">
              <el-radio value="通过">通过</el-radio>
              <el-radio value="退回">退回</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item
            v-if="concludeForm.conclusion === '退回'"
            label="复核意见"
            required
          >
            <el-input
              v-model="concludeForm.opinion"
              type="textarea"
              :rows="3"
              maxlength="500"
              show-word-limit
              placeholder="退回必附意见（FR-05-02），将回传编制人"
            />
          </el-form-item>
          <el-form-item v-if="concludeForm.conclusion === '退回'" label="修订要求">
            <el-input
              v-model="concludeForm.revision_requirement"
              maxlength="500"
              placeholder="选填，如：补充 12 月银行流水核验"
            />
          </el-form-item>
        </el-form>
      </template>
      <template #footer>
        <el-button @click="concludeVisible = false">取消</el-button>
        <el-button type="primary" @click="submitConclude">提交结论</el-button>
      </template>
    </el-dialog>

    <!-- 时间线抽屉：全部轮次意见往返 + 电子签名（FR-05-02 留痕可追溯） -->
    <el-drawer
      v-model="timelineVisible"
      :title="`复核时间线 · ${timeline?.code ?? ''}`"
      size="480px"
    >
      <template v-if="timeline">
        <el-descriptions :column="2" size="small" border>
          <el-descriptions-item label="当前状态">{{ timeline.status }}</el-descriptions-item>
          <el-descriptions-item label="当前版本">{{ timeline.current_version }}</el-descriptions-item>
        </el-descriptions>
        <el-timeline class="timeline">
          <el-timeline-item
            v-for="r in timeline.records"
            :key="r.round_no"
            :type="r.conclusion === '退回' ? 'danger' : 'success'"
            :timestamp="`第 ${r.round_no} 轮 · ${r.reviewed_date ?? ''}`"
          >
            <div class="record">
              <div class="record-head">
                <el-tag :type="r.conclusion === '退回' ? 'danger' : 'success'" size="small">
                  {{ r.conclusion }}
                </el-tag>
                <span class="reviewer">{{ r.reviewer_name ?? '—' }}</span>
              </div>
              <div v-if="r.opinion" class="line">意见：{{ r.opinion }}</div>
              <div v-if="r.revision_requirement" class="line">修订要求：{{ r.revision_requirement }}</div>
              <div v-if="r.signature" class="line signature">
                电子签名：{{ r.signature['复核人'] }} · {{ r.signature['日期'] }} ·
                {{ r.signature['证书号'] }}
              </div>
            </div>
          </el-timeline-item>
        </el-timeline>
        <el-empty
          v-if="!timeline.records.length"
          description="暂无复核轮次（尚无结论出具）"
          :image-size="56"
        />
      </template>
    </el-drawer>
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
.conclude-meta {
  margin-bottom: 12px;
}
.timeline {
  margin-top: 16px;
  padding-left: 4px;
}
.record-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.reviewer {
  font-weight: 600;
}
.record .line {
  margin-top: 4px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.record .signature {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
