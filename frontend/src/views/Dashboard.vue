<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useChart } from '@/composables/useChart'
import { useJobPoller } from '@/composables/useJobPoller'
import { COLORS, heatColor } from '@/utils/echartsTheme'
import { POSITION_NAMES, REGIME_COLORS, WALK_NAMES, money, num, ratioPct } from '@/utils/format'
import { EmptyState, PageHeader, PriceChange, RiskRewardBar, ScoreBar, ScoreRing, SectionCard, SignalBadge, Sparkline } from '@/components/ui'

const router = useRouter()
const env = ref<any>(null)
const history = ref<any[]>([])
const rec = ref<any>(null)
const plans = ref<any[]>([])
const warnings = ref<any[]>([])
const series = ref<Record<string, any>>({})
const live = ref<Record<string, any>>({})
const loading = ref(true)
const heatPeriod = ref<'ret1' | 'ret5' | 'ret20'>('ret5')

const poller = useJobPoller(() => load())

const b = computed(() => env.value?.breadth || {})
const upRatio = computed(() => {
  const total = (b.value.up || 0) + (b.value.down || 0) + (b.value.flat || 0)
  return total ? { up: (b.value.up / total) * 100, flat: (b.value.flat / total) * 100, down: (b.value.down / total) * 100 } : null
})
const overview = computed(() => env.value?.detail?.tencent_overview)
const topPicks = computed(() => (rec.value?.items || []).slice(0, 6))

const gauge = useChart(
  () => {
    if (!env.value) return null
    return {
      series: [
        {
          type: 'gauge',
          startAngle: 200,
          endAngle: -20,
          min: 0,
          max: 100,
          radius: '100%',
          center: ['50%', '62%'],
          progress: { show: true, width: 12, itemStyle: { color: REGIME_COLORS[env.value.regime] } },
          axisLine: { lineStyle: { width: 12, color: [[1, '#eef1f5']] } },
          pointer: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          anchor: { show: false },
          detail: { valueAnimation: true, fontSize: 34, fontWeight: 700, color: COLORS.text, offsetCenter: [0, '-5%'], formatter: '{value}' },
          title: { offsetCenter: [0, '32%'], fontSize: 13, color: COLORS.text3 },
          data: [{ value: Math.round(env.value.score * 10) / 10, name: '市场温度' }],
        },
      ],
    }
  },
  () => env.value,
)

const heat = useChart(
  () => {
    const sectors = env.value?.sectors || []
    if (!sectors.length) return null
    const key = heatPeriod.value
    const span = key === 'ret1' ? 3 : key === 'ret5' ? 6 : 12
    return {
      tooltip: {
        formatter: (p: any) => {
          const s = p.data.raw
          return `<b>${s.industry}</b>（${s.count} 只）<br/>1日 ${num(s.ret1)}%　5日 ${num(s.ret5)}%　20日 ${num(s.ret20)}%<br/>站上 MA20 ${ratioPct(s.above_ma20)}　强度排名 ${s.rank}`
        },
      },
      series: [
        {
          type: 'treemap',
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          width: '100%',
          height: '100%',
          top: 0,
          left: 0,
          itemStyle: { borderColor: '#fff', borderWidth: 2, gapWidth: 2 },
          label: {
            show: true,
            formatter: (p: any) => `{n|${p.data.raw.industry}}\n{v|${(p.data.raw[key] ?? 0) > 0 ? '+' : ''}${num(p.data.raw[key])}%}`,
            rich: { n: { fontSize: 12, color: '#1f2937', lineHeight: 18 }, v: { fontSize: 12, fontWeight: 600, color: '#111827', fontFamily: 'Menlo, monospace' } },
          },
          data: sectors.map((s: any) => ({
            name: s.industry,
            value: s.count,
            raw: s,
            itemStyle: { color: heatColor(s[key] ?? 0, span) },
          })),
        },
      ],
    }
  },
  () => [env.value, heatPeriod.value],
)

const trend = useChart(
  () => {
    if (!history.value.length) return null
    return {
      grid: { left: 32, right: 12, top: 16, bottom: 24 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: history.value.map((h) => h.trade_date), boundaryGap: false },
      yAxis: { type: 'value', min: 0, max: 100, splitNumber: 4 },
      visualMap: { show: false, pieces: [{ lt: 40, color: COLORS.down }, { gte: 40, lt: 60, color: COLORS.warn }, { gte: 60, color: COLORS.up }] },
      series: [
        {
          type: 'line',
          data: history.value.map((h) => Math.round(h.score * 10) / 10),
          symbol: history.value.length < 3 ? 'circle' : 'none',
          symbolSize: 6,
          lineStyle: { width: 2 },
          areaStyle: { opacity: 0.08 },
          markLine: { silent: true, symbol: 'none', lineStyle: { type: 'dashed', color: '#cbd2dc' }, label: { color: COLORS.text3 }, data: [{ yAxis: 40 }, { yAxis: 60 }] },
        },
      ],
    }
  },
  () => history.value,
)

async function load() {
  loading.value = true
  try {
    const [e, h, r, p, w] = await Promise.all([
      api.marketEnv(),
      api.marketEnvHistory(180),
      api.recommendLatest(),
      api.plans('open'),
      api.events(10, 'warning'),
    ])
    env.value = e
    history.value = h
    rec.value = r
    plans.value = p
    warnings.value = w
  } finally {
    loading.value = false
  }
  api.indicesSeries(60).then((s) => (series.value = s)).catch(() => undefined)
  api.indicesLive().then((rows: any[]) => (live.value = Object.fromEntries(rows.map((x) => [x.code, x])))).catch(() => undefined)
}

async function refresh() {
  await api.marketRefresh()
  ElMessage.success('已开始重新计算市场环境')
  poller.start('market_env')
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading && !env">
    <PageHeader title="市场总览">
      <template #subtitle>
        <template v-if="env">数据日期 {{ env.trade_date }} · 宽度与行业强弱由本地全市场日线计算</template>
      </template>
      <el-button :icon="'Refresh'" :loading="!!poller.running.value.market_env" @click="refresh">重新计算</el-button>
    </PageHeader>

    <SectionCard v-if="!loading && !env">
      <EmptyState title="还没有市场环境数据" description="请先到设置页完成数据初始化，然后运行一次收盘后流水线。">
        <el-button type="primary" @click="router.push('/settings')">去设置</el-button>
      </EmptyState>
    </SectionCard>

    <template v-if="env">
      <!-- 首屏：温度 + 指数 -->
      <div class="grid hero">
        <SectionCard title="市场温度" padding="8px 16px 16px">
          <div :ref="gauge.el" style="height: 170px" />
          <div class="regime-row">
            <span class="regime-tag" :style="{ color: REGIME_COLORS[env.regime], background: REGIME_COLORS[env.regime] + '14' }">
              {{ env.regime_name }}
            </span>
            <span class="muted">建议总仓位上限</span>
            <b class="num cap" :style="{ color: REGIME_COLORS[env.regime] }">{{ ratioPct(env.position_cap) }}</b>
          </div>
          <div class="parts">
            <ScoreBar label="指数状态" :value="env.detail?.parts?.index_part" :digits="1" label-width="60px" />
            <ScoreBar label="市场宽度" :value="env.detail?.parts?.breadth_part" :digits="1" label-width="60px" />
            <ScoreBar label="量能" :value="env.detail?.parts?.volume_part" :digits="1" label-width="60px" />
          </div>
        </SectionCard>

        <div class="grid grid-4">
          <div v-for="i in env.indices" :key="i.code" class="index-card" @click="router.push(`/stock/${i.code}`)">
            <div class="row-between">
              <span class="index-name">{{ i.name }}</span>
              <SignalBadge v-if="i.recent_signal" :type="i.recent_signal.type" :date="i.recent_signal.date" size="sm" />
            </div>
            <PriceChange :price="live[i.code]?.price ?? i.close" :pct="live[i.code]?.change_pct ?? i.change_pct" size="lg" class="index-price" />
            <div class="muted small">近 20 日 <span :class="(i.change_20d ?? 0) >= 0 ? 'up' : 'down'" class="num">{{ num(i.change_20d) }}%</span></div>
            <Sparkline :data="series[i.code]?.closes || []" height="46px" class="spark" />
            <div class="tags">
              <span class="chip">日线 {{ WALK_NAMES[i.day_walk_type] }}</span>
              <span class="chip">{{ POSITION_NAMES[i.day_position] }}</span>
              <span class="chip" :class="{ hot: i.week?.week_uptrend }">周线 {{ WALK_NAMES[i.week?.week_walk_type] || '--' }}</span>
            </div>
            <ScoreBar label="状态分" :value="i.score + 3" :max="6" :digits="2" class="mt-8" />
          </div>
        </div>
      </div>

      <!-- 宽度 + 热力图 -->
      <div class="grid mid">
        <SectionCard title="市场宽度">
          <div class="breadth-head">
            <span class="up num bold">涨 {{ b.up }}</span>
            <span class="muted num">平 {{ b.flat }}</span>
            <span class="down num bold">跌 {{ b.down }}</span>
          </div>
          <div v-if="upRatio" class="stack">
            <div class="seg up-bg" :style="{ width: `${upRatio.up}%` }" />
            <div class="seg flat-bg" :style="{ width: `${upRatio.flat}%` }" />
            <div class="seg down-bg" :style="{ width: `${upRatio.down}%` }" />
          </div>
          <div class="limit-row">
            <div class="limit up-soft"><span class="muted small">涨停</span><b class="num up">{{ b.limit_up }}</b></div>
            <div class="limit down-soft"><span class="muted small">跌停</span><b class="num down">{{ b.limit_down }}</b></div>
            <div class="limit"><span class="muted small">量比</span><b class="num">{{ num(b.amount_ratio_ma20) }}</b></div>
          </div>
          <div class="bars">
            <ScoreBar label="站上MA20" :value="(b.above_ma20 || 0) * 100" suffix="%" label-width="64px" />
            <ScoreBar label="站上MA60" :value="(b.above_ma60 || 0) * 100" suffix="%" label-width="64px" />
          </div>
          <div class="muted small amount">两市成交额 <b class="num text-2">{{ money(env.detail?.live_breadth?.amount) }}</b></div>
        </SectionCard>

        <SectionCard title="行业强弱热力图" subtitle="面积为股票数，颜色为涨跌幅" padding="10px">
          <template #extra>
            <el-radio-group v-model="heatPeriod" size="small">
              <el-radio-button value="ret1">1 日</el-radio-button>
              <el-radio-button value="ret5">5 日</el-radio-button>
              <el-radio-button value="ret20">20 日</el-radio-button>
            </el-radio-group>
          </template>
          <div :ref="heat.el" style="height: 300px" />
        </SectionCard>
      </div>

      <!-- 市场画像 + 温度走势 -->
      <div class="grid grid-2 mt">
        <SectionCard title="腾讯市场画像" :subtitle="overview ? `${overview.date} · 总评 ${overview.adj_score}` : ''">
          <div v-if="overview?.items?.length" class="portrait">
            <div v-for="it in overview.items" :key="it.name" class="portrait-item">
              <ScoreBar :label="it.name" :value="it.score" :max="5" label-width="84px" />
              <div class="muted small status">{{ it.status }}</div>
            </div>
          </div>
          <EmptyState v-else title="暂无画像数据" description="收盘后流水线运行时会从腾讯自选股获取" />
        </SectionCard>
        <SectionCard title="市场温度走势" subtitle="40 以下为弱势，60 以上为强势">
          <div :ref="trend.el" style="height: 280px" />
        </SectionCard>
      </div>

      <!-- 推荐 + 预警 -->
      <div class="grid bottom mt">
        <SectionCard :title="`今日推荐`" :subtitle="rec?.run ? `${rec.run.run_date} · ${rec.run.message}` : ''">
          <template #extra><el-button link type="primary" @click="router.push('/picker')">查看全部</el-button></template>
          <div v-if="topPicks.length" class="grid grid-3">
            <div v-for="it in topPicks" :key="it.code" class="pick" @click="router.push(`/stock/${it.code}`)">
              <div class="row-between">
                <div class="grow">
                  <div class="pick-name">{{ it.name }}</div>
                  <div class="muted small"><span class="num">{{ it.code }}</span> · {{ it.industry }}</div>
                </div>
                <ScoreRing :value="it.score" :size="46" :stroke="5" />
              </div>
              <div class="row pick-mid">
                <SignalBadge :type="it.signal_type" :date="it.signal_date" size="sm" />
                <span class="num bold">{{ num(it.price) }}</span>
              </div>
              <RiskRewardBar :stop="it.stop_price" :price="it.price" :target1="it.target1" :show-labels="false" />
              <div class="row-between small muted num">
                <span class="down">止损 {{ num(it.stop_price) }}</span>
                <span class="up">目标 {{ num(it.target1) }}</span>
              </div>
            </div>
          </div>
          <EmptyState v-else title="暂无推荐" description="收盘后流水线会自动生成，也可以在智能选股页手动扫描" />
        </SectionCard>
        <SectionCard title="持仓与预警">
          <template #extra><el-button link type="primary" @click="router.push('/portfolio')">持仓页</el-button></template>
          <div class="row pos-sum">
            <div class="grow"><div class="muted small">持仓计划</div><div class="num big">{{ plans.length }}</div></div>
            <div class="grow"><div class="muted small">待执行建议</div><div class="num big">{{ plans.filter((p) => p.last_advice && p.last_advice.action !== 'hold').length }}</div></div>
          </div>
          <el-timeline v-if="warnings.length" class="timeline">
            <el-timeline-item v-for="w in warnings" :key="w.id" :timestamp="w.trade_date" type="warning">
              <router-link :to="`/stock/${w.code}`">{{ w.name || w.code }}</router-link>
              <span class="text-2">：{{ w.reason }}</span>
            </el-timeline-item>
          </el-timeline>
          <EmptyState v-else title="暂无预警" icon="Bell" />
        </SectionCard>
      </div>
    </template>
  </div>
</template>

<style scoped>
.hero {
  grid-template-columns: 300px minmax(0, 1fr);
}
.mid {
  grid-template-columns: 340px minmax(0, 1fr);
  margin-top: var(--gap);
}
.bottom {
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
}
.regime-row {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: -6px;
}
.regime-tag {
  padding: 2px 10px;
  border-radius: 999px;
  font-weight: 600;
  font-size: 12px;
}
.cap {
  font-size: 20px;
}
.parts {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.index-card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 14px 16px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
  min-width: 0;
}
.index-card:hover {
  border-color: var(--el-color-primary-light-7);
  box-shadow: var(--shadow);
}
.index-name {
  font-weight: 600;
  color: var(--c-text-2);
}
.index-price {
  margin: 8px 0 2px;
  flex-wrap: wrap;
}
.spark {
  margin: 8px 0;
}
.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
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
.mt-8 {
  margin-top: 10px;
}
.breadth-head {
  display: flex;
  justify-content: space-between;
  font-size: 14px;
}
.stack {
  display: flex;
  height: 10px;
  border-radius: 5px;
  overflow: hidden;
  margin: 8px 0 14px;
  background: #eef1f5;
}
.seg {
  height: 100%;
}
.up-bg {
  background: var(--c-up);
}
.flat-bg {
  background: #cbd2dc;
}
.down-bg {
  background: var(--c-down);
}
.limit-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.limit {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 8px 0;
  border-radius: 8px;
  background: var(--c-surface-2);
}
.limit b {
  font-size: 18px;
}
.up-soft {
  background: var(--c-up-soft);
}
.down-soft {
  background: var(--c-down-soft);
}
.bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 16px;
}
.amount {
  margin-top: 14px;
}
.portrait {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 24px;
}
.status {
  margin: 3px 0 0 92px;
  line-height: 1.4;
}
.pick {
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  padding: 12px 14px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: border-color 0.15s;
  min-width: 0;
}
.pick:hover {
  border-color: var(--el-color-primary-light-7);
}
.pick-name {
  font-weight: 600;
  font-size: 14px;
}
.pick-mid {
  justify-content: space-between;
}
.pos-sum {
  padding-bottom: 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid #f0f2f6;
}
.big {
  font-size: 22px;
  font-weight: 650;
}
.timeline {
  padding-left: 2px;
  max-height: 300px;
  overflow-y: auto;
}
</style>
