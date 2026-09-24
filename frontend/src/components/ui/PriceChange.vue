<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ price?: number | null; pct?: number | null; change?: number | null; size?: 'sm' | 'md' | 'lg'; digits?: number }>(),
  { size: 'md', digits: 2 },
)

const cls = computed(() => {
  const v = props.pct ?? props.change ?? 0
  return v > 0 ? 'up' : v < 0 ? 'down' : ''
})
const fmt = (v: number | null | undefined, d: number) => (v === null || v === undefined || Number.isNaN(v) ? '--' : v.toFixed(d))
const sign = (v: number | null | undefined) => (v && v > 0 ? '+' : '')
</script>

<template>
  <span class="price-change num" :class="[cls, size]">
    <span v-if="price !== undefined" class="price">{{ fmt(price, digits) }}</span>
    <span v-if="change !== undefined && change !== null" class="chg">{{ sign(change) }}{{ fmt(change, digits) }}</span>
    <span v-if="pct !== undefined" class="pct">{{ sign(pct) }}{{ fmt(pct, 2) }}%</span>
  </span>
</template>

<style scoped>
.price-change {
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  white-space: nowrap;
}
.sm {
  font-size: 12px;
}
.md .price {
  font-size: 15px;
  font-weight: 600;
}
.lg {
  gap: 12px;
}
.lg .price {
  font-size: 30px;
  font-weight: 700;
  line-height: 1;
  margin-right: 4px;
}
.lg .chg,
.lg .pct {
  font-size: 15px;
  font-weight: 600;
}
</style>
