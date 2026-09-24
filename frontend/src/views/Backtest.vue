<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useChart } from '@/composables/useChart'
import { useJobPoller } from '@/composables/useJobPoller'
import { COLORS } from '@/utils/echartsTheme'
import { colorClass, dt, num, pct, percent, ratioPct } from '@/utils/format'
import { EmptyState, PageHeader, SectionCard, SignalBadge, StatCard } from '@/components/ui'

const form = ref<{ sample_size: number; lookback_bars: number; entry_mode: string; codes: string; split_date: string | null; slippage_pct: number; with_control: boolean }>({
  sample_size: 100, lookback_bars: 750, entry_mode: 'confirmed', codes: '', split_date: null, slippage_pct: 0.1, with_control: true,
})
const runs = ref<any[]>([])
const current = ref<any>(null)
const activate = ref(false)

const poller = useJobPoller((job) => {
  if (job.job_name === 'backtest') {
    ElMessage[job.status === 'success' ? 'success' : 'error'](job.message || '回测结束')
    loadRuns(true)
  }
})

const running = computed(() => poller.running.value.backtest)
const sigRows = computed(() =>
  ['B1', 'B2', 'B3'].map((k) => ({ type: k, ...(current.value?.by_signal?.[k] || { trades: 0 }) })),
)
const trades = computed<any[]>(() => current.value?.trades || [])
const s = computed(() => current.value?.summary || {})
const edge = computed(() => s.value.matched_edge || s.value.edge_ci)
const diagDim = ref('type_regime')
const diagDims = computed<any[]>(() => s.value.diagnostics || [])
const diagRows = computed<any[]>(() => diagDims.value.find((d: any) => d.key === diagDim.value)?.rows || [])
const wf = computed(() => s.value.walk_forward)

function verdict(m: any): { text: string; type: 'success' | 'danger' | 'info' | 'warning' } {
  if (!m) return { text: '样本不足', type: 'info' }
  if (m.significant) return m.edge > 0 ? { text: '显著跑赢', type: 'success' } : { text: '显著跑输', type: 'danger' }
  return { text: '不显著', type: 'warning' }
}
function ci(m: any): string {
  return m ? `[${num(m.ci_low, 2)}, ${num(m.ci_high, 2)}]` : '--'
}
const scopeRows = computed(() => Object.entries(s.value.by_scope || {}).map(([k, v]: any) => ({ key: k, ...v })))

const sigChart = useChart(
  () => {
    if (!current.value?.by_signal) return null
    const rows = sigRows.value
    return {
      grid: { left: 44, right: 44, top: 32, bottom: 24 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      xAxis: { type: 'category', data: rows.map((r) => r.name || r.type) },
      yAxis: [{ type: 'value', name: '胜率%', max: 100 }, { type: 'value', name: '平均R', splitLine: { show: false } }],
      series: [
        { name: '胜率', type: 'bar', barMaxWidth: 32, data: rows.map((r) => (r.win_rate != null ? +(r.win_rate * 100).toFixed(1) : 0)), itemStyle: { color: COLORS.primary } },
        {
          name: '平均 R',
          type: 'bar',
          yAxisIndex: 1,
          itemStyle: { color: COLORS.up },
          barMaxWidth: 32,
          data: rows.map((r) => ({ value: r.avg_r ?? 0, itemStyle: { color: (r.avg_r ?? 0) >= 0 ? COLORS.up : COLORS.down } })),
        },
      ],
    }
  },
  () => current.value,
)

const histChart = useChart(
  () => {
    if (!trades.value.length) return null
    const edges = [-3, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 3, 5]
    const labels: string[] = []
    const counts: number[] = []
    const ctrl: number[] = []
    const colors: string[] = []
    const control: number[] = current.value?.control_r || []
    for (let i = 0; i <= edges.length; i++) {
      const lo = i === 0 ? -Infinity : edges[i - 1]
      const hi = i === edges.length ? Infinity : edges[i]
      labels.push(i === 0 ? `<${edges[0]}` : i === edges.length ? `≥${edges[edges.length - 1]}` : `${lo}~${hi}`)
      counts.push(trades.value.filter((t) => t.r_multiple >= lo && t.r_multiple < hi).length)
      ctrl.push(control.filter((r) => r >= lo && r < hi).length)
      colors.push(hi <= 0 ? COLORS.down : COLORS.up)
    }
    return {
      grid: { left: 36, right: 12, top: 28, bottom: 36 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0, data: ['信号', '随机对照'] },
      xAxis: { type: 'category', data: labels, axisLabel: { rotate: 30 } },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        { name: '信号', type: 'bar', data: counts.map((c, i) => ({ value: c, itemStyle: { color: colors[i] } })), barGap: '0%', itemStyle: { color: COLORS.up } },
        ...(control.length ? [{ name: '随机对照', type: 'bar', data: ctrl, itemStyle: { color: '#cbd2dc' } }] : []),
      ],
    }
  },
  () => current.value,
)

const exitChart = useChart(
  () => {
    const reasons = s.value.exit_reasons || {}
    const data = Object.entries(reasons).map(([name, value]) => ({ name, value }))
    if (!data.length) return null
    return {
      tooltip: { trigger: 'item', formatter: '{b}：{c} 笔（{d}%）' },
      legend: { orient: 'vertical', right: 0, top: 'middle' },
      series: [{ type: 'pie', radius: ['45%', '70%'], center: ['36%', '50%'], label: { show: false }, data }],
    }
  },
  () => current.value,
)

async function loadRuns(openLatest = false) {
  runs.value = await api.backtestRuns()
  if ((openLatest || !current.value) && runs.value.length) await open(runs.value[0].id)
}
async function open(id: number) {
  current.value = await api.backtestRun(id)
}
async function start() {
  const codes = form.value.codes.split(/[\s,，]+/).map((x) => x.trim()).filter(Boolean)
  const f = form.value
  await api.backtestStart({
    sample_size: f.sample_size, lookback_bars: f.lookback_bars, entry_mode: f.entry_mode, codes: codes.length ? codes : null,
    split_date: f.split_date || null, slippage: f.slippage_pct / 100, with_control: f.with_control,
  })
  ElMessage.success('回测已开始')
  poller.start('backtest')
}
async function apply() {
  const r = await api.backtestApply(current.value.id, activate.value)
  ElMessage.success(`已保存为策略配置 #${r.config_id}${activate.value ? '，并已启用' : ''}`)
}

onMounted(() => {
  loadRuns()
  poller.tick().then(() => {
    if (poller.running.value.backtest) poller.start()
  })
})
</script>

<template>
  <div class="page">
    <PageHeader title="策略回测" subtitle="逐根 K 线向前推进，每一步只用当时可见的数据重算缠论结构，避免笔和线段“重画”带来的未来函数；止损止盈规则与持仓策略一致" />

    <div class="grid layout">
      <div class="left">
        <SectionCard title="回测参数">
          <el-form label-position="top" size="small">
            <el-form-item label="样本数量（从通过基本面过滤的股票中随机抽取）">
              <el-input-number v-model="form.sample_size" :min="5" :max="2000" :step="50" style="width: 100%" />
            </el-form-item>
            <el-form-item label="回看 K 线数（另加 250 根预热）">
              <el-input-number v-model="form.lookback_bars" :min="300" :max="1200" :step="50" style="width: 100%" />
            </el-form-item>
            <el-form-item label="入场时机">
              <el-radio-group v-model="form.entry_mode">
                <el-radio-button value="confirmed">笔确认后</el-radio-button>
                <el-radio-button value="early">信号出现即入场</el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item label="样本内外切分日期（留空按 70% 自动切分）">
              <el-date-picker v-model="form.split_date" type="date" value-format="YYYY-MM-DD" placeholder="自动" style="width: 100%" />
            </el-form-item>
            <el-form-item label="单边滑点（%）">
              <el-input-number v-model="form.slippage_pct" :min="0" :max="1" :step="0.05" :precision="2" style="width: 100%" />
            </el-form-item>
            <el-form-item label="随机入场对照组">
              <el-switch v-model="form.with_control" active-text="同时跑对照组，检验买点是否真的有优势" />
            </el-form-item>
            <el-form-item label="指定股票（可选）">
              <el-input v-model="form.codes" type="textarea" :rows="2" placeholder="逗号分隔，如 sh600519,sz000001" />
            </el-form-item>
          </el-form>
          <el-button type="primary" style="width: 100%" :loading="!!running" @click="start">
            {{ running ? `回测中 ${running.progress}/${running.total}` : '开始回测' }}
          </el-button>
          <el-progress v-if="running && running.total" :percentage="Math.round((running.progress / running.total) * 100)" :stroke-width="6" class="mt-8" />
        </SectionCard>

        <SectionCard title="历史回测" class="mt" flush>
          <div v-if="runs.length" class="runs">
            <div v-for="r in runs" :key="r.id" class="run" :class="{ active: current?.id === r.id }" @click="open(r.id)">
              <div class="row-between">
                <b>#{{ r.id }}</b>
                <el-tag size="small" :type="r.status === 'success' ? 'success' : r.status === 'failed' ? 'danger' : 'warning'" effect="plain">{{ r.status }}</el-tag>
              </div>
              <div class="muted small">{{ dt(r.created_at) }} · {{ r.params?.sample_size }} 只 · {{ r.params?.entry_mode === 'early' ? '提前入场' : '确认入场' }}</div>
              <div class="row small">
                <span>{{ r.summary?.trades ?? 0 }} 笔</span>
                <span>胜率 {{ ratioPct(r.summary?.win_rate) }}</span>
                <span :class="colorClass(r.summary?.avg_r)" class="num">平均R {{ num(r.summary?.avg_r) }}</span>
              </div>
            </div>
          </div>
          <EmptyState v-else title="还没有回测记录" />
        </SectionCard>
      </div>

      <div class="right">
        <template v-if="current">
          <div class="grid grid-4">
            <StatCard label="交易笔数" :value="s.trades ?? 0" :sub="current.message" />
            <StatCard label="胜率" :value="ratioPct(s.win_rate, 1)" :sub="`盈亏比 ${num(s.profit_factor)}`" />
            <StatCard label="平均 R" :value="num(s.avg_r)" :value-class="colorClass(s.avg_r)" :sub="`最大亏损 ${num(s.max_loss_r)}R · 最大盈利 ${num(s.max_win_r)}R`" />
            <StatCard label="平均收益 / 持有" :value="percent(s.avg_pnl_pct, 2)" :value-class="colorClass(s.avg_pnl_pct)" :sub="`平均持有 ${num(s.avg_holding_days, 1)} 天`" />
          </div>

          <el-alert v-if="s.note" :title="s.note" type="warning" :closable="false" show-icon class="mt" />

          <div class="grid grid-4 mt">
            <StatCard label="平均超额（中证 1000）" :value="pct(s.avg_excess, 2)" :value-class="colorClass(s.avg_excess)" sub="每笔交易相对同期指数" />
            <StatCard
              label="样本内 → 样本外 平均 R"
              :value="`${num(s.in_sample?.avg_r)} → ${num(s.out_sample?.avg_r)}`"
              :sub="`切分日 ${current.params?.split_date || '--'} · ${s.in_sample?.trades ?? 0} / ${s.out_sample?.trades ?? 0} 笔`"
              hint="建议权重只用样本内数据计算。样本外表现明显变差，说明可能过拟合。"
            />
            <StatCard label="随机对照 平均 R" :value="num(s.control?.avg_r)" :sub="s.control ? `${s.control.trades} 笔 · 胜率 ${ratioPct(s.control.win_rate, 1)}` : '本次未运行对照组'" />
            <StatCard
              :label="s.matched_edge ? '相对同期同市场状态随机组的优势' : '信号相对随机的优势'"
              :value="edge ? `${edge.edge > 0 ? '+' : ''}${num(edge.edge, 2)} R` : '--'"
              :value-class="edge ? colorClass(edge.edge) : ''"
              :sub="edge ? `95% 置信区间 ${ci(edge)} · ${edge.significant ? '显著' : '不显著'}` : '样本不足'"
              hint="随机组按信号交易所在的年份和市场状态加权，剔除'信号恰好出现在大盘上涨期'的择时差异；置信区间用分层自助法估计。区间不跨过 0 才能说买点有稳定优势。"
            />
          </div>

          <div class="grid grid-3 mt">
            <SectionCard title="按信号类型" subtitle="胜率与平均 R">
              <div :ref="sigChart.el" style="height: 240px" />
            </SectionCard>
            <SectionCard title="R 倍数分布">
              <div v-if="trades.length" :ref="histChart.el" style="height: 240px" />
              <EmptyState v-else title="没有交易" />
            </SectionCard>
            <SectionCard title="离场原因">
              <div v-if="Object.keys(s.exit_reasons || {}).length" :ref="exitChart.el" style="height: 240px" />
              <EmptyState v-else title="没有交易" />
            </SectionCard>
          </div>

          <SectionCard title="样本内 / 样本外 · 按信号级别" class="mt" flush>
            <el-table :data="[
              { name: '样本内', ...s.in_sample }, { name: '样本外', ...s.out_sample },
              ...scopeRows.map((r: any) => ({ ...r, name: r.name })),
            ]" size="small">
              <el-table-column prop="name" label="分组" width="100" />
              <el-table-column prop="trades" label="笔数" width="70" />
              <el-table-column label="胜率"><template #default="{ row }">{{ ratioPct(row.win_rate, 1) }}</template></el-table-column>
              <el-table-column label="平均 R"><template #default="{ row }"><span class="num" :class="colorClass(row.avg_r)">{{ num(row.avg_r) }}</span></template></el-table-column>
              <el-table-column label="平均超额"><template #default="{ row }"><span class="num" :class="colorClass(row.avg_excess)">{{ pct(row.avg_excess, 2) }}</span></template></el-table-column>
              <el-table-column label="盈亏比"><template #default="{ row }">{{ num(row.profit_factor) }}</template></el-table-column>
            </el-table>
          </SectionCard>

          <SectionCard v-if="diagDims.length" title="信号诊断" subtitle="每组与同市场状态的随机入场比较，优势 = 信号平均 R − 随机平均 R" class="mt" flush>
            <div class="diag-bar">
              <el-radio-group v-model="diagDim" size="small">
                <el-radio-button v-for="d in diagDims" :key="d.key" :value="d.key">{{ d.title }}</el-radio-button>
              </el-radio-group>
            </div>
            <el-table :data="diagRows" size="small">
              <el-table-column prop="name" label="分组" min-width="130" />
              <el-table-column prop="trades" label="笔数" width="70" />
              <el-table-column label="胜率" width="80"><template #default="{ row }">{{ ratioPct(row.win_rate, 1) }}</template></el-table-column>
              <el-table-column label="平均 R" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.avg_r)">{{ num(row.avg_r) }}</span></template></el-table-column>
              <el-table-column label="随机组 R" width="90"><template #default="{ row }"><span class="num">{{ num(row.matched?.control_avg_r) }}</span></template></el-table-column>
              <el-table-column label="优势" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.matched?.edge)">{{ num(row.matched?.edge, 2) }}</span></template></el-table-column>
              <el-table-column label="95% 区间" width="130"><template #default="{ row }"><span class="num muted">{{ ci(row.matched) }}</span></template></el-table-column>
              <el-table-column label="超额优势" width="90"><template #default="{ row }"><span class="num" :class="colorClass(row.matched?.excess_edge)">{{ pct(row.matched?.excess_edge, 2) }}</span></template></el-table-column>
              <el-table-column label="结论" width="96"><template #default="{ row }"><el-tag size="small" :type="verdict(row.matched).type" effect="light">{{ verdict(row.matched).text }}</el-tag></template></el-table-column>
            </el-table>
          </SectionCard>

          <SectionCard v-if="wf?.folds?.length" title="逐年滚动检验" :subtitle="`每年只用此前年份的交易，挑出跑赢随机组的'信号 × 市场状态'组合（训练样本至少 ${wf.min_n} 笔），在当年检验`" class="mt" flush>
            <div class="wf-overall">
              <span>全部检验年份合计：入选组合 <b class="num">{{ wf.overall.selected.trades || 0 }}</b> 笔，平均 R
                <b class="num" :class="colorClass(wf.overall.selected.avg_r)">{{ num(wf.overall.selected.avg_r) }}</b>，相对随机组优势
                <b class="num" :class="colorClass(wf.overall.edge?.edge)">{{ num(wf.overall.edge?.edge, 2) }}</b> {{ ci(wf.overall.edge) }}</span>
              <el-tag size="small" :type="verdict(wf.overall.edge).type" effect="light">{{ verdict(wf.overall.edge).text }}</el-tag>
            </div>
            <el-table :data="wf.folds" size="small">
              <el-table-column prop="year" label="检验年份" width="90" />
              <el-table-column prop="train_trades" label="训练笔数" width="90" />
              <el-table-column label="入选组合" min-width="220">
                <template #default="{ row }">
                  <span v-if="!row.selected.length" class="muted">无组合跑赢随机组</span>
                  <el-tag v-for="u in row.selected" :key="u" size="small" effect="plain" class="wf-tag">{{ u }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="当年全部信号" width="170">
                <template #default="{ row }">{{ row.test_all.trades || 0 }} 笔 · <span class="num" :class="colorClass(row.edge_all?.edge)">优势 {{ num(row.edge_all?.edge, 2) }}</span></template>
              </el-table-column>
              <el-table-column label="当年入选组合" width="170">
                <template #default="{ row }">{{ row.test_selected.trades || 0 }} 笔 · <span class="num" :class="colorClass(row.edge_selected?.edge)">优势 {{ num(row.edge_selected?.edge, 2) }}</span></template>
              </el-table-column>
              <el-table-column label="结论" width="96"><template #default="{ row }"><el-tag size="small" :type="verdict(row.edge_selected).type" effect="light">{{ verdict(row.edge_selected).text }}</el-tag></template></el-table-column>
            </el-table>
          </SectionCard>

          <SectionCard title="建议信号权重" subtitle="只用样本内交易计算，每类信号至少 10 笔才给出建议" class="mt">
            <div v-if="current.suggested_weights && Object.keys(current.suggested_weights).length" class="row wrap weights">
              <div v-for="(v, k) in current.suggested_weights" :key="k" class="weight">
                <SignalBadge :type="String(k)" />
                <b class="num">{{ v }}</b>
              </div>
              <span class="grow" />
              <el-checkbox v-model="activate">保存后立即启用</el-checkbox>
              <el-button type="primary" size="small" @click="apply">保存为新策略配置</el-button>
            </div>
            <div v-else class="muted small">样本不足，暂无建议。可以加大样本数量后重新回测。</div>
          </SectionCard>

          <SectionCard title="交易明细" :subtitle="`${trades.length} 笔`" class="mt" flush>
            <el-table v-if="trades.length" :data="trades" size="small" max-height="420">
              <el-table-column prop="code" label="代码" width="100" />
              <el-table-column label="信号" width="90"><template #default="{ row }"><SignalBadge :type="row.signal_type" :scope="row.scope || 'bi'" size="sm" /></template></el-table-column>
              <el-table-column label="样本" width="64"><template #default="{ row }">{{ row.segment === 'out' ? '样本外' : '样本内' }}</template></el-table-column>
              <el-table-column label="买入" width="170"><template #default="{ row }"><span class="num">{{ row.entry_date }} @ {{ num(row.entry_price) }}</span></template></el-table-column>
              <el-table-column label="卖出" width="170"><template #default="{ row }"><span class="num">{{ row.exit_date }} @ {{ num(row.exit_price) }}</span></template></el-table-column>
              <el-table-column label="R" width="70" sortable :sort-method="(a: any, b: any) => a.r_multiple - b.r_multiple">
                <template #default="{ row }"><b class="num" :class="colorClass(row.r_multiple)">{{ num(row.r_multiple) }}</b></template>
              </el-table-column>
              <el-table-column label="收益" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.pnl_pct)">{{ percent(row.pnl_pct, 2) }}</span></template></el-table-column>
              <el-table-column label="同期指数" width="84"><template #default="{ row }"><span class="num">{{ pct(row.bench_ret, 2) }}</span></template></el-table-column>
              <el-table-column label="超额" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.excess)">{{ pct(row.excess, 2) }}</span></template></el-table-column>
              <el-table-column prop="holding_days" label="持有天数" width="90" />
              <el-table-column prop="exit_reason" label="离场原因" />
            </el-table>
            <EmptyState v-else title="这次回测没有产生交易" />
          </SectionCard>
        </template>
        <SectionCard v-else>
          <EmptyState title="还没有回测结果" description="在左侧设置参数后开始回测。建议先抽 100 只股票，看看一买、二买、三买各自的胜率和平均 R。" icon="TrendCharts" />
        </SectionCard>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout {
  grid-template-columns: 300px minmax(0, 1fr);
  align-items: start;
}
.mt-8 {
  margin-top: 8px;
}
.runs {
  max-height: 520px;
  overflow-y: auto;
}
.run {
  padding: 10px 16px;
  border-bottom: 1px solid #f0f2f6;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.run:hover {
  background: var(--c-surface-2);
}
.run.active {
  background: var(--c-primary-soft);
  box-shadow: inset 3px 0 0 var(--c-primary);
}
.weights {
  gap: 16px;
}
.weight {
  display: flex;
  align-items: center;
  gap: 8px;
}
.weight b {
  font-size: 16px;
}
.diag-bar {
  padding: 10px 16px;
  border-bottom: 1px solid var(--c-border);
}
.wf-overall {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  font-size: 13px;
  border-bottom: 1px solid var(--c-border);
}
.wf-tag {
  margin: 2px 4px 2px 0;
}
</style>
