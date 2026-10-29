<script setup lang="ts">
/**
 * 统一入口 audit-hub（PRD §12.1）：登录 + 中文命令面板（触发词直达各模块）+
 * 项目一览与八阶段门状态一览。项目发起 = 「立项」命令跳项目工作台；
 * 分发随立项审批流（FR-01-02 拟任团队），Phase 1 不做独立分发面板。
 * 权限以后端为准：命令按 /auth/me 权限矩阵过滤，越权由后端 403 拦截。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useSessionStore } from '@/stores/session'
import { errText } from '@/api/client'
import { listProjects } from '@/api/project'
import { listGates } from '@/api/quality'
import type { GateOut, ProjectOut } from '@/api/types'

const session = useSessionStore()
const router = useRouter()

const loginName = ref('')
const password = ref('')
const loading = ref(false)

async function onLogin() {
  if (!loginName.value || !password.value) {
    ElMessage.warning('请输入账号与密码')
    return
  }
  loading.value = true
  try {
    await session.login(loginName.value, password.value)
    ElMessage.success(`欢迎，${session.userName}（${session.globalRole}）`)
    await loadProjects()
  } catch (err) {
    ElMessage.error(errText(err))
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------------------
// 中文命令面板（§12.1 核心要素：中文触发词直达各模块）
// ---------------------------------------------------------------------------
type PermLevel = 'NONE' | 'VIEW' | 'OPERATE' | 'PROPOSE' | 'APPROVE'
interface HubCommand {
  title: string
  keywords: string[] // 中文触发词（PRD §12.1：命令面板按执业语言直达）
  perm: [string, PermLevel]
  go: string
}

const COMMANDS: HubCommand[] = [
  {
    title: '立项申请（项目发起）',
    keywords: ['立项', '发起', '新项目', '承接', '业务承接'],
    perm: ['project.approve', 'PROPOSE'],
    go: '/project',
  },
  { title: '打开项目工作台', keywords: ['项目', '工作台', '看板', '面板'], perm: ['project.approve', 'VIEW'], go: '/project' },
  { title: '打开底稿工作台', keywords: ['底稿', '编制', 'Excel', '工作底稿', '投喂'], perm: ['worksheet.edit', 'VIEW'], go: '/worksheet' },
  { title: '打开复核工作台', keywords: ['复核', '队列', '意见', '双签', '时间线'], perm: ['worksheet.review_sign', 'VIEW'], go: '/review' },
  { title: '打开报告签发', keywords: ['报告', '签发', '出稿', '会签', '附注'], perm: ['worksheet.review_sign', 'VIEW'], go: '/review?tab=rpt' },
  { title: '打开质控驾驶舱', keywords: ['质控', '驾驶舱', '阶段门', '八门', '归档', '检查'], perm: ['archive.access', 'VIEW'], go: '/quality' },
  { title: '打开知识库问答', keywords: ['知识', '问答', '准则', 'RAG', '检索'], perm: ['knowledge.write', 'VIEW'], go: '/knowledge' },
]

interface Suggestion {
  key: string
  title: string
  desc: string
  go: string
}

const keyword = ref('')
const activeIdx = ref(0)

/** 命令 + 项目混合联想：空输入给全量命令，输入后按触发词/标题/项目编号名称过滤 */
const suggestions = computed<Suggestion[]>(() => {
  const kw = keyword.value.trim()
  const cmds = COMMANDS.filter((c) => session.can(c.perm[0], c.perm[1]))
    .filter((c) => !kw || c.keywords.some((k) => k.includes(kw)) || c.title.includes(kw))
    .map((c) => ({ key: `cmd:${c.title}`, title: c.title, desc: `触发词：${c.keywords.join(' / ')}`, go: c.go }))
  const projs = projects.value
    .filter((p) => kw && (p.name.includes(kw) || (p.project_no ?? '').includes(kw)))
    .slice(0, 4)
    .map((p) => ({ key: `prj:${p.id}`, title: `打开项目 ${p.project_no} ${p.name}`, desc: `状态：${p.status}`, go: '/project' }))
  return [...cmds, ...projs]
})

watch(keyword, () => {
  activeIdx.value = 0
})

function moveIdx(delta: number) {
  const total = suggestions.value.length
  if (!total) return
  activeIdx.value = (activeIdx.value + delta + total) % total
}

function runCommand(s: Suggestion) {
  keyword.value = ''
  router.push(s.go)
}

function onPaletteEnter() {
  const total = suggestions.value.length
  if (total) runCommand(suggestions.value[Math.min(activeIdx.value, total - 1)])
}

// ---------------------------------------------------------------------------
// 项目一览与八阶段门状态（§12.1：项目检索 + 八门状态一览；展开行懒加载门况）
// ---------------------------------------------------------------------------
const projects = ref<ProjectOut[]>([])
const gateCache = ref<Record<string, GateOut[]>>({})
const projFilter = ref('')

const filteredProjects = computed(() => {
  const kw = projFilter.value.trim()
  if (!kw) return projects.value
  return projects.value.filter((p) => p.name.includes(kw) || (p.project_no ?? '').includes(kw))
})

async function loadProjects() {
  try {
    projects.value = await listProjects()
  } catch (err) {
    ElMessage.error(errText(err))
  }
}

async function onExpandChange(row: ProjectOut, expandedRows: ProjectOut[]) {
  // el-table expand-change 签名为 (row, expandedRows)；仅展开（非收起）时懒加载门况
  if (gateCache.value[row.id] || !expandedRows.includes(row)) return
  try {
    gateCache.value[row.id] = await listGates(row.id)
  } catch {
    gateCache.value[row.id] = [] // 未审批项目无门数据，静默提示
  }
}

function gateTagType(status: string): 'success' | 'primary' | 'warning' | 'info' {
  if (status === '已通过') return 'success'
  if (status === '进行中') return 'primary'
  if (status === '跳过') return 'warning'
  return 'info'
}

function passedCount(gates?: GateOut[]) {
  return gates?.filter((g) => g.gate_status === '已通过').length ?? 0
}

function goProject(_p: ProjectOut) {
  router.push('/project')
}

onMounted(async () => {
  if (session.isLoggedIn) await loadProjects()
})
</script>

<template>
  <div class="hub">
    <h2>统一入口 audit-hub</h2>
    <p class="hint">命令面板：中文触发词直达各模块、项目检索与八门状态一览（PRD §12.1）</p>

    <el-card v-if="!session.isLoggedIn" class="login-card">
      <template #header>登录</template>
      <el-form label-width="64px" @submit.prevent="onLogin">
        <el-form-item label="账号">
          <el-input v-model="loginName" placeholder="login_name" autofocus />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="password" type="password" show-password />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" native-type="submit" :loading="loading">登录</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <template v-else>
      <!-- 中文命令面板（§12.1：触发词直达各模块；回车执行高亮项，↑↓ 可切换） -->
      <el-card class="palette-card">
        <template #header>
          命令面板
          <span class="hint-inline">输入中文触发词，回车直达</span>
        </template>
        <el-input
          v-model="keyword"
          size="large"
          clearable
          placeholder="如：立项 / 底稿 / 复核 / 报告签发 / 质控 / 知识问答，或项目编号、名称"
          @keyup.enter="onPaletteEnter"
          @keydown.down.prevent="moveIdx(1)"
          @keydown.up.prevent="moveIdx(-1)"
        />
        <div class="cmd-list">
          <div
            v-for="(s, i) in suggestions"
            :key="s.key"
            class="cmd-item"
            :class="{ active: i === activeIdx }"
            @click="runCommand(s)"
            @mouseenter="activeIdx = i"
          >
            <span class="cmd-title">{{ s.title }}</span>
            <span class="cmd-desc">{{ s.desc }}</span>
          </div>
          <div v-if="!suggestions.length" class="cmd-empty">
            无匹配命令或项目（试试：立项 / 底稿 / 复核 / 报告 / 质控 / 知识）
          </div>
        </div>
      </el-card>

      <!-- 项目一览与八阶段门状态（§12.1：项目检索 + 展开行八门一览，懒加载） -->
      <el-card class="search-card">
        <template #header>项目一览 · 展开行查看八阶段门状态</template>
        <el-input v-model="projFilter" placeholder="按项目编号 / 名称过滤" clearable />
        <el-table
          :data="filteredProjects"
          size="small"
          class="result-table"
          row-key="id"
          @expand-change="onExpandChange"
        >
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="gate-row">
                <template v-if="gateCache[row.id]?.length">
                  <span class="muted">
                    已通过 {{ passedCount(gateCache[row.id]) }}/{{ gateCache[row.id].length }}：
                  </span>
                  <el-tag
                    v-for="(g, i) in gateCache[row.id]"
                    :key="g.id"
                    :type="gateTagType(g.gate_status)"
                    effect="plain"
                    size="small"
                  >
                    门{{ i + 1 }} {{ g.stage }}·{{ g.gate_status }}
                  </el-tag>
                </template>
                <span v-else class="muted">暂无阶段门数据（立项审批后初始化八门）</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="project_no" label="项目编号" width="140" />
          <el-table-column prop="name" label="项目名称" />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === '进行中' ? 'success' : 'info'" size="small">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="" width="90">
            <template #default="{ row }">
              <el-button link type="primary" @click="goProject(row)">打开</el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-if="!filteredProjects.length"
          description="无匹配项目"
          :image-size="48"
        />
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.hub {
  max-width: 860px;
}
.hint {
  color: var(--el-text-color-secondary);
  margin-bottom: 16px;
}
.hint-inline {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 400;
  margin-left: 8px;
}
.login-card,
.palette-card,
.search-card {
  margin-bottom: 16px;
}
.cmd-list {
  margin-top: 8px;
  max-height: 320px;
  overflow-y: auto;
}
.cmd-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px;
  border-radius: 6px;
  cursor: pointer;
}
.cmd-item.active {
  background: var(--el-fill-color-light);
}
.cmd-title {
  font-size: 14px;
}
.cmd-desc {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.cmd-empty {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  padding: 12px;
  text-align: center;
}
.result-table {
  margin-top: 12px;
}
.gate-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
