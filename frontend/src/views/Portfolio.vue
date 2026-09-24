<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import ChanChart from '@/components/ChanChart.vue'
import { useJobPoller } from '@/composables/useJobPoller'
import { COLORS } from '@/utils/echartsTheme'
import { ACTION_TYPES, colorClass, dt, money, num, ratioPct } from '@/utils/format'
import { EmptyState, PageHeader, RiskRewardBar, SectionCard, SignalBadge, StatCard, StatusSteps } from '@/components/ui'

const router = useRouter()
const tab = ref('open')
const openPlans = ref<any[]>([])
const closedPlans = ref<any[]>([])
const events = ref<any[]>([])
const account = ref<any>(null)
const env = ref<any>(null)
const loading = ref(false)
const drawer = ref<{ visible: boolean; plan: any; chan: any }>({ visible: false, plan: null, chan: null })

const poller = useJobPoller((job) => {
  if (job.job_name === 'evaluate_positions') {
    ElMessage.success(job.message || '评估完成')
    load()
  }
})

const lastPrice = (p: any) => p.last_advice?.close ?? p.entry_price
const floatingR = (p: any) => (p.r_value ? (lastPrice(p) - p.entry_price) / p.r_value : null)
const pnl = (p: any) => (lastPrice(p) - p.entry_price) * p.remaining_qty

const summary = computed(() => {
  const mv = openPlans.value.reduce((s, p) => s + p.remaining_qty * lastPrice(p), 0)
  const floating = openPlans.value.reduce((s, p) => s + pnl(p), 0)
  const equity = account.value?.total_asset || 0
  const pending = openPlans.value.filter((p) => p.last_advice && p.last_advice.action !== 'hold').length
  return { mv, floating, equity, ratio: equity ? mv / equity : 0, pending }
})
const cap = computed(() => env.value?.position_cap ?? null)

const EVENT_LEVEL: Record<string, any> = { warning: 'warning', execute: 'danger', info: 'info' }
const EVENT_NAMES: Record<string, string> = {
  open: '开仓', fill: '成交', stop_move: '止损上移', warning: '预警', advice: '建议', signal: '卖点', close: '关闭', add: '加仓', cancel: '作废',
}

async function load() {
  loading.value = true
  try {
    const [o, c, e] = await Promise.all([api.plans('open'), api.plans('closed'), api.events(200)])
    openPlans.value = o
    closedPlans.value = c
    events.value = e
  } finally {
    loading.value = false
  }
  api.simAccount().then((a) => (account.value = a)).catch(() => undefined)
  api.marketEnv().then((x) => (env.value = x)).catch(() => undefined)
}

async function evaluate() {
  await api.evaluate()
  poller.start('evaluate_positions')
}

async function showDetail(p: any) {
  drawer.value = { visible: true, plan: await api.plan(p.id), chan: null }
  api.stockChan(p.code, 'day', 160).then((c) => (drawer.value.chan = c)).catch(() => undefined)
}

const drawerLines = computed(() => {
  const p = drawer.value.plan
  if (!p) return []
  const out = [
    { name: '止损', value: p.current_stop, color: COLORS.down },
    { name: '成本', value: p.entry_price, color: COLORS.text2 },
  ]
  if (p.target1) out.push({ name: '目标一', value: p.target1, color: COLORS.up })
  return out
})

async function execute(p: any) {
  const a = p.last_advice
  if (!a || a.action === 'hold') return
  const text = a.action === 'add' ? '按建议加仓' : `卖出 ${a.action === 'close' ? p.remaining_qty : a.quantity} 股`
  await ElMessageBox.confirm(`${p.name || p.code}：${text}？\n原因：${a.reasons.join('；')}`, '确认执行建议', { type: 'warning' })
  const order = await api.executeAdvice(p.id)
  ElMessage.success(`已提交模拟委托，状态 ${order.status}`)
  load()
}

async function closeManual(p: any) {
  const { value } = await ElMessageBox.prompt('输入平仓价格（可留空，仅关闭计划）', `手动关闭 ${p.name || p.code}`, {
    inputPattern: /^(\d+(\.\d+)?)?$/,
    inputErrorMessage: '请输入价格',
  })
  await api.closeManual(p.id, value ? Number(value) : undefined, '手动关闭')
  ElMessage.success('已关闭')
  load()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="持仓与策略" subtitle="止损由结构决定，仓位由止损距离决定，止盈由卖点和跟踪止损决定。每日收盘后自动评估，执行前都需要你确认。">
      <el-button type="primary" :icon="'Refresh'" :loading="!!poller.running.value.evaluate_positions" @click="evaluate">立即评估全部持仓</el-button>
    </PageHeader>

    <div class="grid grid-4">
      <StatCard label="持仓计划" :value="openPlans.length" :sub="`待执行建议 ${summary.pending} 条`" />
      <StatCard label="持仓市值" :value="money(summary.mv)" :sub="`模拟账户总资产 ${money(summary.equity)}`" />
      <StatCard label="浮动盈亏" :value="num(summary.floating)" :value-class="colorClass(summary.floating)" sub="按最近一次评估时的收盘价" />
      <StatCard label="仓位 / 市场上限" :value="`${ratioPct(summary.ratio)} / ${cap != null ? ratioPct(cap) : '--'}`">
        <div class="cap-bar">
          <div class="cap-fill" :class="{ over: cap != null && summary.ratio > cap }" :style="{ width: `${Math.min(100, summary.ratio * 100)}%` }" />
          <div v-if="cap != null" class="cap-mark" :style="{ left: `${cap * 100}%` }" />
        </div>
      </StatCard>
    </div>

    <SectionCard class="mt" padding="4px 16px 16px">
      <el-tabs v-model="tab">
        <el-tab-pane :label="`持仓中（${openPlans.length}）`" name="open">
          <div v-loading="loading">
            <EmptyState v-if="!openPlans.length" title="还没有持仓计划" description="在智能选股或个股分析页，对有买点的股票点击“开仓”，系统会按风险计算仓位并提交模拟买单。">
              <el-button type="primary" @click="router.push('/picker')">去智能选股</el-button>
            </EmptyState>
            <div v-for="p in openPlans" :key="p.id" class="plan">
              <div class="plan-left">
                <div class="row">
                  <router-link :to="`/stock/${p.code}`" class="name">{{ p.name || p.code }}</router-link>
                  <span class="muted num small">{{ p.code }}</span>
                </div>
                <div class="row small muted">
                  <SignalBadge v-if="p.entry_signal" :type="p.entry_signal" :date="p.entry_signal_date" size="sm" />
                  <span>开仓 {{ p.entry_date }}</span>
                </div>
                <StatusSteps :status="p.status" class="steps" />
              </div>
              <div class="plan-mid">
                <div class="row nums">
                  <div><span>剩余 / 原始</span><b class="num">{{ p.remaining_qty }} / {{ p.quantity }}</b></div>
                  <div><span>成本</span><b class="num">{{ num(p.entry_price) }}</b></div>
                  <div><span>最新</span><b class="num" :class="colorClass(lastPrice(p) - p.entry_price)">{{ num(lastPrice(p)) }}</b></div>
                  <div><span>浮动 R</span><b class="num" :class="colorClass(floatingR(p))">{{ num(floatingR(p)) }}</b></div>
                  <div><span>浮动盈亏</span><b class="num" :class="colorClass(pnl(p))">{{ num(pnl(p)) }}</b></div>
                </div>
                <RiskRewardBar :stop="p.current_stop" :entry="p.entry_price" :price="lastPrice(p)" :target1="p.target1" :target2="p.target2" />
                <div class="muted small">止损来源：{{ p.stop_source || '--' }}</div>
              </div>
              <div class="plan-right">
                <template v-if="p.last_advice">
                  <div class="row">
                    <el-tag :type="ACTION_TYPES[p.last_advice.action]" effect="dark" size="small">{{ p.last_advice.action_name }}</el-tag>
                    <span v-if="p.last_advice.quantity" class="num small">{{ p.last_advice.quantity }} 股</span>
                    <span class="muted small">{{ p.last_advice.date }}</span>
                  </div>
                  <div class="advice-reason">{{ p.last_advice.reasons?.join('；') }}</div>
                  <div v-for="w in p.last_advice.warnings" :key="w" class="warn-line">{{ w }}</div>
                </template>
                <div v-else class="muted small">尚未评估</div>
                <div class="row plan-actions">
                  <el-button size="small" type="danger" :disabled="!p.last_advice || p.last_advice.action === 'hold'" @click="execute(p)">执行建议</el-button>
                  <el-button size="small" @click="showDetail(p)">详情</el-button>
                  <el-button size="small" link @click="closeManual(p)">关闭</el-button>
                </div>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane :label="`已平仓（${closedPlans.length}）`" name="closed">
          <el-table v-if="closedPlans.length" :data="closedPlans" size="small">
            <el-table-column label="股票" min-width="130">
              <template #default="{ row }"><router-link :to="`/stock/${row.code}`" class="bold">{{ row.name || row.code }}</router-link></template>
            </el-table-column>
            <el-table-column label="开仓信号" width="120">
              <template #default="{ row }"><SignalBadge v-if="row.entry_signal" :type="row.entry_signal" size="sm" /></template>
            </el-table-column>
            <el-table-column prop="entry_date" label="开仓日" width="110" />
            <el-table-column label="成本" width="80"><template #default="{ row }"><span class="num">{{ num(row.entry_price) }}</span></template></el-table-column>
            <el-table-column prop="quantity" label="数量" width="80" />
            <el-table-column label="已实现盈亏" width="120">
              <template #default="{ row }"><span class="num" :class="colorClass(row.realized_pnl)">{{ num(row.realized_pnl) }}</span></template>
            </el-table-column>
            <el-table-column label="R 倍数" width="90">
              <template #default="{ row }">
                <span class="num" :class="colorClass(row.realized_pnl)">{{ num(row.r_value && row.quantity ? row.realized_pnl / (row.r_value * row.quantity) : null) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="平仓时间" width="150"><template #default="{ row }">{{ dt(row.closed_at) }}</template></el-table-column>
            <el-table-column label="" width="80"><template #default="{ row }"><el-button link type="primary" @click="showDetail(row)">详情</el-button></template></el-table-column>
          </el-table>
          <EmptyState v-else title="还没有已平仓的记录" />
        </el-tab-pane>

        <el-tab-pane label="事件记录" name="events">
          <el-table v-if="events.length" :data="events" size="small" height="560">
            <el-table-column prop="trade_date" label="日期" width="110" />
            <el-table-column label="股票" width="140"><template #default="{ row }">{{ row.name || row.code }}</template></el-table-column>
            <el-table-column label="类型" width="100">
              <template #default="{ row }"><el-tag size="small" :type="EVENT_LEVEL[row.level]" effect="plain">{{ EVENT_NAMES[row.event_type] || row.event_type }}</el-tag></template>
            </el-table-column>
            <el-table-column label="价格" width="80"><template #default="{ row }"><span class="num">{{ num(row.price) }}</span></template></el-table-column>
            <el-table-column prop="quantity" label="数量" width="80" />
            <el-table-column prop="reason" label="原因" min-width="300" />
          </el-table>
          <EmptyState v-else title="暂无事件" />
        </el-tab-pane>
      </el-tabs>
    </SectionCard>

    <el-drawer v-model="drawer.visible" :title="`${drawer.plan?.name || ''} · 持仓计划 #${drawer.plan?.id || ''}`" size="620px">
      <template v-if="drawer.plan">
        <StatusSteps :status="drawer.plan.status" />
        <div class="mini-chart">
          <ChanChart
            v-if="drawer.chan"
            :data="drawer.chan"
            :lines="drawerLines"
            :layers="{ volume: false, macd: false, segment: false }"
            :zoom-bars="120"
            height="280px"
          />
          <div v-else v-loading="true" style="height: 280px" />
        </div>
        <div class="kv">
          <div><span>开仓信号</span><b>{{ drawer.plan.entry_signal_name }} · {{ drawer.plan.entry_signal_date }}</b></div>
          <div><span>1R</span><b class="num">{{ num(drawer.plan.r_value, 3) }}</b></div>
          <div><span>初始止损 / 当前止损</span><b class="num">{{ num(drawer.plan.initial_stop) }} → {{ num(drawer.plan.current_stop) }}</b></div>
          <div><span>目标一 / 目标二</span><b class="num">{{ num(drawer.plan.target1) }} / {{ num(drawer.plan.target2) }}</b></div>
          <div><span>已实现盈亏</span><b class="num" :class="colorClass(drawer.plan.realized_pnl)">{{ num(drawer.plan.realized_pnl) }}</b></div>
          <div><span>减仓次数</span><b class="num">{{ drawer.plan.reduce_steps }}</b></div>
        </div>
        <h4 class="tl-title">事件时间线</h4>
        <el-timeline>
          <el-timeline-item
            v-for="e in drawer.plan.events"
            :key="e.id"
            :timestamp="`${e.trade_date} · ${EVENT_NAMES[e.event_type] || e.event_type}`"
            :type="e.level === 'warning' ? 'warning' : e.level === 'execute' ? 'danger' : 'primary'"
          >
            {{ e.reason }}<span v-if="e.price" class="muted num"> @ {{ num(e.price) }}</span>
          </el-timeline-item>
        </el-timeline>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.cap-bar {
  position: relative;
  height: 8px;
  border-radius: 4px;
  background: #eef1f5;
  margin-top: 10px;
}
.cap-fill {
  height: 100%;
  border-radius: 4px;
  background: var(--c-primary);
}
.cap-fill.over {
  background: var(--c-up);
}
.cap-mark {
  position: absolute;
  top: -3px;
  width: 2px;
  height: 14px;
  background: var(--c-text-2);
}
.plan {
  display: grid;
  grid-template-columns: 240px minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 24px;
  padding: 16px 4px;
  border-bottom: 1px solid #f0f2f6;
  align-items: start;
}
.plan:last-child {
  border-bottom: none;
}
.name {
  font-weight: 650;
  font-size: 15px;
  color: var(--c-text);
}
.plan-left {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.steps {
  margin-top: 4px;
}
.plan-mid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.nums {
  gap: 22px;
  flex-wrap: wrap;
}
.nums div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nums span {
  font-size: 11px;
  color: var(--c-text-3);
}
.nums b {
  font-size: 14px;
}
.plan-right {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.advice-reason {
  font-size: 12px;
  color: var(--c-text-2);
  line-height: 1.6;
}
.warn-line {
  font-size: 12px;
  color: #b45309;
  background: var(--c-warn-soft);
  border-radius: 6px;
  padding: 4px 8px;
}
.plan-actions {
  margin-top: 4px;
}
.mini-chart {
  margin: 14px 0;
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  padding: 6px;
}
.kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 16px;
}
.kv div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kv span {
  font-size: 11px;
  color: var(--c-text-3);
}
.tl-title {
  margin: 20px 0 12px;
}
</style>
