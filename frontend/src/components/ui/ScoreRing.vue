<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{ value: number | null | undefined; size?: number; stroke?: number; label?: string; max?: number }>(), {
  size: 64,
  stroke: 6,
  max: 100,
})

const r = computed(() => (props.size - props.stroke) / 2)
const circ = computed(() => 2 * Math.PI * r.value)
const ratio = computed(() => Math.max(0, Math.min(1, (props.value ?? 0) / props.max)))
const color = computed(() => {
  const v = ratio.value * 100
  if (v >= 70) return 'var(--c-up)'
  if (v >= 50) return 'var(--c-warn)'
  return 'var(--c-info)'
})
</script>

<template>
  <div class="score-ring" :style="{ width: `${size}px`, height: `${size}px` }">
    <svg :width="size" :height="size">
      <circle :cx="size / 2" :cy="size / 2" :r="r" :stroke-width="stroke" class="track" fill="none" />
      <circle
        :cx="size / 2"
        :cy="size / 2"
        :r="r"
        :stroke-width="stroke"
        fill="none"
        stroke-linecap="round"
        :stroke="color"
        :stroke-dasharray="`${circ * ratio} ${circ}`"
        :transform="`rotate(-90 ${size / 2} ${size / 2})`"
      />
    </svg>
    <div class="center">
      <div class="v num" :style="{ fontSize: `${Math.round(size * 0.28)}px` }">{{ value == null ? '--' : Math.round(value) }}</div>
      <div v-if="label" class="l">{{ label }}</div>
    </div>
  </div>
</template>

<style scoped>
.score-ring {
  position: relative;
  flex-shrink: 0;
}
.track {
  stroke: #eef1f5;
}
.center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  line-height: 1.1;
}
.v {
  font-weight: 700;
  color: var(--c-text);
}
.l {
  font-size: 10px;
  color: var(--c-text-3);
  margin-top: 2px;
}
</style>
