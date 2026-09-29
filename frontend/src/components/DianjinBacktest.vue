<script setup lang="ts">
import { computed } from 'vue'
import { useChart } from '@/composables/useChart'
import { COLORS } from '@/utils/echartsTheme'
import { colorClass } from '@/utils/format'
import { SectionCard, StatCard } from '@/components/ui'

const props = defineProps<{ result: any; vkey: string }>()

const v = computed(() => props.result?.variants?.[props.vkey])
const base = computed(() => v.value?.exits?.base)
const bench = computed(() => v.value?.benchmarks || {})
const rnd = computed(() => base.value?.random)
const years = computed(() => Object.keys(base.value?.yearly || {}))
const compareRows = computed(() =>
  ['v2', 'v1_short', 'roe_only', 'dy_only', 'v1']
    .map((k) => ({ key: k, ...props.result?.variants?.[k] }))
    .filter((r) => r.exits)
    .map((r) => ({ ...r.exits.base, key: r.key, name: r.name, start: r.start })),
)
const exitRows = computed(() => Object.entries(v.value?.exits || {}).map(([k, m]: any) => ({ key: k, ...m })))

const pct = (x: number | null | undefined) => (x == null ? '--' : `${x > 0 ? '+' : ''}${x.toFixed(1)}%`)
const plain = (x: number | null | undefined) => (x == null ? '--' : `${x.toFixed(1)}%`)

const BENCH_NAMES: Record<string, string> = { sh000922: '中证红利', sh000300: '沪深300', sh000852: '中证1000' }

const curve = useChart(
  () => {
    const c = base.value?.curve
    if (!c) return null
    const keys = ['strategy', ...Object.keys(BENCH_NAMES).filter((k) => c[k])]
    return {
      tooltip: { trigger: 'axis', valueFormatter: (x: number) => x?.toFixed(3) },
      legend: { top: 0 },
      grid: { left: 48, right: 16, top: 32, bottom: 28 },
      xAxis: { type: 'category', data: c.dates },
      yAxis: { type: 'value', scale: true },
      series: keys.map((k, i) => ({
        name: k === 'strategy' ? v.value?.name : BENCH_NAMES[k],
        type: 'line',
        showSymbol: false,
        data: c[k],
        lineStyle: { width: k === 'strategy' ? 2.2 : 1.2 },
        color: k === 'strategy' ? COLORS.up : COLORS.palette[i],
      })),
    }
  },
  () => [props.vkey, props.result?.id],
)

const heat = useChart(
  () => {
    const g = v.value?.grid
    if (!g) return null
    const data: any[] = []
    g.cells.forEach((row: any[], i: number) => row.forEach((c: any, j: number) => data.push([j, i, c.cagr])))
    const vals = data.map((x) => x[2]).filter((x) => x != null)
    return {
      tooltip: { formatter: (p: any) => `买入线 ${(g.buy[p.value[1]] * 100).toFixed(0)}%，卖出线 ${(g.sell[p.value[0]] * 100).toFixed(0)}%<br/>年化 ${p.value[2]}%` },
      grid: { left: 64, right: 16, top: 8, bottom: 64 },
      xAxis: { type: 'category', name: '卖出线', nameLocation: 'middle', nameGap: 28, data: g.sell.map((s: number) => `${(s * 100).toFixed(0)}%`) },
      yAxis: { type: 'category', name: '买入线', data: g.buy.map((b: number) => `${(b * 100).toFixed(0)}%`) },
      visualMap: { min: Math.min(...vals), max: Math.max(...vals), orient: 'horizontal', left: 'center', bottom: 0, itemHeight: 120,
        inRange: { color: [COLORS.down, '#f5f5f5', COLORS.up] }, calculable: false },
      series: [{ type: 'heatmap', data, label: { show: true, formatter: (p: any) => p.value[2]?.toFixed(1) } }],
    }
  },
  () => [props.vkey, props.result?.id],
)
</script>

<template>
  <SectionCard v-if="base" :title="`回测检验 · ${v.name}`" :subtitle="`${v.start} 至 ${base.end} · 最多同时持有 10 只、等权，次日开盘成交，含手续费、印花税和 0.1% 滑点`" class="mt">
    <div class="grid grid-4">
      <StatCard label="年化收益" :value="pct(base.cagr)" :value-class="colorClass(base.cagr)" :sub="`累计 ${pct(base.total)}`" />
      <StatCard label="最大回撤" :value="plain(base.max_drawdown)" :sub="`夏普 ${base.sharpe ?? '--'} · 平均仓位 ${plain(base.exposure)}`" />
      <StatCard label="交易" :value="`${base.trades} 笔`" :sub="`胜率 ${plain(base.win_rate)} · 平均每笔 ${pct(base.avg_trade)} · 平均持有 ${base.avg_hold_days ?? '--'} 天`" />
      <StatCard label="随机对照" :value="rnd ? `第 ${rnd.percentile} 百分位` : '--'"
        :sub="rnd ? `同日随机股票平均 ${pct(rnd.random_median)}（90% 区间 ${pct(rnd.random_p5)}～${pct(rnd.random_p95)}）` : ''" />
    </div>

    <div class="row2 mt">
      <div>
        <div class="bold small">净值曲线（起点为 1）</div>
        <div :ref="curve.el" style="height: 280px" />
      </div>
      <div>
        <div class="bold small">同期基准</div>
        <el-table :data="Object.entries(bench).map(([k, b]: any) => ({ name: k, ...b }))" size="small">
          <el-table-column prop="name" label="指数" />
          <el-table-column label="年化"><template #default="{ row }"><span class="num" :class="colorClass(row.cagr)">{{ pct(row.cagr) }}</span></template></el-table-column>
          <el-table-column label="最大回撤"><template #default="{ row }"><span class="num">{{ plain(row.max_drawdown) }}</span></template></el-table-column>
        </el-table>
        <div class="muted small mt-8">
          样本内年化 {{ pct(base.in_sample_cagr) }}，样本外（{{ base.out_sample_start }} 起）年化 {{ pct(base.out_sample_cagr) }}
        </div>
        <div class="years mt-8">
          <span v-for="y in years" :key="y" class="year">{{ y }}：<b class="num" :class="colorClass(base.yearly[y])">{{ pct(base.yearly[y]) }}</b></span>
        </div>
      </div>
    </div>

    <div class="row2 mt">
      <div>
        <div class="bold small">参数稳健性：不同买入线、卖出线下的年化收益（%）</div>
        <div :ref="heat.el" style="height: 320px" />
      </div>
      <div>
        <div class="bold small">出场规则对比</div>
        <el-table :data="exitRows" size="small">
          <el-table-column prop="name" label="规则" min-width="150" />
          <el-table-column label="年化" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.cagr)">{{ pct(row.cagr) }}</span></template></el-table-column>
          <el-table-column label="回撤" width="80"><template #default="{ row }"><span class="num">{{ plain(row.max_drawdown) }}</span></template></el-table-column>
          <el-table-column label="交易" width="60"><template #default="{ row }"><span class="num">{{ row.trades }}</span></template></el-table-column>
        </el-table>
        <div class="bold small mt">版本对比</div>
        <el-table :data="compareRows" size="small">
          <el-table-column prop="name" label="版本" min-width="170" />
          <el-table-column label="起点" width="80"><template #default="{ row }"><span class="num small">{{ row.start?.slice(0, 7) }}</span></template></el-table-column>
          <el-table-column label="年化" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.cagr)">{{ pct(row.cagr) }}</span></template></el-table-column>
          <el-table-column label="回撤" width="80"><template #default="{ row }"><span class="num">{{ plain(row.max_drawdown) }}</span></template></el-table-column>
          <el-table-column label="交易" width="60"><template #default="{ row }"><span class="num">{{ row.trades }}</span></template></el-table-column>
        </el-table>
      </div>
    </div>
    <el-alert type="info" :closable="false" show-icon class="mt" :title="result.caveat"
      description="回测只说明这套规则在过去这段时间的表现，不代表以后的结果。改良版的「近三年 ROE」要到 2022 年年报公告后才完整，所以回测期较短。" />
  </SectionCard>
</template>

<style scoped>
.row2 {
  display: grid;
  grid-template-columns: 1.3fr 1fr;
  gap: 20px;
}
.years {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 12px;
}
</style>
