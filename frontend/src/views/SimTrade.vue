<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useChart } from '@/composables/useChart'
import { COLORS } from '@/utils/echartsTheme'
import { colorClass, dt, money, num, pct, ratioPct } from '@/utils/format'
import { EmptyState, PageHeader, SectionCard, SignalBadge, StatCard } from '@/components/ui'

const account = ref<any>(null)
const positions = ref<any[]>([])
const orders = ref<any[]>([])
const stats = ref<any>(null)
const loading = ref(false)
const submitting = ref(false)
const direction = ref<'buy' | 'sell'>('buy')
const form = ref<{ code: string; name: string; quantity: number; price?: number }>({ code: '', name: '', quantity: 100 })
const statusFilter = ref('all')

const STATUS: Record<string, { label: string; type: any }> = {
  pending: { label: '待成交', type: 'warning' },
  partial: { label: '部分成交', type: 'warning' },
  filled: { label: '已成交', type: 'success' },
  cancelled: { label: '已撤单', type: 'info' },
  rejected: { label: '废单', type: 'danger' },
}
const SOURCE: Record<string, string> = { manual: '手动', strategy: '策略', app: 'App' }

const holding = computed(() => positions.value.find((p) => p.code === form.value.code))
const amount = computed(() => (form.value.price ? form.value.price * form.value.quantity : 0))
const fee = computed(() => {
  const commission = Math.max(5, amount.value * 0.00025)
  return direction.value === 'sell' ? commission + amount.value * 0.0005 : commission
})
const pnlRate = computed(() => {
  const a = account.value
  if (!a?.total_asset || a.total_pnl == null) return null
  const initial = a.total_asset - a.total_pnl
  return initial ? (a.total_pnl / initial) * 100 : null
})
const filteredOrders = computed(() => {
  if (statusFilter.value === 'all') return orders.value
  if (statusFilter.value === 'open') return orders.value.filter((o) => ['pending', 'partial'].includes(o.status))
  return orders.value.filter((o) => o.status === statusFilter.value)
})
const openCount = computed(() => orders.value.filter((o) => ['pending', 'partial'].includes(o.status)).length)

function lot(v: number) {
  return Math.max(100, Math.floor(v / 100) * 100)
}
function quick(ratio: number) {
  if (direction.value === 'buy') {
    if (!form.value.price || !account.value?.cash) return ElMessage.warning('先填写代码并取现价')
    form.value.quantity = lot((account.value.cash * ratio) / (form.value.price * 1.001))
  } else {
    const avail = holding.value?.available ?? holding.value?.quantity ?? 0
    if (!avail) return ElMessage.warning('没有可卖数量')
    form.value.quantity = ratio >= 1 ? Math.floor(avail / 100) * 100 : lot(avail * ratio)
  }
}
function tickPrice(delta: number) {
  if (form.value.price) form.value.price = +(form.value.price + delta).toFixed(2)
}

async function querySearch(q: string, cb: (items: any[]) => void) {
  if (!q.trim()) return cb([])
  const rows = await api.searchStocks(q).catch(() => [])
  cb(rows.filter((r: any) => !r.is_index && !r.code.startsWith('bj')).map((r: any) => ({ value: r.code, name: r.name, industry: r.industry })))
}
async function onPick(item: any) {
  form.value.code = item.value
  form.value.name = item.name
  await fillPrice()
}
async function fillPrice() {
  if (!form.value.code) return
  const r = await api.simLimitPrice(form.value.code, direction.value)
  form.value.price = r.price
}
function fromPosition(p: any) {
  direction.value = 'sell'
  form.value = { code: p.code, name: p.name, quantity: Math.floor((p.available ?? p.quantity) / 100) * 100 || 100, price: undefined }
  fillPrice()
}

const curve = useChart(
  () => {
    const pts = stats.value?.equity_curve || []
    if (!pts.length) return null
    return {
      grid: { left: 70, right: 16, top: 28, bottom: 28 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      xAxis: { type: 'category', data: pts.map((p: any) => p.date), boundaryGap: false },
      yAxis: { type: 'value', scale: true },
      series: [
        { name: '总资产', type: 'line', data: pts.map((p: any) => p.total_asset), symbol: pts.length < 3 ? 'circle' : 'none', lineStyle: { width: 2, color: COLORS.primary }, itemStyle: { color: COLORS.primary }, areaStyle: { opacity: 0.08, color: COLORS.primary } },
        { name: '持仓市值', type: 'line', data: pts.map((p: any) => p.market_value), symbol: 'none', lineStyle: { width: 1.4, color: COLORS.warn }, itemStyle: { color: COLORS.warn } },
      ],
    }
  },
  () => stats.value,
)

const sigChart = useChart(
  () => {
    const rows = stats.value?.by_signal || []
    if (!rows.length) return null
    return {
      grid: { left: 40, right: 40, top: 28, bottom: 24 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      xAxis: { type: 'category', data: rows.map((r: any) => r.name) },
      yAxis: [{ type: 'value', name: '胜率%', max: 100 }, { type: 'value', name: '平均R', splitLine: { show: false } }],
      series: [
        { name: '胜率', type: 'bar', barMaxWidth: 28, data: rows.map((r: any) => (r.win_rate != null ? +(r.win_rate * 100).toFixed(1) : null)), itemStyle: { color: COLORS.primary } },
        { name: '平均 R', type: 'line', yAxisIndex: 1, data: rows.map((r: any) => r.avg_r), itemStyle: { color: COLORS.up } },
      ],
    }
  },
  () => stats.value,
)

async function load() {
  loading.value = true
  try {
    const [a, p, o, s] = await Promise.all([api.simAccount(), api.simPositions(), api.simOrders(), api.simStats()])
    account.value = a
    positions.value = p
    orders.value = o
    stats.value = s
  } finally {
    loading.value = false
  }
}

async function submit() {
  const f = form.value
  if (!f.code) return ElMessage.warning('请输入股票代码')
  if (!f.code.startsWith('sh') && !f.code.startsWith('sz')) return ElMessage.warning('模拟交易仅支持沪深 A 股')
  if (f.quantity % 100) return ElMessage.warning('数量必须为 100 的整数倍')
  const verb = direction.value === 'buy' ? '买入' : '卖出'
  await ElMessageBox.confirm(`${verb} ${f.name || f.code} ${f.quantity} 股，限价 ${f.price ?? '自动'}，约 ${money(amount.value)} 元？`, '确认模拟下单')
  submitting.value = true
  try {
    const o = await api.simPlace({ code: f.code, direction: direction.value, quantity: f.quantity, price: f.price })
    ElMessage.success(`委托已提交：${STATUS[o.status]?.label || o.status}`)
    load()
  } finally {
    submitting.value = false
  }
}

async function cancel(o: any) {
  await ElMessageBox.confirm(`撤销 ${o.name || o.code} 的${o.direction === 'buy' ? '买入' : '卖出'}委托？`, '确认撤单')
  await api.simCancel(o.order_id)
  ElMessage.success('已撤单')
  load()
}
async function sync(range = 'today') {
  const r = await api.simSync(range)
  ElMessage.success(`同步完成：远端 ${r.remote} 条，新增 ${r.created}，新成交 ${r.fills}`)
  load()
}
async function snapshot() {
  await api.simSnapshot()
  ElMessage.success('已记录今日账户快照')
  load()
}

watch(direction, () => fillPrice())
onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading && !account">
    <PageHeader title="模拟盘" subtitle="腾讯自选股练习赛组合（虚拟资金）：仅沪深 A 股、限价单、100 股整数倍、T+1">
      <el-button :icon="'Refresh'" @click="sync('today')">同步今日委托</el-button>
      <el-button @click="sync('recent')">同步近 3 月</el-button>
      <el-button @click="snapshot">记录快照</el-button>
    </PageHeader>

    <div class="grid grid-4">
      <StatCard label="总资产" :value="money(account?.total_asset)" :sub="account ? `${num(account.total_asset)} 元` : ''" />
      <StatCard label="可用资金" :value="money(account?.cash)" :sub="account?.total_asset ? `占总资产 ${ratioPct(account.cash / account.total_asset)}` : ''" />
      <StatCard label="持仓市值" :value="money(account?.market_value)" :sub="`${positions.length} 只持仓`" />
      <StatCard label="总盈亏" :value="num(account?.total_pnl)" :value-class="colorClass(account?.total_pnl)" :sub="pnlRate != null ? `收益率 ${pct(pnlRate)}` : ''" :sub-class="colorClass(pnlRate)" />
    </div>

    <div class="grid trade mt">
      <SectionCard padding="0">
        <div class="ticket-tabs">
          <button :class="['tab', 'buy', { active: direction === 'buy' }]" @click="direction = 'buy'">买入</button>
          <button :class="['tab', 'sell', { active: direction === 'sell' }]" @click="direction = 'sell'">卖出</button>
        </div>
        <div class="ticket">
          <label>股票</label>
          <el-autocomplete v-model="form.code" :fetch-suggestions="querySearch" placeholder="代码或名称，如 600519" clearable @select="onPick">
            <template #default="{ item }">
              <div class="row"><b>{{ item.name }}</b><span class="muted num">{{ item.value }}</span><span class="muted small">{{ item.industry }}</span></div>
            </template>
          </el-autocomplete>
          <div v-if="form.name" class="muted small">{{ form.name }}<template v-if="holding"> · 持仓 {{ holding.quantity }}，可卖 {{ holding.available ?? holding.quantity }}</template></div>

          <label>限价</label>
          <div class="row">
            <el-button size="small" @click="tickPrice(-0.01)">-</el-button>
            <el-input-number v-model="form.price" :min="0" :precision="2" :step="0.01" :controls="false" class="grow" />
            <el-button size="small" @click="tickPrice(0.01)">+</el-button>
            <el-button size="small" link type="primary" @click="fillPrice">取现价</el-button>
          </div>

          <label>数量（股）</label>
          <el-input-number v-model="form.quantity" :min="100" :step="100" step-strictly style="width: 100%" />
          <div class="quick">
            <template v-if="direction === 'buy'">
              <el-button size="small" @click="quick(0.25)">1/4 仓</el-button>
              <el-button size="small" @click="quick(0.5)">1/2 仓</el-button>
              <el-button size="small" @click="quick(1)">全仓</el-button>
            </template>
            <template v-else>
              <el-button size="small" @click="quick(0.5)">卖一半</el-button>
              <el-button size="small" @click="quick(1)">全部卖出</el-button>
            </template>
          </div>

          <div class="estimate">
            <div class="row-between"><span class="muted">预计金额</span><b class="num">{{ num(amount) }}</b></div>
            <div class="row-between"><span class="muted">预计费用</span><span class="num">{{ num(fee) }}</span></div>
            <div v-if="direction === 'buy'" class="row-between"><span class="muted">可用资金</span><span class="num">{{ num(account?.cash) }}</span></div>
          </div>
          <el-button :type="direction === 'buy' ? 'danger' : 'success'" size="large" :loading="submitting" class="submit" @click="submit">
            {{ direction === 'buy' ? '模拟买入' : '模拟卖出' }}
          </el-button>
        </div>
      </SectionCard>

      <SectionCard title="持仓" subtitle="来自腾讯模拟账户" flush>
        <el-table v-if="positions.length" :data="positions" size="small">
          <el-table-column label="股票" min-width="140">
            <template #default="{ row }">
              <router-link :to="`/stock/${row.code}`" class="bold">{{ row.name || row.code }}</router-link>
              <div class="muted small num">{{ row.code }}</div>
            </template>
          </el-table-column>
          <el-table-column label="持仓 / 可卖" width="110"><template #default="{ row }"><span class="num">{{ row.quantity }} / {{ row.available ?? '--' }}</span></template></el-table-column>
          <el-table-column label="成本 / 现价" width="130"><template #default="{ row }"><span class="num">{{ num(row.cost) }} / {{ num(row.price) }}</span></template></el-table-column>
          <el-table-column label="市值" width="100"><template #default="{ row }"><span class="num">{{ money(row.market_value) }}</span></template></el-table-column>
          <el-table-column label="浮动盈亏" width="150">
            <template #default="{ row }"><span class="num" :class="colorClass(row.pnl)">{{ num(row.pnl) }}（{{ pct(row.pnl_pct) }}）</span></template>
          </el-table-column>
          <el-table-column label="" width="70"><template #default="{ row }"><el-button link type="success" @click="fromPosition(row)">卖出</el-button></template></el-table-column>
        </el-table>
        <EmptyState v-else title="当前没有持仓" description="可以在左侧手动下单，或在智能选股页按信号开仓" icon="Wallet" />
      </SectionCard>
    </div>

    <SectionCard title="委托记录" subtitle="本地镜像，关联开仓信号" class="mt" flush>
      <template #extra>
        <el-radio-group v-model="statusFilter" size="small">
          <el-radio-button value="all">全部</el-radio-button>
          <el-radio-button value="open">待成交（{{ openCount }}）</el-radio-button>
          <el-radio-button value="filled">已成交</el-radio-button>
          <el-radio-button value="cancelled">已撤单</el-radio-button>
        </el-radio-group>
      </template>
      <el-table v-if="filteredOrders.length" :data="filteredOrders" size="small" max-height="380">
        <el-table-column label="时间" width="140"><template #default="{ row }"><span class="num">{{ dt(row.time || row.created_at) }}</span></template></el-table-column>
        <el-table-column label="股票" min-width="120"><template #default="{ row }">{{ row.name || row.code }}</template></el-table-column>
        <el-table-column label="方向" width="70">
          <template #default="{ row }"><span class="dir" :class="row.direction">{{ row.direction === 'buy' ? '买入' : '卖出' }}</span></template>
        </el-table-column>
        <el-table-column label="委托" width="130"><template #default="{ row }"><span class="num">{{ row.quantity }} @ {{ num(row.price) }}</span></template></el-table-column>
        <el-table-column label="成交" width="130"><template #default="{ row }"><span class="num">{{ row.filled_qty }} @ {{ num(row.filled_price) }}</span></template></el-table-column>
        <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag size="small" :type="STATUS[row.status]?.type" effect="plain">{{ STATUS[row.status]?.label || row.status }}</el-tag></template></el-table-column>
        <el-table-column label="信号" width="110"><template #default="{ row }"><SignalBadge v-if="row.signal_type" :type="row.signal_type" size="sm" /><span v-else class="muted">--</span></template></el-table-column>
        <el-table-column label="来源" width="70"><template #default="{ row }">{{ SOURCE[row.source] || row.source }}</template></el-table-column>
        <el-table-column label="" width="70">
          <template #default="{ row }">
            <el-button v-if="['pending', 'partial'].includes(row.status) && row.order_id" link type="danger" @click="cancel(row)">撤单</el-button>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState v-else title="没有符合条件的委托" icon="Tickets" />
    </SectionCard>

    <div class="grid grid-2 mt">
      <SectionCard title="资金曲线" subtitle="每日收盘后自动记录快照">
        <div v-if="stats?.equity_curve?.length" :ref="curve.el" style="height: 260px" />
        <EmptyState v-else title="还没有快照" description="收盘后流水线会自动记录，也可以点右上角记录快照" icon="TrendCharts" />
      </SectionCard>
      <SectionCard title="复盘统计" subtitle="按开仓信号统计已平仓的持仓计划">
        <div class="kpis">
          <div><span>已平仓</span><b class="num">{{ stats?.closed_trades ?? '--' }}</b></div>
          <div><span>胜率</span><b class="num">{{ ratioPct(stats?.win_rate, 1) }}</b></div>
          <div><span>盈亏比</span><b class="num">{{ num(stats?.profit_factor) }}</b></div>
          <div><span>最大回撤</span><b class="num down">{{ ratioPct(stats?.max_drawdown, 1) }}</b></div>
        </div>
        <div v-if="stats?.by_signal?.length" :ref="sigChart.el" style="height: 200px" />
        <EmptyState v-else title="还没有已平仓的交易" icon="DataLine" />
      </SectionCard>
    </div>
  </div>
</template>

<style scoped>
.trade {
  grid-template-columns: 360px minmax(0, 1fr);
  align-items: start;
}
.ticket-tabs {
  display: grid;
  grid-template-columns: 1fr 1fr;
  border-bottom: 1px solid var(--c-border);
}
.tab {
  height: 44px;
  border: none;
  background: var(--c-surface-2);
  font-size: 14px;
  font-weight: 600;
  color: var(--c-text-3);
  cursor: pointer;
}
.tab.buy {
  border-top-left-radius: var(--radius);
}
.tab.sell {
  border-top-right-radius: var(--radius);
}
.tab.buy.active {
  background: var(--c-surface);
  color: var(--c-up);
  box-shadow: inset 0 -2px 0 var(--c-up);
}
.tab.sell.active {
  background: var(--c-surface);
  color: var(--c-down);
  box-shadow: inset 0 -2px 0 var(--c-down);
}
.ticket {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ticket label {
  font-size: 12px;
  color: var(--c-text-3);
  margin-top: 4px;
}
.quick {
  display: flex;
  gap: 6px;
}
.estimate {
  margin-top: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--c-surface-2);
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
}
.submit {
  margin-top: 6px;
  width: 100%;
}
.dir.buy {
  color: var(--c-up);
  font-weight: 600;
}
.dir.sell {
  color: var(--c-down);
  font-weight: 600;
}
.kpis {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 8px;
}
.kpis div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--c-surface-2);
}
.kpis span {
  font-size: 11px;
  color: var(--c-text-3);
}
.kpis b {
  font-size: 16px;
}
</style>
