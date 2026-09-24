<script setup lang="ts">
import { computed } from 'vue'

/**
 * 把止损、成本/现价、目标一、目标二画在同一条价格轴上。
 * 左侧绿色是风险区（止损到参考价），右侧红色是收益区（参考价到目标）。
 */
const props = withDefaults(
  defineProps<{
    stop: number | null | undefined
    price: number | null | undefined
    target1?: number | null
    target2?: number | null
    entry?: number | null
    showLabels?: boolean
  }>(),
  { showLabels: true },
)

const basePrice = computed(() => props.entry ?? props.price ?? null)
const points = computed(() => {
  const vals = [props.stop, props.price, props.entry, props.target1, props.target2].filter((v): v is number => v != null && !Number.isNaN(v))
  if (vals.length < 2) return null
  const lo = Math.min(...vals)
  const hi = Math.max(...vals)
  const span = hi - lo || 1
  const pos = (v: number | null | undefined) => (v == null ? null : ((v - lo) / span) * 100)
  return {
    stop: pos(props.stop),
    price: pos(props.price),
    entry: pos(props.entry),
    t1: pos(props.target1),
    t2: pos(props.target2),
    ref: pos(basePrice.value),
  }
})
const rr = computed(() => {
  if (props.stop == null || basePrice.value == null || props.target1 == null) return null
  const risk = basePrice.value - props.stop
  return risk > 0 ? (props.target1 - basePrice.value) / risk : null
})
const fmt = (v: number | null | undefined) => (v == null ? '--' : v.toFixed(2))
</script>

<template>
  <div class="rr">
    <div v-if="points" class="axis">
      <div
        v-if="points.stop != null && points.ref != null"
        class="zone risk"
        :style="{ left: `${Math.min(points.stop, points.ref)}%`, width: `${Math.abs(points.ref - points.stop)}%` }"
      />
      <div
        v-if="points.ref != null && (points.t2 ?? points.t1) != null"
        class="zone reward"
        :style="{ left: `${points.ref}%`, width: `${Math.max(0, (points.t2 ?? points.t1 ?? 0) - points.ref)}%` }"
      />
      <span v-if="points.stop != null" class="tick stop" :style="{ left: `${points.stop}%` }" />
      <span v-if="points.entry != null" class="tick entry" :style="{ left: `${points.entry}%` }" />
      <span v-if="points.t1 != null" class="tick t" :style="{ left: `${points.t1}%` }" />
      <span v-if="points.t2 != null" class="tick t2" :style="{ left: `${points.t2}%` }" />
      <span v-if="points.price != null" class="now" :style="{ left: `${points.price}%` }" />
    </div>
    <div v-if="showLabels" class="labels num">
      <span class="down">止损 {{ fmt(stop) }}</span>
      <span v-if="entry != null">成本 {{ fmt(entry) }}</span>
      <span>现价 {{ fmt(price) }}</span>
      <span class="up">目标 {{ fmt(target1) }}<template v-if="target2"> / {{ fmt(target2) }}</template></span>
      <span v-if="rr != null" class="rr-v" :class="rr >= 2 ? 'good' : 'weak'">盈亏比 {{ rr.toFixed(1) }}</span>
    </div>
  </div>
</template>

<style scoped>
.rr {
  width: 100%;
}
.axis {
  position: relative;
  height: 10px;
  border-radius: 5px;
  background: #eef1f5;
  margin: 6px 0;
}
.zone {
  position: absolute;
  top: 0;
  bottom: 0;
  border-radius: 5px;
}
.risk {
  background: rgba(18, 161, 80, 0.28);
}
.reward {
  background: rgba(229, 56, 59, 0.22);
}
.tick {
  position: absolute;
  top: -3px;
  width: 2px;
  height: 16px;
  margin-left: -1px;
  border-radius: 1px;
}
.stop {
  background: var(--c-down);
}
.entry {
  background: var(--c-text-2);
}
.t {
  background: var(--c-up);
}
.t2 {
  background: rgba(229, 56, 59, 0.5);
}
.now {
  position: absolute;
  top: -4px;
  width: 12px;
  height: 18px;
  margin-left: -6px;
  border-radius: 4px;
  background: var(--c-primary);
  border: 2px solid #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
}
.labels {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  font-size: 11px;
  color: var(--c-text-2);
}
.rr-v {
  font-weight: 600;
}
.good {
  color: var(--c-up);
}
.weak {
  color: var(--c-text-3);
}
</style>
