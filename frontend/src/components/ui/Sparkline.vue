<script setup lang="ts">
import { useChart } from '@/composables/useChart'
import { COLORS } from '@/utils/echartsTheme'

const props = withDefaults(defineProps<{ data: number[]; height?: string; color?: string; area?: boolean }>(), {
  height: '36px',
  area: true,
})

const chart = useChart(
  () => {
    if (!props.data?.length) return null
    const first = props.data[0]
    const last = props.data[props.data.length - 1]
    const color = props.color || (last >= first ? COLORS.up : COLORS.down)
    return {
      animation: false,
      grid: { left: 0, right: 0, top: 2, bottom: 2 },
      xAxis: { type: 'category', show: false, data: props.data.map((_, i) => i) },
      yAxis: { type: 'value', show: false, scale: true },
      series: [
        {
          type: 'line',
          data: props.data,
          symbol: 'none',
          lineStyle: { width: 1.5, color },
          areaStyle: props.area
            ? { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: color + '33' }, { offset: 1, color: color + '00' }] } }
            : undefined,
        },
      ],
    }
  },
  () => [props.data, props.color],
)
</script>

<template>
  <div :ref="chart.el" :style="{ width: '100%', height }" />
</template>
