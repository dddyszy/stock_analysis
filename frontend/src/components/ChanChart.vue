<script setup lang="ts">
import { useChart } from '@/composables/useChart'
import { COLORS } from '@/utils/echartsTheme'
import { SIGNAL_NAMES } from '@/utils/format'

interface PriceLine {
  name: string
  value: number
  color?: string
}

export interface ChanLayers {
  bi: boolean
  segment: boolean
  zhongshu: boolean
  signals: boolean
  volume: boolean
  macd: boolean
}

const props = withDefaults(
  defineProps<{ data: any; lines?: PriceLine[]; height?: string; layers?: Partial<ChanLayers>; zoomBars?: number }>(),
  { lines: () => [], height: '600px', layers: () => ({}), zoomBars: 160 },
)

const BI = '#2f6bff'
const SEG = '#8b5cf6'

function layer(k: keyof ChanLayers) {
  return props.layers[k] ?? true
}

function buildOption() {
  const d = props.data
  if (!d?.bars?.length) return null
  const dates: string[] = d.bars.map((b: any[]) => b[0])
  const dateSet = new Set(dates)
  const first = dates[0]
  const kdata = d.bars.map((b: any[]) => [b[1], b[4], b[3], b[2]])
  const clampDate = (x: string) => (x < first ? first : x)
  // 起点在可见窗口之前的连线，按时间比例插值出窗口第一天的价格
  const startPoint = (s: any) => {
    if (dateSet.has(s.start_dt)) return [s.start_dt, s.start_price]
    const t0 = Date.parse(s.start_dt)
    const t1 = Date.parse(s.end_dt)
    const ratio = t1 > t0 ? (Date.parse(first) - t0) / (t1 - t0) : 0
    return [first, +(s.start_price + (s.end_price - s.start_price) * ratio).toFixed(3)]
  }
  const polyline = (items: any[]) => {
    const pts: any[] = []
    items.forEach((s: any, i: number) => {
      if (i === 0) pts.push(startPoint(s))
      if (dateSet.has(s.end_dt)) pts.push([s.end_dt, s.end_price])
    })
    return pts
  }

  const bis = (d.bis || []).filter((b: any) => b.end_dt >= first)
  const biPoints = polyline(bis.filter((b: any) => b.confirmed))
  const lastBi = bis.find((b: any) => !b.confirmed)
  const lastBiPoints = lastBi ? [startPoint(lastBi), [lastBi.end_dt, lastBi.end_price]] : []
  const segPoints = polyline((d.segments || []).filter((s: any) => s.end_dt >= first))

  const zsAreas = (d.zhongshus || [])
    .filter((z: any) => z.end_dt >= first && (layer('segment') || z.level === 'bi'))
    .map((z: any) => [
      {
        xAxis: clampDate(z.start_dt),
        yAxis: z.zd,
        itemStyle: {
          color: z.level === 'seg' ? 'rgba(139,92,246,0.05)' : 'rgba(245,158,11,0.12)',
          borderColor: z.level === 'seg' ? 'rgba(139,92,246,0.55)' : 'rgba(245,158,11,0.75)',
          borderWidth: 1,
          borderType: z.level === 'seg' ? 'dashed' : 'solid',
        },
      },
      { xAxis: z.end_dt, yAxis: z.zg },
    ])

  const signals = (d.signals || []).filter((s: any) => dateSet.has(s.dt))
  const markPoints = signals.map((s: any) => {
    const buy = s.type.startsWith('B')
    const invalid = s.extra?.invalidated
    const color = invalid ? '#b8bfca' : buy ? COLORS.up : COLORS.down
    return {
      name: s.type,
      coord: [s.dt, s.price],
      value: `${s.scope === 'seg' ? '段' : ''}${SIGNAL_NAMES[s.type]}${s.confirmed ? '' : '?'}`,
      symbol: 'pin',
      symbolSize: s.scope === 'seg' ? 46 : 40,
      symbolRotate: buy ? 180 : 0,
      symbolOffset: [0, buy ? 22 : -22],
      itemStyle: { color, opacity: s.confirmed ? 1 : 0.6, borderColor: s.scope === 'seg' ? '#8b5cf6' : undefined, borderWidth: s.scope === 'seg' ? 2 : 0 },
      label: { color: '#fff', fontSize: 10, fontWeight: 600, offset: [0, buy ? 4 : -2] },
    }
  })

  const divergenceAreas = signals
    .filter((s: any) => (s.type === 'B1' || s.type === 'S1') && s.extra?.divergence)
    .map((s: any) => {
      const range = s.extra?.leave_range
      if (!range) return null
      return [
        { xAxis: clampDate(range[0]), itemStyle: { color: s.type === 'B1' ? 'rgba(229,56,59,0.08)' : 'rgba(18,161,80,0.08)' } },
        { xAxis: range[1] },
      ]
    })
    .filter(Boolean)

  const markLines = props.lines.map((l) => ({
    name: l.name,
    yAxis: l.value,
    lineStyle: { color: l.color || COLORS.text3, type: 'dashed', width: 1 },
    label: { formatter: `${l.name} ${Number(l.value).toFixed(2)}`, position: 'insideEndTop', color: l.color || COLORS.text2, fontSize: 11 },
  }))

  const macd = d.macd || { dif: [], dea: [], hist: [] }
  const showVol = layer('volume')
  const showMacd = layer('macd')
  // 主图、成交量、MACD 的纵向布局
  const mainH = showVol && showMacd ? 60 : showVol || showMacd ? 72 : 86
  const grids: any[] = [{ left: 16, right: 64, top: '2%', height: `${mainH}%` }]
  let top = 2 + mainH + 3
  const volIdx = showVol ? grids.push({ left: 16, right: 64, top: `${top}%`, height: '10%' }) - 1 : -1
  if (showVol) top += 13
  const macdIdx = showMacd ? grids.push({ left: 16, right: 64, top: `${top}%`, height: '12%' }) - 1 : -1
  const allIdx = grids.map((_, i) => i)

  const xAxes = grids.map((_, i) => ({
    type: 'category',
    gridIndex: i,
    data: dates,
    boundaryGap: true,
    axisLabel: { show: i === grids.length - 1 },
    axisLine: { lineStyle: { color: COLORS.border } },
    splitLine: { show: false },
    min: 'dataMin',
    max: 'dataMax',
  }))
  const yAxes = grids.map((_, i) =>
    i === 0
      ? { scale: true, gridIndex: 0, position: 'right', splitNumber: 5 }
      : { scale: true, gridIndex: i, position: 'right', splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
  )
  const startPct = dates.length > props.zoomBars ? Math.max(0, 100 - (props.zoomBars / dates.length) * 100) : 0

  const series: any[] = [
    {
      name: 'K线',
      type: 'candlestick',
      data: kdata,
      barMaxWidth: 12,
      itemStyle: { color: COLORS.up, color0: COLORS.down, borderColor: COLORS.up, borderColor0: COLORS.down },
      markArea: { silent: true, data: layer('zhongshu') ? zsAreas : [] },
      markPoint: { data: layer('signals') ? markPoints : [] },
      markLine: { symbol: 'none', silent: true, data: markLines },
    },
  ]
  if (layer('bi')) {
    series.push(
      { name: '笔', type: 'line', data: biPoints, symbol: 'circle', symbolSize: 3, lineStyle: { color: BI, width: 1.4 }, itemStyle: { color: BI }, z: 5 },
      { name: '笔', type: 'line', data: lastBiPoints, symbol: 'none', lineStyle: { color: BI, width: 1.4, type: 'dashed' }, z: 5 },
    )
  }
  if (layer('segment')) {
    series.push({ name: '线段', type: 'line', data: segPoints, symbol: 'none', lineStyle: { color: SEG, width: 2.4, opacity: 0.85 }, z: 4 })
  }
  if (showVol) {
    series.push({
      name: '成交量',
      type: 'bar',
      xAxisIndex: volIdx,
      yAxisIndex: volIdx,
      barMaxWidth: 12,
      data: d.bars.map((b: any[]) => ({ value: b[5], itemStyle: { color: b[4] >= b[1] ? 'rgba(229,56,59,0.55)' : 'rgba(18,161,80,0.55)' } })),
    })
  }
  if (showMacd) {
    series.push(
      {
        name: 'MACD',
        type: 'bar',
        xAxisIndex: macdIdx,
        yAxisIndex: macdIdx,
        barMaxWidth: 6,
        data: macd.hist.map((v: number) => ({ value: v, itemStyle: { color: v >= 0 ? COLORS.up : COLORS.down } })),
        markArea: { silent: true, data: divergenceAreas },
      },
      { name: 'DIF', type: 'line', xAxisIndex: macdIdx, yAxisIndex: macdIdx, data: macd.dif, symbol: 'none', lineStyle: { width: 1, color: '#f59e0b' } },
      { name: 'DEA', type: 'line', xAxisIndex: macdIdx, yAxisIndex: macdIdx, data: macd.dea, symbol: 'none', lineStyle: { width: 1, color: '#2f6bff' } },
    )
  }

  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: '#c3cad6' }, crossStyle: { color: '#c3cad6' }, label: { backgroundColor: '#4b5563' } },
      formatter: (params: any[]) => {
        const k = params.find((p) => p.seriesName === 'K线')
        if (!k) return ''
        const i = k.dataIndex
        const b = d.bars[i]
        const prev = i > 0 ? d.bars[i - 1][4] : b[1]
        const chg = ((b[4] / prev - 1) * 100).toFixed(2)
        const color = b[4] >= prev ? COLORS.up : COLORS.down
        const sig = signals
          .filter((s: any) => s.dt === b[0])
          .map((s: any) => `<div style="margin-top:4px"><b style="color:${s.type.startsWith('B') ? COLORS.up : COLORS.down}">${s.scope === 'seg' ? '线段' : '笔'}${SIGNAL_NAMES[s.type]}</b> ${s.desc}</div>`)
          .join('')
        return `<div style="font-weight:600;margin-bottom:4px">${b[0]}</div>` +
          `开 ${b[1]}　高 ${b[2]}<br/>低 ${b[3]}　收 <b style="color:${color}">${b[4]}（${chg}%）</b>` +
          `<br/><span style="color:${COLORS.text3}">量 ${b[5]}　MACD ${macd.hist[i] ?? '--'}</span>${sig}`
      },
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: allIdx, start: startPct, end: 100 },
      { type: 'slider', xAxisIndex: allIdx, start: startPct, end: 100, bottom: 4, height: 16, borderColor: COLORS.border, fillerColor: 'rgba(47,107,255,0.08)' },
    ],
    series,
  }
}

const chart = useChart(buildOption, () => [props.data, props.lines, props.layers, props.zoomBars])
</script>

<template>
  <div :ref="chart.el" :style="{ width: '100%', height }" />
</template>
