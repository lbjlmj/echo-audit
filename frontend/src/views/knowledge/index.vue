<script setup lang="ts">
/**
 * 知识库问答（PRD §12.6）——RAG 对话窗，答案强制带准则锚点引用。
 * 本期范围：FR-07-01 准则问答（任一角色；项目上下文必填=留痕归属）+
 * 未覆盖明示警示（硬约束 6：citations 为空 ⇔ 不强行作答）+
 * 问答历史回放（append-only 留痕，可追溯）。
 * 知识条目审批链（§15-3）/ Skill 管理 / 图谱查询随后续批次入本页。
 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { errText } from '@/api/client'
import { askQa, qaHistory } from '@/api/knowledge'
import { listProjectOptions } from '@/api/worksheet'
import type { CitationOut, ProjectOptionOut, QaHistoryOut } from '@/api/types'

interface ChatMsg {
  role: '问' | '答'
  text: string
  covered?: boolean
  citations?: CitationOut[]
  runNo?: string
  operator?: string
  askedAt?: string
}

const projects = ref<ProjectOptionOut[]>([])
const projectId = ref('')
const question = ref('')
const asking = ref(false)
const messages = ref<ChatMsg[]>([])
const history = ref<QaHistoryOut[]>([])
const historyVisible = ref(false)

const canAsk = computed(() => projectId.value && question.value.trim().length >= 2)

async function loadHistory() {
  if (!projectId.value) return
  try {
    history.value = await qaHistory(projectId.value)
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

/** 历史回放灌入对话流（append-only 留痕 → 按时间正序重现） */
function replayHistory() {
  messages.value = history.value.map((h) => ({
    role: '答' as const,
    text: h.answer,
    covered: h.covered,
    citations: (h.citations ?? []) as unknown as CitationOut[],
    runNo: h.run_no ?? undefined,
    operator: h.operator,
    askedAt: h.asked_at?.slice(0, 19).replace('T', ' '),
  })).flatMap((m, i) => {
    const q = history.value[i]
    return [
      { role: '问' as const, text: q.question, operator: q.operator, askedAt: q.asked_at?.slice(0, 19).replace('T', ' ') },
      m,
    ]
  })
}

async function onProjectChange() {
  messages.value = []
  history.value = []
  await loadHistory()
  replayHistory()
}

async function ask() {
  if (!canAsk.value) {
    ElMessage.warning('请选择项目并输入至少 2 个字的问题')
    return
  }
  const q = question.value.trim()
  messages.value.push({ role: '问', text: q })
  question.value = ''
  asking.value = true
  await nextTick(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }))
  try {
    const out = await askQa(projectId.value, q)
    messages.value.push({
      role: '答', text: out.answer, covered: out.covered,
      citations: out.citations, runNo: out.run_no,
    })
    if (!out.covered) {
      ElMessage.warning('知识库未覆盖该问题，已明示不作答（硬约束 6：无引用不出答案）')
    }
    await loadHistory()
  } catch (err) {
    ElMessage.error(errText(err))
    messages.value.push({ role: '答', text: `提问失败：${errText(err)}`, covered: false })
  } finally {
    asking.value = false
  }
}

function fmtScore(score: number): string {
  return `匹配度 ${(score * 100).toFixed(0)}%`
}

onMounted(async () => {
  try {
    projects.value = await listProjectOptions()
  } catch (err) {
    ElMessage.error(errText(err))
  }
})
</script>

<template>
  <div class="knowledge-view">
    <div class="toolbar">
      <h2>知识库问答</h2>
      <el-space>
        <el-select
          v-model="projectId"
          placeholder="选择项目（问答留痕归属）"
          style="width: 300px"
          @change="onProjectChange"
        >
          <el-option
            v-for="p in projects"
            :key="p.id"
            :label="`${p.project_no} ${p.name}`"
            :value="p.id"
          />
        </el-select>
        <el-button :disabled="!projectId" @click="historyVisible = !historyVisible">
          {{ historyVisible ? '收起历史' : `问答历史（${history.length}）` }}
        </el-button>
      </el-space>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="intro"
      title="RAG 准则问答：答案强制带准则锚点引用（标准号+条款）；知识库未覆盖时明示不作答，杜绝幻觉（硬约束 6）"
    />

    <!-- 对话流：问/答气泡 + 引用卡 -->
    <div class="chat">
      <el-empty
        v-if="!messages.length"
        :description="projectId ? '尚无问答记录，输入执业问题开始提问' : '选择项目后开始提问'"
        :image-size="72"
      />
      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role === '问' ? 'ask' : 'ans'">
        <div class="bubble">
          <div class="meta" v-if="m.role === '问'">我 · {{ m.askedAt ?? '' }}</div>
          <div class="meta" v-else-if="m.askedAt">
            留痕回放 · {{ m.operator }} · {{ m.askedAt }}
            <el-tag v-if="m.runNo" size="small" type="info" effect="plain" class="run-tag">{{ m.runNo }}</el-tag>
          </div>
          <div class="text">{{ m.text }}</div>
          <template v-if="m.role === '答' && m.citations?.length">
            <el-divider class="cite-divider">准则引用（红线 6：无引用不出答案）</el-divider>
            <div v-for="(c, j) in m.citations" :key="j" class="citation">
              <el-tag size="small" type="success" effect="plain">
                {{ c.source }} · {{ c.standard_no ?? '' }} {{ c.clause ?? '' }}
              </el-tag>
              <span class="cite-purpose">{{ c.purpose ?? c.content ?? '' }}</span>
              <el-tag size="small" effect="plain" class="score-tag">{{ fmtScore(c.score) }}</el-tag>
            </div>
          </template>
          <el-alert
            v-else-if="m.role === '答' && m.covered === false"
            type="warning"
            :closable="false"
            show-icon
            class="uncovered"
            title="未覆盖明示：知识库未检索到足够依据，本系统拒绝强行作答（防幻觉）"
          />
        </div>
      </div>
    </div>

    <!-- 提问输入（≤500 字，PRD QaIn 约束） -->
    <div class="composer">
      <el-input
        v-model="question"
        type="textarea"
        :rows="2"
        maxlength="500"
        show-word-limit
        :disabled="!projectId || asking"
        placeholder="如：函证程序需要关注哪些底线要求？（任一角色均可提问）"
        @keydown.ctrl.enter="ask"
      />
      <el-button type="primary" :loading="asking" :disabled="!canAsk" class="ask-btn" @click="ask">
        提问
      </el-button>
    </div>

    <!-- 问答历史抽屉（append-only 留痕，全程可追溯） -->
    <el-drawer v-model="historyVisible" title="问答历史（留痕回放）" size="560px">
      <el-table :data="history" size="small">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="expand-body">
              <div class="expand-label">答案</div>
              <div>{{ row.answer }}</div>
              <template v-if="row.citations?.length">
                <div class="expand-label">引用</div>
                <div v-for="(c, j) in row.citations" :key="j" class="citation">
                  <el-tag size="small" type="success" effect="plain">
                    {{ c.source }} · {{ c.standard_no ?? '' }} {{ c.clause ?? '' }}
                  </el-tag>
                  <span class="cite-purpose">{{ c.purpose ?? c.content ?? '' }}</span>
                </div>
              </template>
              <div v-if="row.run_no" class="expand-label">留痕 run：{{ row.run_no }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="question" label="问题" min-width="180" show-overflow-tooltip />
        <el-table-column label="覆盖" width="80">
          <template #default="{ row }">
            <el-tag :type="row.covered ? 'success' : 'warning'" size="small">
              {{ row.covered ? '已覆盖' : '未覆盖' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="operator" label="提问人" width="90" />
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ row.asked_at?.slice(0, 19).replace('T', ' ') ?? '—' }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!history.length" description="暂无问答留痕" :image-size="48" />
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
.intro {
  margin-bottom: 12px;
}
.chat {
  max-width: 860px;
  margin: 0 auto;
}
.msg {
  display: flex;
  margin-bottom: 12px;
}
.msg.ask {
  justify-content: flex-end;
}
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color-lighter);
}
.msg.ask .bubble {
  background: var(--el-color-primary-light-9);
  border-color: var(--el-color-primary-light-7);
}
.meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.run-tag {
  margin-left: 6px;
}
.text {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.cite-divider {
  margin: 8px 0 4px;
}
.cite-divider :deep(.el-divider__text) {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.citation {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 13px;
}
.cite-purpose {
  color: var(--el-text-color-regular);
}
.score-tag {
  margin-left: auto;
}
.uncovered {
  margin-top: 8px;
}
.composer {
  position: sticky;
  bottom: 0;
  display: flex;
  gap: 8px;
  align-items: flex-end;
  max-width: 860px;
  margin: 8px auto 0;
  padding: 10px;
  background: var(--el-bg-color);
  border-top: 1px solid var(--el-border-color-lighter);
}
.ask-btn {
  height: 54px;
}
.expand-body {
  padding: 4px 12px;
  font-size: 13px;
  line-height: 1.6;
}
.expand-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin: 6px 0 2px;
}
</style>
