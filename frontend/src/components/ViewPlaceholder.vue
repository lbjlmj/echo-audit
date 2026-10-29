<script setup lang="ts">
/** 视图占位：标题/职责来自 PRD §12，业务组件随 FR 域落位。 */
defineProps<{ title: string; description: string }>()
</script>

<template>
  <el-card>
    <template #header>
      <b>{{ title }}</b>
    </template>
    <el-empty :description="description" />
  </el-card>
</template>
