<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import ChanChart from '@/components/ChanChart.vue'
import EntryDialog from '@/components/EntryDialog.vue'
import { useChart } from '@/composables/useChart'
import { COLORS } from '@/utils/echartsTheme'
import { POSITION_NAMES, WALK_NAMES, money, num, pct, percent } from '@/utils/format'
import { EmptyState, PriceChange, RiskRewardBar, ScoreBar, ScoreRing, SectionCard, SignalBadge, StatusSteps } from '@/components/ui'

const route = useRoute()
const code = computed(() => String(route.params.code))
const info = ref<any>(null)
const chan = ref<any>(null)
const quote = ref<any>(null)
const fundRows = ref<any[]>([])
const plans = ref<any[]>([])
const level = ref<'day' | 'week'>('day')
const bars = ref(400)
const layers = ref({ bi: true, segment: true, zhongshu: true, signals: true, volume: true, macd: true })
const loading = ref(false)
const entryVisible = ref(false)
const tab = ref('signals')

const isIndex = computed(() => !!info.value?.basic?.is_index)
const plan = computed(() => plans.value.find((p) => p.code === code.value))
const summary = computed(() => chan.value?.summary || {})
const ml = computed(() => chan.value?.multi_level || {})
const fund = computed(() => info.value?.fundamental)
const entryPlan = computed(() => (level.value === 'day' ? chan.value?.entry_plan : null))
const recentSignals = computed(() => [...(chan.value?.signals || [])].reverse().slice(0, 30))
const lastBar = computed(() => chan.value?.bars?.[chan.value.bars.length - 1])
const price = computed(() => quote.value?.price ?? lastBar.value?.[4])
const lines = computed(() => {
  const out: any[] = []
  if (plan.value) {
    out.push({ name: '止损', value: plan.value.current_stop, color: COLORS.down })
    out.push({ name: '成本', value: plan.value.entry_price, color: COLORS.text2 })
    if (plan.value.target1) out.push({ name: '目标一', value: plan.value.target1, color: COLORS.up })
  } else if (entryPlan.value) {
    out.push({ name: '建议止损', value: entryPlan.value.stop, color: COLORS.down })
    if (entryPlan.value.target1) out.push({ name: '目标一', value: entryPlan.value.target1, color: COLORS.up })
  }
  return out
})

const DIRECTION = (d: number) => (d === 1 ? '向上' : d === -1 ? '向下' : '--')

const finChart = useChart(
  () => {
    const rows = fundRows.value
    if (!rows.length) return null
    return {
      grid: { left: 56, right: 48, top: 36, bottom: 28 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0, data: ['营收（亿）', '归母净利（亿）', 'ROE(TTM) %'] },
      xAxis: { type: 'category', data: rows.map((r) => r.report_date) },
      yAxis: [{ type: 'value', name: '亿元' }, { type: 'value', name: '%', splitLine: { show: false } }],
      series: [
        { name: '营收（亿）', type: 'bar', barMaxWidth: 22, data: rows.map((r) => (r.revenue ? +(r.revenue / 1e8).toFixed(2) : null)), itemStyle: { color: '#c7d7ff' } },
        { name: '归母净利（亿）', type: 'bar', barMaxWidth: 22, data: rows.map((r) => (r.net_profit != null ? +(r.net_profit / 1e8).toFixed(2) : null)), itemStyle: { color: COLORS.primary } },
        { name: 'ROE(TTM) %', type: 'line', yAxisIndex: 1, data: rows.map((r) => (r.roe != null ? +r.roe.toFixed(2) : null)), itemStyle: { color: COLORS.warn } },
      ],
    }
  },
  () => [fundRows.value, tab.value],
)

async function loadChan() {
  loading.value = true
  try {
    chan.value = await api.stockChan(code.value, level.value, bars.value)
  } finally {
    loading.value = false
  }
}

async function load() {
  info.value = await api.stockInfo(code.value)
  await loadChan()
  api.stockQuote(code.value).then((q) => (quote.value = q)).catch(() => undefined)
  api.stockFundamentals(code.value).then((r) => (fundRows.value = r)).catch(() => undefined)
  api.plans('open').then((p) => (plans.value = p)).catch(() => undefined)
}

async function toggleWatch() {
  if (info.value.in_watchlist) await api.delWatch(code.value)
  else await api.addWatch(code.value)
  info.value.in_watchlist = !info.value.in_watchlist
  ElMessage.success(info.value.in_watchlist ? '已加入自选' : '已移出自选')
}

watch([level, bars], loadChan)
onMounted(load)
</script>

<template>
  <div class="page">
    <!-- 头部 -->
    <SectionCard v-if="info" padding="16px 20px">
      <div class="header">
        <div class="grow">
          <div class="row wrap">
            <h1 class="title">{{ info.basic.name }}</h1>
            <span class="muted num">{{ info.basic.code }}</span>
            <el-tag v-if="info.basic.industry" size="small" effect="plain">{{ info.basic.industry }}</el-tag>
            <el-tag v-if="info.basic.is_st" size="small" type="danger">ST</el-tag>
            <el-tag v-if="isIndex" size="small" type="info">指数</el-tag>
          </div>
          <div class="price-line">
            <PriceChange :price="price" :change="quote && quote.prev_close ? quote.price - quote.prev_close : undefined" :pct="quote?.change_pct" size="lg" />
            <span v-if="quote?.dt" class="muted small">{{ quote.dt }}</span>
          </div>
        </div>
        <div class="stats">
          <div><span>今开</span><b class="num">{{ num(quote?.open) }}</b></div>
          <div><span>最高</span><b class="num up">{{ num(quote?.high) }}</b></div>
          <div><span>最低</span><b class="num down">{{ num(quote?.low) }}</b></div>
          <div><span>昨收</span><b class="num">{{ num(quote?.prev_close) }}</b></div>
          <div><span>市盈率 TTM</span><b class="num">{{ num(quote?.pe_ttm, 1) }}</b></div>
          <div><span>市净率</span><b class="num">{{ num(quote?.pb) }}</b></div>
          <div><span>总市值</span><b class="num">{{ money(quote?.total_mv) }}</b></div>
          <div><span>成交额</span><b class="num">{{ money(quote?.amount) }}</b></div>
        </div>
        <div class="head-actions">
          <el-button :icon="info.in_watchlist ? 'StarFilled' : 'Star'" @click="toggleWatch">{{ info.in_watchlist ? '已自选' : '加自选' }}</el-button>
          <el-button v-if="entryPlan && !plan && !isIndex" type="danger" @click="entryVisible = true">按信号开仓</el-button>
        </div>
      </div>
    </SectionCard>

    <div class="grid body mt">
      <!-- 主图 -->
      <SectionCard padding="8px 12px 12px">
        <template #title>
          <el-radio-group v-model="level" size="small">
            <el-radio-button value="day">日线</el-radio-button>
            <el-radio-button value="week">周线</el-radio-button>
          </el-radio-group>
        </template>
        <template #extra>
          <el-checkbox v-model="layers.bi" size="small">笔</el-checkbox>
          <el-checkbox v-model="layers.segment" size="small">线段</el-checkbox>
          <el-checkbox v-model="layers.zhongshu" size="small">中枢</el-checkbox>
          <el-checkbox v-model="layers.signals" size="small">买卖点</el-checkbox>
          <el-checkbox v-model="layers.volume" size="small">成交量</el-checkbox>
          <el-checkbox v-model="layers.macd" size="small">MACD</el-checkbox>
          <el-select v-model="bars" size="small" style="width: 104px">
            <el-option :value="250" label="近 250 根" />
            <el-option :value="400" label="近 400 根" />
            <el-option :value="800" label="近 800 根" />
          </el-select>
        </template>
        <div v-loading="loading">
          <div class="legend muted small">
            <span><i class="lg bi" />笔（虚线未确认）</span>
            <span><i class="lg seg" />线段</span>
            <span><i class="lg zs" />笔中枢</span>
            <span><i class="lg zs2" />线段中枢</span>
            <span>“段”为线段级别信号；标记带 ? 为未确认，灰色为已失效；MACD 浅色区为背驰段</span>
          </div>
          <ChanChart v-if="chan" :data="chan" :lines="lines" :layers="layers" height="640px" />
        </div>
      </SectionCard>

      <!-- 右侧面板 -->
      <div class="side">
        <SectionCard :title="`结构概览 · ${level === 'day' ? '日线' : '周线'}`">
          <div class="kv">
            <div><span>走势类型</span><b :class="summary.walk_type === 'up_trend' ? 'up' : summary.walk_type === 'down_trend' ? 'down' : ''">{{ WALK_NAMES[summary.walk_type] }}</b></div>
            <div><span>相对中枢</span><b>{{ POSITION_NAMES[summary.position] }}</b></div>
            <div><span>最后一笔</span><b>{{ DIRECTION(summary.last_bi_direction) }}{{ summary.last_bi_extending ? '（延伸中）' : '' }}</b></div>
            <div><span>MACD</span><b>{{ summary.macd_above_zero ? '零轴上方' : '零轴下方' }}</b></div>
            <div v-if="summary.last_zhongshu" class="full">
              <span>最近中枢</span><b class="num">{{ num(summary.last_zhongshu.zd) }} ~ {{ num(summary.last_zhongshu.zg) }}</b>
            </div>
            <div class="full"><span>笔 / 线段 / 中枢</span><b class="num">{{ summary.bi_count }} / {{ summary.segment_count }} / {{ summary.zhongshu_count }}</b></div>
          </div>
        </SectionCard>

        <SectionCard title="周线与日线联立">
          <div class="levels">
            <div class="lv">
              <span class="lv-name">周线</span>
              <span class="chip" :class="{ hot: ml.week_uptrend }">{{ WALK_NAMES[ml.week_walk_type] || '--' }}</span>
              <span class="chip">{{ POSITION_NAMES[ml.week_position] || '--' }}</span>
              <span class="chip">{{ DIRECTION(ml.week_direction) }}</span>
            </div>
            <div v-if="level === 'day'" class="lv">
              <span class="lv-name">日线</span>
              <span class="chip">{{ WALK_NAMES[summary.walk_type] }}</span>
              <span class="chip">{{ POSITION_NAMES[summary.position] }}</span>
              <span class="chip">{{ DIRECTION(summary.last_bi_direction) }}</span>
            </div>
          </div>
          <div class="resonance">
            <span class="muted small">共振分</span>
            <div class="res-track">
              <div class="res-mid" />
              <div class="res-fill" :style="{ left: (ml.resonance ?? 0) >= 0 ? '50%' : `${50 + (ml.resonance ?? 0) * 50}%`, width: `${Math.abs(ml.resonance ?? 0) * 50}%`, background: (ml.resonance ?? 0) >= 0 ? 'var(--c-up)' : 'var(--c-down)' }" />
            </div>
            <b class="num" :class="(ml.resonance ?? 0) >= 0 ? 'up' : 'down'">{{ num(ml.resonance) }}</b>
          </div>
          <ul class="reasons small">
            <li v-for="n in ml.notes || []" :key="n">{{ n }}</li>
          </ul>
        </SectionCard>

        <SectionCard v-if="plan" title="当前持仓计划">
          <StatusSteps :status="plan.status" />
          <RiskRewardBar class="mt-12" :stop="plan.current_stop" :entry="plan.entry_price" :price="price" :target1="plan.target1" :target2="plan.target2" />
          <div class="kv mt-12">
            <div><span>剩余数量</span><b class="num">{{ plan.remaining_qty }}</b></div>
            <div><span>最新建议</span><b>{{ plan.last_advice?.action_name || '--' }}</b></div>
            <div class="full"><span>止损来源</span><b class="small">{{ plan.stop_source }}</b></div>
          </div>
        </SectionCard>
        <SectionCard v-else-if="entryPlan" :title="`开仓参考 · ${entryPlan.signal_name}`">
          <template #extra><SignalBadge :type="entryPlan.signal_type" :date="entryPlan.signal_date" size="sm" /></template>
          <RiskRewardBar :stop="entryPlan.stop" :price="entryPlan.entry_price" :target1="entryPlan.target1" :target2="entryPlan.target2" />
          <div class="kv mt-12">
            <div><span>建议数量</span><b class="num">{{ entryPlan.quantity }} 股</b></div>
            <div><span>建议金额</span><b class="num">{{ money(entryPlan.position_value) }}</b></div>
            <div><span>1R</span><b class="num">{{ num(entryPlan.r_value, 3) }}</b></div>
            <div><span>是否可开仓</span><b :class="entryPlan.allowed ? 'up' : 'muted'">{{ entryPlan.allowed ? '满足条件' : '不满足' }}</b></div>
          </div>
          <div v-for="w in entryPlan.warnings" :key="w" class="warn-line">{{ w }}</div>
        </SectionCard>

        <SectionCard v-if="fund" title="基本面">
          <template #extra>
            <el-tag size="small" :type="fund.passed ? 'success' : 'danger'" effect="plain">{{ fund.passed ? '通过硬过滤' : '未通过' }}</el-tag>
          </template>
          <div class="row fund-head">
            <ScoreRing :value="fund.score" :size="64" label="基本面" />
            <div class="grow">
              <ScoreBar label="ROE" :value="fund.metrics.roe" :max="25" suffix="%" :digits="1" label-width="54px" />
              <ScoreBar label="净利同比" :value="Math.max(0, fund.metrics.profit_yoy ?? 0)" :max="60" suffix="%" :digits="0" label-width="54px" color="#8b5cf6" />
              <ScoreBar label="腾讯评分" :value="fund.metrics.funm_score" label-width="54px" color="#14b8a6" />
            </div>
          </div>
          <div class="kv mt-12">
            <div><span>PE(TTM)</span><b class="num">{{ num(fund.metrics.pe_ttm, 1) }}</b></div>
            <div><span>行业 PE 分位</span><b class="num">{{ percent(fund.metrics.pe_industry_pct, 0) }}</b></div>
            <div><span>负债率</span><b class="num">{{ percent(fund.metrics.debt_ratio, 1) }}</b></div>
            <div><span>营收同比</span><b class="num">{{ pct(fund.metrics.revenue_yoy, 1) }}</b></div>
          </div>
          <div class="fund-notes">
            <div v-for="r in fund.reasons" :key="r" class="down small">✕ {{ r }}</div>
            <div v-for="h in fund.highlights" :key="h" class="up small">✓ {{ h }}</div>
            <div class="muted small">数据来源：{{ fund.metrics.source === 'financials' ? '三大报表' : '腾讯诊股评分近似' }}</div>
          </div>
        </SectionCard>
      </div>
    </div>

    <SectionCard class="mt" padding="4px 16px 16px">
      <el-tabs v-model="tab">
        <el-tab-pane :label="`买卖点（${recentSignals.length}）`" name="signals">
          <el-table v-if="recentSignals.length" :data="recentSignals" size="small">
            <el-table-column label="信号" width="150">
              <template #default="{ row }">
                <SignalBadge :type="row.type" :confirmed="row.confirmed" :invalid="!!row.extra?.invalidated" :scope="row.scope" :strong="!!row.extra?.divergence?.strong" />
              </template>
            </el-table-column>
            <el-table-column label="级别" width="80"><template #default="{ row }">{{ row.scope === 'seg' ? '线段级别' : '笔级别' }}</template></el-table-column>
            <el-table-column prop="dt" label="日期" width="110" />
            <el-table-column label="价格" width="90"><template #default="{ row }"><span class="num">{{ num(row.price) }}</span></template></el-table-column>
            <el-table-column label="力度" width="130"><template #default="{ row }"><ScoreBar :value="row.strength * 100" /></template></el-table-column>
            <el-table-column label="止损 / 目标一" width="140">
              <template #default="{ row }"><span class="num"><span class="down">{{ num(row.stop_price) }}</span> / <span class="up">{{ num(row.target1) }}</span></span></template>
            </el-table-column>
            <el-table-column label="背驰指标" width="180">
              <template #default="{ row }">
                <div v-if="row.extra?.divergence" class="div-flags">
                  <span :class="{ on: row.extra.divergence.area_divergent }">面积</span>
                  <span :class="{ on: row.extra.divergence.dif_divergent }">DIF</span>
                  <span :class="{ on: row.extra.divergence.slope_divergent }">斜率</span>
                  <span :class="{ on: row.extra.divergence.volume_divergent }">量能</span>
                </div>
                <span v-else class="muted">--</span>
              </template>
            </el-table-column>
            <el-table-column prop="desc" label="说明" min-width="280" />
          </el-table>
          <EmptyState v-else title="当前窗口内没有买卖点" />
        </el-tab-pane>
        <el-tab-pane v-if="!isIndex" label="财务趋势" name="fund">
          <div v-if="fundRows.length" :ref="finChart.el" style="height: 300px" />
          <EmptyState v-else title="还没有这只股票的三大报表" description="财报只为候选股、持仓和自选股拉取（腾讯接口限频）。加入自选后，下次基本面同步会补上。" />
          <el-table v-if="fundRows.length" :data="[...fundRows].reverse()" size="small" class="mt">
            <el-table-column prop="report_date" label="报告期" width="110" />
            <el-table-column label="营收"><template #default="{ row }"><span class="num">{{ money(row.revenue) }}</span></template></el-table-column>
            <el-table-column label="归母净利"><template #default="{ row }"><span class="num">{{ money(row.net_profit) }}</span></template></el-table-column>
            <el-table-column label="营收同比"><template #default="{ row }"><span class="num">{{ pct(row.revenue_yoy, 1) }}</span></template></el-table-column>
            <el-table-column label="净利同比"><template #default="{ row }"><span class="num">{{ pct(row.profit_yoy, 1) }}</span></template></el-table-column>
            <el-table-column label="ROE(TTM)"><template #default="{ row }"><span class="num">{{ num(row.roe, 1) }}</span></template></el-table-column>
            <el-table-column label="负债率"><template #default="{ row }"><span class="num">{{ num(row.debt_ratio, 1) }}</span></template></el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </SectionCard>

    <EntryDialog
      v-if="entryPlan"
      v-model="entryVisible"
      :code="code"
      :name="info?.basic?.name"
      :signal-type="entryPlan.signal_type"
      :signal-date="entryPlan.signal_date"
      @done="load"
    />
  </div>
</template>

<style scoped>
.header {
  display: flex;
  align-items: center;
  gap: 24px;
}
.title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}
.price-line {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-top: 6px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(90px, 1fr));
  gap: 8px 24px;
}
.stats div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stats span {
  font-size: 11px;
  color: var(--c-text-3);
}
.stats b {
  font-size: 14px;
  font-weight: 600;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.body {
  grid-template-columns: minmax(0, 1fr) 340px;
  align-items: start;
}
.side {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding: 2px 4px 6px;
}
.lg {
  display: inline-block;
  width: 16px;
  height: 8px;
  margin-right: 5px;
  vertical-align: middle;
  border-radius: 2px;
}
.lg.bi {
  height: 2px;
  background: #2f6bff;
}
.lg.seg {
  height: 3px;
  background: #8b5cf6;
}
.lg.zs {
  background: rgba(245, 158, 11, 0.2);
  border: 1px solid rgba(245, 158, 11, 0.8);
}
.lg.zs2 {
  background: rgba(139, 92, 246, 0.06);
  border: 1px dashed rgba(139, 92, 246, 0.6);
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
.kv .full {
  grid-column: 1 / -1;
}
.kv span {
  font-size: 11px;
  color: var(--c-text-3);
}
.kv b {
  font-size: 13px;
  font-weight: 600;
}
.levels {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.lv {
  display: flex;
  align-items: center;
  gap: 6px;
}
.lv-name {
  width: 34px;
  font-size: 12px;
  color: var(--c-text-3);
}
.chip {
  font-size: 11px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
  color: var(--c-text-2);
}
.chip.hot {
  color: var(--c-up);
  border-color: #f8c9ca;
  background: var(--c-up-soft);
}
.resonance {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 0 6px;
}
.res-track {
  position: relative;
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: #eef1f5;
}
.res-mid {
  position: absolute;
  left: 50%;
  top: -2px;
  width: 1px;
  height: 12px;
  background: #b8bfca;
}
.res-fill {
  position: absolute;
  top: 0;
  height: 100%;
  border-radius: 4px;
}
.mt-12 {
  margin-top: 12px;
}
.warn-line {
  margin-top: 8px;
  font-size: 12px;
  color: #b45309;
  background: var(--c-warn-soft);
  border-radius: 6px;
  padding: 5px 8px;
}
.fund-head {
  gap: 14px;
  align-items: center;
}
.fund-head .grow {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.div-flags {
  display: flex;
  gap: 4px;
}
.div-flags span {
  font-size: 11px;
  padding: 0 6px;
  border-radius: 4px;
  background: #f1f3f6;
  color: var(--c-text-3);
}
.div-flags span.on {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 600;
}
.fund-notes {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
</style>
