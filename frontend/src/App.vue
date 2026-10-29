<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="logo">Echo 审计作业平台</div>
      <!-- 六个界面模块严格对应 PRD §12；菜单显隐由后端权限驱动，前端仅兜底（AGENTS.md 硬约束 5） -->
      <el-menu :default-active="$route.path" router>
        <el-menu-item index="/hub">统一入口 audit-hub</el-menu-item>
        <el-menu-item v-if="session.can('project.approve', 'VIEW')" index="/project">
          项目工作台
        </el-menu-item>
        <el-menu-item v-if="session.can('worksheet.edit', 'VIEW')" index="/worksheet">
          底稿工作台
        </el-menu-item>
        <el-menu-item v-if="session.can('worksheet.review_sign', 'VIEW')" index="/review">
          复核工作台
        </el-menu-item>
        <el-menu-item v-if="session.can('archive.access', 'VIEW')" index="/quality">
          质控驾驶舱
        </el-menu-item>
        <el-menu-item v-if="session.can('knowledge.write', 'VIEW')" index="/knowledge">
          知识库问答
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar" height="48px">
        <span class="who">
          {{ session.userName || '未登录' }}
          <el-tag v-if="session.globalRole" size="small" type="info">{{ session.globalRole }}</el-tag>
        </span>
        <el-button v-if="session.isLoggedIn" link type="danger" @click="onLogout">登出</el-button>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const router = useRouter()

// 登录过则拉取会话（刷新后恢复权限矩阵）
if (session.token && !session.userId) {
  session.fetchMe().catch(() => session.logout())
}

async function onLogout() {
  await session.logout()
  router.push('/hub')
}
</script>

<style scoped>
.layout {
  height: 100vh;
}
.aside {
  border-right: 1px solid var(--el-border-color-light);
}
.logo {
  font-weight: 700;
  padding: 16px;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  border-bottom: 1px solid var(--el-border-color-light);
}
.who {
  display: flex;
  align-items: center;
  gap: 8px;
}
</style>
