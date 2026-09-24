<script setup lang="ts">
import Sparkline from './Sparkline.vue'

defineProps<{
  label: string
  value: string | number
  sub?: string
  subClass?: string
  valueClass?: string
  trend?: number[]
  hint?: string
}>()
</script>

<template>
  <div class="stat-card">
    <div class="label">
      {{ label }}
      <el-tooltip v-if="hint" :content="hint" placement="top"><el-icon class="hint"><InfoFilled /></el-icon></el-tooltip>
    </div>
    <div class="value num" :class="valueClass">{{ value }}</div>
    <div v-if="sub || $slots.sub" class="sub" :class="subClass"><slot name="sub">{{ sub }}</slot></div>
    <Sparkline v-if="trend?.length" :data="trend" height="34px" class="spark" />
    <slot />
  </div>
</template>

<style scoped>
.stat-card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 14px 16px;
  min-width: 0;
}
.label {
  color: var(--c-text-3);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.hint {
  cursor: help;
}
.value {
  margin-top: 6px;
  font-size: 24px;
  font-weight: 650;
  line-height: 1.2;
  color: var(--c-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sub {
  margin-top: 4px;
  font-size: 12px;
  color: var(--c-text-2);
}
.spark {
  margin-top: 8px;
}
</style>
