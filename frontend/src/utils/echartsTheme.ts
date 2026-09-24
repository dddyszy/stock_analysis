import * as echarts from 'echarts'

export const COLORS = {
  primary: '#2f6bff',
  up: '#e5383b',
  down: '#12a150',
  warn: '#f59e0b',
  text: '#111827',
  text2: '#4b5563',
  text3: '#8a93a3',
  border: '#e6e9ef',
  grid: '#f0f2f6',
  palette: ['#2f6bff', '#f59e0b', '#8b5cf6', '#14b8a6', '#ef4444', '#64748b', '#ec4899', '#22c55e'],
}

// canvas 不认 CSS 变量，这里写字面量
export const FONT = "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"

const axis = {
  axisLine: { lineStyle: { color: COLORS.border } },
  axisTick: { show: false },
  axisLabel: { color: COLORS.text3, fontSize: 11 },
  splitLine: { lineStyle: { color: COLORS.grid } },
}

let registered = false

export function registerTheme() {
  if (registered) return
  registered = true
  echarts.registerTheme('chanLight', {
    color: COLORS.palette,
    backgroundColor: 'transparent',
    textStyle: { fontFamily: FONT, color: COLORS.text2 },
    title: { textStyle: { color: COLORS.text, fontSize: 13, fontWeight: 600 } },
    legend: { textStyle: { color: COLORS.text2, fontSize: 12 }, itemWidth: 14, itemHeight: 8 },
    tooltip: {
      backgroundColor: 'rgba(255,255,255,0.97)',
      borderColor: COLORS.border,
      borderWidth: 1,
      textStyle: { color: COLORS.text, fontSize: 12 },
      extraCssText: 'box-shadow: 0 8px 24px rgba(16,24,40,.1); border-radius: 8px;',
    },
    categoryAxis: axis,
    valueAxis: axis,
    timeAxis: axis,
    line: { symbolSize: 4, smooth: false, lineStyle: { width: 1.6 } },
    bar: { itemStyle: { borderRadius: [3, 3, 0, 0] } },
  })
}

export const upDownColor = (v: number) => (v >= 0 ? COLORS.up : COLORS.down)

/** 涨跌幅映射到红绿渐变，用于热力图。 */
export function heatColor(pct: number, span = 5) {
  const t = Math.max(-1, Math.min(1, pct / span))
  const lerp = (a: number, b: number, k: number) => Math.round(a + (b - a) * k)
  if (t >= 0) {
    return `rgb(${lerp(245, 229, t)},${lerp(246, 56, t)},${lerp(248, 59, t)})`
  }
  const k = -t
  return `rgb(${lerp(245, 18, k)},${lerp(246, 161, k)},${lerp(248, 80, k)})`
}
