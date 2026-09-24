<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import EntryDialog from '@/components/EntryDialog.vue'
import { useJobPoller } from '@/composables/useJobPoller'
import { num, ratioPct } from '@/utils/format'
import { EmptyState, PageHeader, RiskRewardBar, ScoreBar, ScoreRing, SectionCard, SignalBadge, StatCard } from '@/components/ui'

const router = useRouter()
const data = ref<any>(null)
const runs = ref<any[]>([])
const runId = ref<number | undefined>()
const loading = ref(false)
const view = ref<'card' | 'table'>((localStorage.getItem('picker-view') as any) || 'card')
const filterSignals = ref<string[]>([])
const filterIndustry = ref('')
const minScore = ref(0)
const onlyGoodRR = ref(false)
const pool = ref<'main' | 'watch'>('main')
const sortBy = ref<'score' | 'rr' | 'chan' | 'fund' | 'date'>('score')
const expanded = ref<Set<string>>(new Set())
const entry = ref<{ visible: boolean; row: any }>({ visible: false, row: null })

const poller = useJobPoller((job) => {
  if (job.job_name === 'recommend' || job.job_name === 'post_close_pipeline') {
    ElMessage[job.status === 'success' ? 'success' : 'error'](job.message || '推荐任务结束')
    load()
  }
})

function rr(row: any) {
  const risk = row.price - row.stop_price
  return risk > 0 && row.target1 ? (row.target1 - row.price) / risk : null
}

const source = computed<any[]>(() => (pool.value === 'main' ? data.value?.items : data.value?.watch) || [])
const industries = computed(() => Array.from(new Set<string>(source.value.map((i: any) => i.industry).filter(Boolean))))
const items = computed(() => {
  const list = source.value.filter(
    (i: any) =>
      (!filterSignals.value.length || filterSignals.value.includes(i.signal_type)) &&
      (!filterIndustry.value || i.industry === filterIndustry.value) &&
      i.score >= minScore.value &&
      (!onlyGoodRR.value || (rr(i) ?? 0) >= 2),
  )
  const key: Record<string, (x: any) => number> = {
    score: (x) => x.score,
    rr: (x) => rr(x) ?? -99,
    chan: (x) => x.chan_score,
    fund: (x) => x.fund_score,
    date: (x) => Date.parse(x.signal_date),
  }
  return [...list].sort((a, b) => key[sortBy.value](b) - key[sortBy.value](a))
})
const stats = computed(() => {
  const all = source.value
  const by = (t: string) => all.filter((i: any) => i.signal_type === t).length
  return {
    total: all.length, b1: by('B1'), b2: by('B2'), b3: by('B3'), goodRR: all.filter((i: any) => (rr(i) ?? 0) >= 2).length,
    seg: all.filter((i: any) => i.scope === 'seg').length, strong: all.filter((i: any) => i.strong_div).length,
  }
})

function setView(v: 'card' | 'table') {
  view.value = v
  localStorage.setItem('picker-view', v)
}
function toggle(code: string) {
  const s = new Set(expanded.value)
  s.has(code) ? s.delete(code) : s.add(code)
  expanded.value = s
}

async function load(id?: number) {
  loading.value = true
  try {
    runs.value = await api.recommendRuns()
    data.value = id ? await api.recommendRun(id) : await api.recommendLatest()
    runId.value = data.value?.run?.id
  } finally {
    loading.value = false
  }
}

async function trigger() {
  await api.recommendTrigger()
  ElMessage.success('已开始全市场扫描')
  poller.start('recommend')
}

async function addWatch(row: any) {
  await api.addWatch(row.code, `${row.signal_name} ${row.signal_date}`)
  ElMessage.success(`已把 ${row.name} 加入自选`)
}

onMounted(() => {
  load()
  poller.tick().then(() => {
    if (Object.keys(poller.running.value).length) poller.start()
  })
})
</script>

<template>
  <div class="page">
    <PageHeader title="智能选股">
      <template #subtitle>
        <template v-if="data?.run">批次 #{{ data.run.id }} · 数据日期 {{ data.run.run_date }} · {{ data.run.message }}</template>
      </template>
      <el-select v-model="runId" placeholder="历史批次" style="width: 220px" @change="(v: number) => load(v)">
        <el-option v-for="r in runs" :key="r.id" :value="r.id" :label="`#${r.id} ${r.run_date} · ${r.total_selected} 只`" />
      </el-select>
      <el-button type="primary" :icon="'Search'" :loading="!!poller.running.value.recommend" @click="trigger">
        {{ poller.running.value.recommend ? `扫描中 ${poller.running.value.recommend.progress}/${poller.running.value.recommend.total}` : '重新扫描全市场' }}
      </el-button>
    </PageHeader>

    <div v-if="data?.run" class="grid grid-4">
      <StatCard :label="pool === 'main' ? '主推荐' : '观察池'" :value="stats.total" :sub="pool === 'main' ? `基本面与风险初筛 ${data.run.total_scanned} 只` : '信号所在的笔或线段尚未确认'" />
      <StatCard label="信号构成" :value="`${stats.b1} / ${stats.b2} / ${stats.b3}`" :sub="`一买 / 二买 / 三买 · 线段级别 ${stats.seg} · 强背驰 ${stats.strong}`" />
      <StatCard label="盈亏比 ≥ 2" :value="stats.goodRR" :sub="`占 ${stats.total ? Math.round((stats.goodRR / stats.total) * 100) : 0}%`" />
      <StatCard label="市场仓位上限" :value="ratioPct(data.run.position_cap)" sub="开仓数量会按此上限约束" />
    </div>

    <SectionCard class="mt" padding="12px 16px">
      <div class="toolbar">
        <el-radio-group v-model="pool" size="small">
          <el-radio-button value="main">主推荐（{{ data?.items?.length || 0 }}）</el-radio-button>
          <el-radio-button value="watch">观察池（{{ data?.watch?.length || 0 }}）</el-radio-button>
        </el-radio-group>
        <el-checkbox-group v-model="filterSignals" size="small">
          <el-checkbox-button value="B1">一买</el-checkbox-button>
          <el-checkbox-button value="B2">二买</el-checkbox-button>
          <el-checkbox-button value="B3">三买</el-checkbox-button>
        </el-checkbox-group>
        <el-select v-model="filterIndustry" clearable placeholder="全部行业" size="small" style="width: 140px">
          <el-option v-for="i in industries" :key="i" :value="i" :label="i" />
        </el-select>
        <div class="row small"><span class="muted">最低综合分</span><el-slider v-model="minScore" :max="100" size="small" style="width: 140px" /></div>
        <el-switch v-model="onlyGoodRR" size="small" active-text="仅盈亏比 ≥ 2" />
        <el-select v-model="sortBy" size="small" style="width: 130px">
          <el-option value="score" label="按综合分" />
          <el-option value="rr" label="按盈亏比" />
          <el-option value="chan" label="按缠论分" />
          <el-option value="fund" label="按基本面分" />
          <el-option value="date" label="按信号日期" />
        </el-select>
        <span class="muted small grow">共 {{ items.length }} 只</span>
        <el-radio-group :model-value="view" size="small" @change="(v: any) => setView(v)">
          <el-radio-button value="card"><el-icon><Grid /></el-icon></el-radio-button>
          <el-radio-button value="table"><el-icon><List /></el-icon></el-radio-button>
        </el-radio-group>
      </div>
    </SectionCard>

    <div v-loading="loading" class="mt">
      <SectionCard v-if="!loading && !items.length">
        <EmptyState :title="pool === 'main' ? '暂无主推荐' : '观察池为空'" description="数据初始化完成后，每个交易日收盘后会自动生成；也可以点右上角重新扫描。">
          <el-button type="primary" @click="trigger">重新扫描全市场</el-button>
        </EmptyState>
      </SectionCard>

      <div v-else-if="view === 'card'" class="cards">
        <div v-for="it in items" :key="it.code" class="pick-card">
          <div class="head">
            <div class="rank num">{{ it.rank }}</div>
            <div class="grow">
              <div class="row">
                <router-link :to="`/stock/${it.code}`" class="name">{{ it.name }}</router-link>
                <SignalBadge :type="it.signal_type" :date="it.signal_date" :scope="it.scope" :strong="it.strong_div" :confirmed="it.confirmed" size="sm" />
              </div>
              <div class="muted small">
                <span class="num">{{ it.code }}</span> · {{ it.industry }}
                <template v-if="it.avg_amount"> · 日均成交 <span class="num">{{ num(it.avg_amount / 1e8, 1) }}</span> 亿</template>
              </div>
            </div>
            <ScoreRing :value="it.score" :size="54" label="综合" />
          </div>
          <div class="price-row">
            <span class="muted small">现价</span>
            <b class="num price">{{ num(it.price) }}</b>
            <span class="muted small">止损距离</span>
            <b class="num down">{{ ratioPct((it.price - it.stop_price) / it.price, 1) }}</b>
          </div>
          <RiskRewardBar :stop="it.stop_price" :price="it.price" :target1="it.target1" :target2="it.target2" />
          <div class="scores">
            <ScoreBar label="缠论" :value="it.chan_score" color="#2f6bff" label-width="40px" />
            <ScoreBar label="基本面" :value="it.fund_score" color="#8b5cf6" label-width="40px" />
            <ScoreBar label="行业" :value="it.sector_score" color="#14b8a6" label-width="40px" />
          </div>
          <div class="reason-first">{{ it.reasons?.[0] }}</div>
          <div v-if="it.risk_labels?.length" class="risk-row">
            <span v-for="r in it.risk_labels" :key="r" class="risk-chip">{{ r }}</span>
          </div>
          <ul v-if="expanded.has(it.code)" class="reasons small">
            <li v-for="r in it.reasons.slice(1)" :key="r">{{ r }}</li>
          </ul>
          <div class="actions">
            <el-button link size="small" @click="toggle(it.code)">{{ expanded.has(it.code) ? '收起理由' : `全部理由（${it.reasons?.length || 0}）` }}</el-button>
            <span class="grow" />
            <el-button size="small" @click="addWatch(it)">加自选</el-button>
            <el-button size="small" @click="router.push(`/stock/${it.code}`)">分析</el-button>
            <el-button size="small" type="danger" @click="entry = { visible: true, row: it }">开仓</el-button>
          </div>
        </div>
      </div>

      <SectionCard v-else flush>
        <el-table :data="items" row-key="code" size="small">
          <el-table-column type="expand">
            <template #default="{ row }">
              <ul class="reasons expand">
                <li v-for="r in row.reasons" :key="r">{{ r }}</li>
              </ul>
            </template>
          </el-table-column>
          <el-table-column prop="rank" label="#" width="46" />
          <el-table-column label="股票" min-width="140">
            <template #default="{ row }">
              <router-link :to="`/stock/${row.code}`" class="bold">{{ row.name }}</router-link>
              <div class="muted small num">{{ row.code }} · {{ row.industry }}</div>
            </template>
          </el-table-column>
          <el-table-column label="信号" width="120">
            <template #default="{ row }"><SignalBadge :type="row.signal_type" :date="row.signal_date" :scope="row.scope" :strong="row.strong_div" :confirmed="row.confirmed" size="sm" /></template>
          </el-table-column>
          <el-table-column label="风险收益" min-width="220">
            <template #default="{ row }">
              <RiskRewardBar :stop="row.stop_price" :price="row.price" :target1="row.target1" :target2="row.target2" />
            </template>
          </el-table-column>
          <el-table-column label="缠论 / 基本面 / 行业" width="200">
            <template #default="{ row }">
              <ScoreBar label="缠论" :value="row.chan_score" label-width="36px" />
              <ScoreBar label="基本" :value="row.fund_score" color="#8b5cf6" label-width="36px" />
              <ScoreBar label="行业" :value="row.sector_score" color="#14b8a6" label-width="36px" />
            </template>
          </el-table-column>
          <el-table-column label="综合" width="80" align="center">
            <template #default="{ row }"><ScoreRing :value="row.score" :size="42" :stroke="4" /></template>
          </el-table-column>
          <el-table-column label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button link type="danger" @click="entry = { visible: true, row }">开仓</el-button>
              <el-button link type="primary" @click="addWatch(row)">加自选</el-button>
            </template>
          </el-table-column>
        </el-table>
      </SectionCard>
    </div>

    <EntryDialog
      v-if="entry.row"
      v-model="entry.visible"
      :code="entry.row.code"
      :name="entry.row.name"
      :signal-type="entry.row.signal_type"
      :signal-date="entry.row.signal_date"
    />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: var(--gap);
}
.pick-card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: box-shadow 0.15s, border-color 0.15s;
}
.pick-card:hover {
  box-shadow: var(--shadow);
  border-color: var(--el-color-primary-light-7);
}
.head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.rank {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--c-text-2);
  flex-shrink: 0;
}
.name {
  font-weight: 650;
  font-size: 15px;
  color: var(--c-text);
}
.price-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.price {
  font-size: 18px;
  margin-right: 10px;
}
.scores {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.reason-first {
  font-size: 12px;
  color: var(--c-text-2);
  line-height: 1.6;
  background: var(--c-surface-2);
  border-radius: 6px;
  padding: 6px 8px;
}
.actions {
  display: flex;
  align-items: center;
  gap: 4px;
  border-top: 1px solid #f0f2f6;
  padding-top: 8px;
}
.risk-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.risk-chip {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  color: #b45309;
  background: var(--c-warn-soft);
}
.expand {
  padding: 4px 48px 8px;
}
</style>
