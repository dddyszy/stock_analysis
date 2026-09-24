<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ label?: string; value: number | null | undefined; max?: number; color?: string; suffix?: string; digits?: number; labelWidth?: string }>(),
  { max: 100, color: 'var(--c-primary)', suffix: '', digits: 0, labelWidth: '56px' },
)

const pct = computed(() => Math.max(0, Math.min(100, ((props.value ?? 0) / props.max) * 100)))
const text = computed(() => (props.value == null ? '--' : `${Number(props.value).toFixed(props.digits)}${props.suffix}`))
</script>

<template>
  <div class="score-bar">
    <span v-if="label" class="label" :style="{ width: labelWidth }">{{ label }}</span>
    <div class="track"><div class="fill" :style="{ width: `${pct}%`, background: color }" /></div>
    <span class="value num">{{ text }}</span>
  </div>
</template>

<style scoped>
.score-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}
.label {
  color: var(--c-text-3);
  flex-shrink: 0;
}
.track {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: #eef1f5;
  overflow: hidden;
  min-width: 40px;
}
.fill {
  height: 100%;
  border-radius: 3px;
}
.value {
  min-width: 36px;
  text-align: right;
  color: var(--c-text-2);
}
</style>
