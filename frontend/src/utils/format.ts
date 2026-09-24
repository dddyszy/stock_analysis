export const SIGNAL_NAMES: Record<string, string> = { B1: '一买', B2: '二买', B3: '三买', S1: '一卖', S2: '二卖', S3: '三卖' }

export const WALK_NAMES: Record<string, string> = {
  up_trend: '上涨趋势',
  down_trend: '下跌趋势',
  consolidation: '盘整',
  unknown: '未知',
}

export const POSITION_NAMES: Record<string, string> = { above: '中枢上方', inside: '中枢内部', below: '中枢下方', unknown: '未知' }

export const REGIME_COLORS: Record<string, string> = { strong: '#e53935', neutral: '#f5a623', weak: '#1e9e57' }

export const ACTION_TYPES: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'primary'> = {
  hold: 'info',
  add: 'danger',
  reduce: 'warning',
  close: 'success',
}

export function num(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '--'
  return Number(v).toFixed(digits)
}

export function pct(v: unknown, digits = 2, ratio = false): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--'
  const n = ratio ? Number(v) * 100 : Number(v)
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`
}

/** 已经是百分数的值加上 %，缺失时显示 --（不带正负号，适合 ROE、负债率等）。 */
export function percent(v: unknown, digits = 1): string {
  if (v === null || v === undefined || v === '' || Number.isNaN(Number(v))) return '--'
  return `${Number(v).toFixed(digits)}%`
}

export function ratioPct(v: unknown, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--'
  return `${(Number(v) * 100).toFixed(digits)}%`
}

export function money(v: unknown): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '--'
  const n = Number(v)
  if (Math.abs(n) >= 1e8) return `${(n / 1e8).toFixed(2)} 亿`
  if (Math.abs(n) >= 1e4) return `${(n / 1e4).toFixed(2)} 万`
  return n.toFixed(2)
}

export function colorClass(v: unknown): string {
  const n = Number(v)
  if (!n || Number.isNaN(n)) return ''
  return n > 0 ? 'up' : 'down'
}

export function signalTagType(t: string): 'danger' | 'success' {
  return t?.startsWith('B') ? 'danger' : 'success'
}

export function dt(v?: string | null): string {
  if (!v) return '--'
  return v.replace('T', ' ').slice(0, 16)
}
