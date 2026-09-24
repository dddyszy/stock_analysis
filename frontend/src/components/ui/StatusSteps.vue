<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string; compact?: boolean }>()

const STEPS = [
  { key: 'Initial', label: '初始' },
  { key: 'Breakeven', label: '保本' },
  { key: 'Trailing', label: '跟踪' },
  { key: 'Reduced', label: '减仓' },
  { key: 'Closed', label: '清仓' },
]
const current = computed(() => Math.max(0, STEPS.findIndex((s) => s.key === props.status)))
</script>

<template>
  <div class="steps" :class="{ compact }">
    <template v-for="(s, i) in STEPS" :key="s.key">
      <div class="step" :class="{ done: i < current, active: i === current }">
        <span class="dot" />
        <span v-if="!compact || i === current" class="label">{{ s.label }}</span>
      </div>
      <span v-if="i < STEPS.length - 1" class="line" :class="{ done: i < current }" />
    </template>
  </div>
</template>

<style scoped>
.steps {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
}
.step {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--c-text-3);
  white-space: nowrap;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #dfe3ea;
}
.done .dot {
  background: var(--el-color-primary-light-5);
}
.active {
  color: var(--c-primary);
  font-weight: 600;
}
.active .dot {
  background: var(--c-primary);
  box-shadow: 0 0 0 3px var(--c-primary-soft);
}
.line {
  flex: 1;
  min-width: 10px;
  height: 2px;
  background: #e8ebf0;
  border-radius: 1px;
}
.line.done {
  background: var(--el-color-primary-light-5);
}
.compact .line {
  min-width: 6px;
}
</style>
