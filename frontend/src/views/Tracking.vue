<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useChart } from '@/composables/useChart'
import { useJobPoller } from '@/composables/useJobPoller'
import { COLORS } from '@/utils/echartsTheme'
import { SIGNAL_NAMES, colorClass, num, percent, ratioPct } from '@/utils/format'
import { EmptyState, PageHeader, SectionCard, SignalBadge, StatCard } from '@/components/ui'

const router = useRouter()
const data = ref<any>(null)
const loading = ref(false)
const horizon = ref<'5' | '10' | '20'>('10')
const groupBy = ref<'by_signal' | 'by_scope' | 'by_regime' | 'by_pool'>('by_signal')
const detail = ref<{ visible: boolean; run: any; rows: any[] }>({ visible: false, run: null, rows: [] })

const poller = useJobPoller((job) => {
  if (job.job_name === 'tracking') {
    ElMessage.success(job.message || '已更新')
    load()
  }
})

const GROUP_NAMES: Record<string, Record<string, string>> = {
  by_signal: SIGNAL_NAMES,
  by_scope: { bi: '笔级别', seg: '线段级别' },
  by_regime: { strong: '强势', neutral: '震荡', weak: '弱势' },
  by_pool: { main: '主推荐', watch: '观察池' },
}
const k = computed(() => data.value?.kpis?.[horizon.value] || {})
const hasData = computed(() => Object.values(data.value?.kpis || {}).some((x: any) => x.n > 0))

const curveChart = useChart(
  () => {
    const pts = data.value?.curve || []
    if (!pts.length) return null
    return {
      grid: { left: 44, right: 16, top: 28, bottom: 28 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      xAxis: { type: 'category', data: pts.map((p: any) => p.date) },
      yAxis: { type: 'value', name: '%' },
      series: [
        { name: '单批 5 日超额', type: 'bar', barMaxWidth: 18, data: pts.map((p: any) => ({ value: p.avg_ex5, itemStyle: { color: p.avg_ex5 >= 0 ? COLORS.up : COLORS.down } })) },
        { name: '累计超额', type: 'line', data: pts.map((p: any) => p.cum), itemStyle: { color: COLORS.primary }, lineStyle: { width: 2 } },
      ],
    }
  },
  () => data.value,
)

const groupChart = useChart(
  () => {
    const rows = data.value?.[groupBy.value] || []
    if (!rows.length) return null
    const names = GROUP_NAMES[groupBy.value]
    return {
      grid: { left: 44, right: 44, top: 28, bottom: 24 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      xAxis: { type: 'category', data: rows.map((r: any) => names[r.group] || r.group) },
      yAxis: [{ type: 'value', name: '超额%' }, { type: 'value', name: '胜率%', max: 100, splitLine: { show: false } }],
      series: [
        { name: '10 日平均超额', type: 'bar', barMaxWidth: 30, data: rows.map((r: any) => ({ value: r.avg_excess, itemStyle: { color: (r.avg_excess ?? 0) >= 0 ? COLORS.up : COLORS.down } })), itemStyle: { color: COLORS.up } },
        { name: '10 日胜率', type: 'line', yAxisIndex: 1, data: rows.map((r: any) => (r.win_rate != null ? +(r.win_rate * 100).toFixed(1) : null)), itemStyle: { color: COLORS.primary } },
      ],
    }
  },
  () => [data.value, groupBy.value],
)

const hitChart = useChart(
  () => {
    const h = data.value?.first_hit
    if (!h || !(h.stop + h.target + h.none)) return null
    return {
      tooltip: { trigger: 'item', formatter: '{b}：{c}（{d}%）' },
      legend: { orient: 'vertical', right: 0, top: 'middle' },
      series: [{
        type: 'pie', radius: ['48%', '72%'], center: ['38%', '50%'], label: { show: false },
        data: [
          { name: '先到目标一', value: h.target, itemStyle: { color: COLORS.up } },
          { name: '先触及止损', value: h.stop, itemStyle: { color: COLORS.down } },
          { name: '都未触及', value: h.none, itemStyle: { color: '#cbd2dc' } },
        ],
      }],
    }
  },
  () => data.value,
)

async function load() {
  loading.value = true
  try {
    data.value = await api.recommendTracking(180)
  } finally {
    loading.value = false
  }
}
async function refresh() {
  await api.trackingRefresh()
  poller.start('tracking')
}
async function openRun(r: any) {
  detail.value = { visible: true, run: r, rows: await api.recommendRunPerf(r.run_id) }
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading && !data">
    <PageHeader title="推荐跟踪" subtitle="每条推荐按次日开盘价入场，统计之后 5/10/20 个交易日的收益，以及相对沪深 300、中证 1000 的超额；次日一字涨停视为买不进，不计入统计">
      <el-radio-group v-model="horizon" size="small">
        <el-radio-button value="5">5 日</el-radio-button>
        <el-radio-button value="10">10 日</el-radio-button>
        <el-radio-button value="20">20 日</el-radio-button>
      </el-radio-group>
      <el-button :icon="'Refresh'" :loading="!!poller.running.value.tracking" @click="refresh">立即更新</el-button>
    </PageHeader>

    <SectionCard v-if="data && !hasData">
      <EmptyState
        title="推荐满 5 个交易日后开始有数据"
        :description="`目前共跟踪 ${data.total_items} 条推荐。每个交易日收盘后会自动更新；有了数据后，这里会显示推荐的真实表现，用来检验和调整打分权重。`"
        icon="DataLine"
      >
        <el-button @click="router.push('/picker')">查看当前推荐</el-button>
      </EmptyState>
    </SectionCard>

    <template v-if="data && hasData">
      <div class="grid grid-4">
        <StatCard :label="`${horizon} 日平均收益（主推荐）`" :value="percent(k.avg_ret, 2)" :value-class="colorClass(k.avg_ret)" :sub="`${k.n} 条 · 胜率 ${ratioPct(k.win_rate, 1)}`" />
        <StatCard :label="`${horizon} 日超额 · 中证 1000`" :value="percent(k.avg_ex1000, 2)" :value-class="colorClass(k.avg_ex1000)" :sub="`跑赢比例 ${ratioPct(k.beat_rate, 1)}`" />
        <StatCard :label="`${horizon} 日超额 · 沪深 300`" :value="percent(k.avg_ex300, 2)" :value-class="colorClass(k.avg_ex300)" />
        <StatCard
          label="Rank IC 均值"
          :value="num(data.ic.mean, 3)"
          :value-class="colorClass(data.ic.mean)"
          :sub="`${data.ic.horizon} 日 · ${data.ic.n_runs} 批 · IC>0 占 ${ratioPct(data.ic.positive_ratio)}`"
          hint="每批推荐中综合分与之后收益的排序相关系数。持续大于 0 说明分数越高的股票表现越好，一般 0.03 以上就有参考价值。"
        />
      </div>

      <div class="grid mt two">
        <SectionCard title="累计超额曲线" subtitle="每批主推荐 5 日平均超额（相对中证 1000）的累加">
          <div :ref="curveChart.el" style="height: 280px" />
        </SectionCard>
        <SectionCard title="先到目标还是先止损" subtitle="主推荐，至少跟踪 5 天">
          <div :ref="hitChart.el" style="height: 280px" />
        </SectionCard>
      </div>

      <SectionCard title="分组表现" subtitle="10 日平均超额与胜率" class="mt">
        <template #extra>
          <el-radio-group v-model="groupBy" size="small">
            <el-radio-button value="by_signal">信号类型</el-radio-button>
            <el-radio-button value="by_scope">信号级别</el-radio-button>
            <el-radio-button value="by_regime">市场温度</el-radio-button>
            <el-radio-button value="by_pool">主推荐 / 观察池</el-radio-button>
          </el-radio-group>
        </template>
        <div class="grid group-grid">
          <div :ref="groupChart.el" style="height: 260px" />
          <el-table :data="data[groupBy]" size="small">
            <el-table-column label="分组"><template #default="{ row }">{{ GROUP_NAMES[groupBy][row.group] || row.group }}</template></el-table-column>
            <el-table-column prop="n_with_data" label="样本" width="60" />
            <el-table-column label="平均收益" width="90"><template #default="{ row }"><span class="num" :class="colorClass(row.avg_ret)">{{ percent(row.avg_ret, 2) }}</span></template></el-table-column>
            <el-table-column label="平均超额" width="90"><template #default="{ row }"><span class="num" :class="colorClass(row.avg_excess)">{{ percent(row.avg_excess, 2) }}</span></template></el-table-column>
            <el-table-column label="胜率" width="70"><template #default="{ row }"><span class="num">{{ ratioPct(row.win_rate) }}</span></template></el-table-column>
          </el-table>
        </div>
      </SectionCard>
    </template>

    <SectionCard v-if="data?.runs?.length" title="推荐批次" subtitle="点击查看每只股票的后续表现" class="mt" flush>
      <el-table :data="data.runs" size="small" @row-click="openRun" class="clickable">
        <el-table-column prop="run_id" label="#" width="56" />
        <el-table-column prop="run_date" label="推荐日期" width="110" />
        <el-table-column label="市场" width="70"><template #default="{ row }">{{ GROUP_NAMES.by_regime[row.regime] || row.regime }}</template></el-table-column>
        <el-table-column label="主推荐 / 观察" width="110"><template #default="{ row }">{{ row.n_main }} / {{ row.n_watch }}</template></el-table-column>
        <el-table-column prop="days" label="已跟踪天数" width="100" />
        <el-table-column label="5 日收益 / 超额" width="150"><template #default="{ row }"><span class="num" :class="colorClass(row.avg_ret5)">{{ percent(row.avg_ret5, 2) }}</span> / <span class="num" :class="colorClass(row.avg_ex5)">{{ percent(row.avg_ex5, 2) }}</span></template></el-table-column>
        <el-table-column label="10 日收益 / 超额" width="150"><template #default="{ row }"><span class="num" :class="colorClass(row.avg_ret10)">{{ percent(row.avg_ret10, 2) }}</span> / <span class="num" :class="colorClass(row.avg_ex10)">{{ percent(row.avg_ex10, 2) }}</span></template></el-table-column>
        <el-table-column label="20 日收益 / 超额" width="150"><template #default="{ row }"><span class="num" :class="colorClass(row.avg_ret20)">{{ percent(row.avg_ret20, 2) }}</span> / <span class="num" :class="colorClass(row.avg_ex20)">{{ percent(row.avg_ex20, 2) }}</span></template></el-table-column>
        <el-table-column label="IC（5 / 10 日）"><template #default="{ row }"><span class="num">{{ num(row.ic5, 3) }} / {{ num(row.ic10, 3) }}</span></template></el-table-column>
      </el-table>
    </SectionCard>

    <el-drawer v-model="detail.visible" :title="`推荐批次 #${detail.run?.run_id || ''} · ${detail.run?.run_date || ''}`" size="820px">
      <el-table :data="detail.rows" size="small" height="100%">
        <el-table-column label="股票" min-width="120">
          <template #default="{ row }"><router-link :to="`/stock/${row.code}`" class="bold">{{ row.name || row.code }}</router-link><div class="muted small num">{{ row.code }}</div></template>
        </el-table-column>
        <el-table-column label="池" width="64"><template #default="{ row }">{{ row.pool === 'main' ? '主推荐' : '观察' }}</template></el-table-column>
        <el-table-column label="信号" width="90"><template #default="{ row }"><SignalBadge :type="row.signal_type" :scope="row.scope" size="sm" /></template></el-table-column>
        <el-table-column label="入场" width="120">
          <template #default="{ row }"><span v-if="row.blocked" class="muted">一字涨停未买入</span><span v-else class="num">{{ row.entry_date?.slice(5) }} @ {{ num(row.entry_price) }}</span></template>
        </el-table-column>
        <el-table-column label="5 日" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.ret5)">{{ percent(row.ret5, 1) }}</span></template></el-table-column>
        <el-table-column label="10 日" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.ret10)">{{ percent(row.ret10, 1) }}</span></template></el-table-column>
        <el-table-column label="20 日" width="80"><template #default="{ row }"><span class="num" :class="colorClass(row.ret20)">{{ percent(row.ret20, 1) }}</span></template></el-table-column>
        <el-table-column label="10 日超额" width="90"><template #default="{ row }"><span class="num" :class="colorClass(row.ex1000_10)">{{ percent(row.ex1000_10, 1) }}</span></template></el-table-column>
        <el-table-column label="最大回撤" width="90"><template #default="{ row }"><span class="num down">{{ percent(row.max_drawdown, 1) }}</span></template></el-table-column>
        <el-table-column label="先触及" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.first_hit" size="small" effect="plain" :type="row.first_hit === 'target' ? 'danger' : 'success'">{{ row.first_hit === 'target' ? '目标一' : '止损' }}</el-tag>
            <span v-else class="muted">--</span>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>
  </div>
</template>

<style scoped>
.two {
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
}
.group-grid {
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  align-items: center;
}
.clickable :deep(tr) {
  cursor: pointer;
}
</style>
