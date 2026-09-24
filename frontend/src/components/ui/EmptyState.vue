<script setup lang="ts">
withDefaults(defineProps<{ title?: string; description?: string; icon?: string }>(), {
  title: '暂无数据',
  icon: 'DataAnalysis',
})
</script>

<template>
  <div class="empty">
    <div class="icon"><el-icon :size="26"><component :is="icon" /></el-icon></div>
    <div class="title">{{ title }}</div>
    <div v-if="description" class="desc">{{ description }}</div>
    <div v-if="$slots.default" class="actions"><slot /></div>
  </div>
</template>

<style scoped>
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 36px 16px;
  text-align: center;
}
.icon {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--c-primary-soft);
  color: var(--c-primary);
}
.title {
  margin-top: 12px;
  font-weight: 600;
  color: var(--c-text);
}
.desc {
  margin-top: 4px;
  color: var(--c-text-3);
  font-size: 12px;
  max-width: 420px;
  line-height: 1.6;
}
.actions {
  margin-top: 14px;
  display: flex;
  gap: 8px;
}
</style>
