<script setup lang="ts">
import { computed } from 'vue'
import { SIGNAL_NAMES } from '@/utils/format'

const props = withDefaults(
  defineProps<{ type: string; confirmed?: boolean; invalid?: boolean; date?: string; size?: 'sm' | 'md'; scope?: string; strong?: boolean }>(),
  { confirmed: true, invalid: false, size: 'md', scope: 'bi', strong: false },
)

const buy = computed(() => props.type?.startsWith('B'))
const state = computed(() => (props.invalid ? 'invalid' : props.confirmed ? 'confirmed' : 'pending'))
const title = computed(() => {
  const st = { invalid: '已失效', confirmed: '已确认', pending: '未确认（所在笔或线段还在延伸）' }[state.value]
  return `${props.scope === 'seg' ? '线段级别' : '笔级别'} · ${st}${props.strong ? ' · 多指标强背驰' : ''}`
})
</script>

<template>
  <span class="signal-badge" :class="[buy ? 'buy' : 'sell', state, size]" :title="title">
    <span class="dot" />
    <span v-if="scope === 'seg'" class="scope">段</span>{{ SIGNAL_NAMES[type] || type }}<span v-if="state === 'pending'">?</span>
    <span v-if="strong" class="strong" title="多指标强背驰">强</span>
    <span v-if="date" class="date">{{ date.slice(5) }}</span>
  </span>
</template>

<style scoped>
.signal-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border-radius: 999px;
  padding: 2px 9px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  border: 1px solid transparent;
}
.sm {
  padding: 1px 7px;
  font-size: 11px;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}
.buy {
  color: var(--c-up);
  background: var(--c-up-soft);
}
.sell {
  color: var(--c-down);
  background: var(--c-down-soft);
}
.pending {
  background: transparent;
  border-style: dashed;
  border-color: currentColor;
}
.invalid {
  color: var(--c-text-3);
  background: #f1f3f6;
  text-decoration: line-through;
}
.scope {
  font-size: 10px;
  padding: 0 3px;
  border-radius: 3px;
  background: #8b5cf6;
  color: #fff;
  margin-right: 1px;
}
.strong {
  font-size: 10px;
  padding: 0 3px;
  border-radius: 3px;
  border: 1px solid currentColor;
  line-height: 1.2;
}
.date {
  font-weight: 400;
  opacity: 0.8;
  font-family: var(--font-num);
}
</style>
