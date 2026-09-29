<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useJobPoller } from '@/composables/useJobPoller'
import { num } from '@/utils/format'
import { EmptyState, PageHeader, SectionCard, StatCard } from '@/components/ui'
import DianjinBacktest from '@/components/DianjinBacktest.vue'

const list = ref<any[]>([])
const backtest = ref<any>(null)
const active = ref('dianjin_v2')
const data = ref<any>(null)
const loading = ref(false)
const zone = ref('')
const passedOnly = ref(true)

const ZONE_TAGS: Record<string, 'danger' | 'warning' | 'info' | 'success' | 'primary'> = {
  buy: 'danger', near_buy: 'warning', middle: 'info', sell: 'success', insufficient: 'info',
}
const ZONE_ORDER = ['buy', 'near_buy', 'middle', 'sell']

const current = computed(() => list.value.find((s) => s.key === active.value))
const failRows = computed(() => Object.entries(data.value?.fail_counts || {}).sort((a: any, b: any) => b[1] - a[1]))

const poller = useJobPoller((job) => {
  if (job.job_name === 'strategy_scan' || job.job_name === 'value_daily') {
    ElMessage[job.status === 'success' ? 'success' : 'error'](job.message?.slice(0, 80) || '扫描结束')
    load()
  }
})

let seq = 0
async function load() {
  const my = ++seq
  loading.value = true
  try {
    const res = await api.strategyScreen(active.value, { zone: zone.value || undefined, passed_only: passedOnly.value })
    if (my === seq) data.value = res
  } finally {
    if (my === seq) loading.value = false
  }
}

async function scan() {
  await api.startJob('strategy_scan')
  ElMessage.success('已开始策略扫描')
  poller.start('strategy_scan')
}

function pct(v: number | null | undefined, digits = 1) {
  return v == null ? '--' : `${v.toFixed(digits)}%`
}
function ratioPct(v: number | null | undefined) {
  return v == null ? '--' : `${(v * 100).toFixed(0)}%`
}
function dist(a: number | null | undefined, b: number | null | undefined) {
  if (a == null || b == null || !b) return '--'
  const v = (a / b - 1) * 100
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

watch([active, zone, passedOnly], load)
onMounted(async () => {
  list.value = await api.strategies()
  load()
  api.strategyBacktest().then((r) => (backtest.value = r)).catch(() => undefined)
})
</script>

<template>
  <div class="page">
    <PageHeader title="策略" :subtitle="data?.date ? `数据日期 ${data.date} · 价值池 ${data.total} 只，通过筛选 ${data.passed} 只` : '多种策略各自的筛选条件、所处区域和检验结论'">
      <el-radio-group v-model="active">
        <el-radio-button v-for="s in list" :key="s.key" :value="s.key">{{ s.name }}</el-radio-button>
      </el-radio-group>
      <el-button type="primary" :icon="'Refresh'" :loading="!!poller.running.value.strategy_scan" @click="scan">重新扫描</el-button>
    </PageHeader>

    <SectionCard v-if="current" :title="current.name" subtitle="规则说明" class="mt">
      <div class="rules">
        <ol>
          <li v-for="r in current.rules" :key="r">{{ r }}</li>
        </ol>
        <div class="verdict">
          <div class="bold">回测检验</div>
          <div class="muted">{{ current.backtest }}</div>
          <div class="muted small mt-8">{{ current.caveat }}</div>
        </div>
      </div>
    </SectionCard>

    <DianjinBacktest v-if="backtest && current?.backtest_key" :result="backtest" :vkey="current.backtest_key" />

    <div v-if="data?.date" class="grid grid-4 mt">
      <StatCard v-for="z in ZONE_ORDER" :key="z" :label="data.zone_names[z]" :value="data.counts[z] || 0"
        :sub="z === 'buy' ? '低于 MA120 的 88%' : z === 'near_buy' ? '距买入线 4% 以内' : z === 'sell' ? '高于 MA120 的 112%' : '两条线之间'" />
    </div>

    <SectionCard class="mt" padding="12px 16px">
      <div class="toolbar">
        <el-radio-group v-model="zone" size="small">
          <el-radio-button value="">全部区域</el-radio-button>
          <el-radio-button v-for="z in ZONE_ORDER" :key="z" :value="z">{{ data?.zone_names?.[z] || z }}</el-radio-button>
        </el-radio-group>
        <el-switch v-model="passedOnly" active-text="只看通过全部筛选条件的" />
        <span v-if="failRows.length" class="muted small grow">
          未通过的原因：<span v-for="[k, v] in failRows" :key="k" class="fail">{{ k }} {{ v }} 只</span>
        </span>
      </div>
    </SectionCard>

    <SectionCard v-loading="loading" class="mt" flush>
      <el-table v-if="data?.items?.length" :data="data.items" size="small">
        <el-table-column label="股票" min-width="140">
          <template #default="{ row }">
            <router-link :to="`/stock/${row.code}`" class="bold">{{ row.name }}</router-link>
            <div class="muted small num">{{ row.code }} · {{ row.industry || '--' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="区域" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="row.passed ? ZONE_TAGS[row.zone] : 'info'">{{ row.passed ? row.zone_name : '未通过筛选' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="现价" width="80"><template #default="{ row }"><span class="num">{{ num(row.price) }}</span></template></el-table-column>
        <el-table-column label="MA120" width="80"><template #default="{ row }"><span class="num">{{ num(row.ma120) }}</span></template></el-table-column>
        <el-table-column label="现价 / MA120" width="100"><template #default="{ row }"><span class="num bold">{{ ratioPct(row.ratio) }}</span></template></el-table-column>
        <el-table-column label="买入线" width="110">
          <template #default="{ row }"><span class="num">{{ num(row.buy_line) }}</span> <span class="muted small">{{ dist(row.buy_line, row.price) }}</span></template>
        </el-table-column>
        <el-table-column label="卖出线" width="110">
          <template #default="{ row }"><span class="num">{{ num(row.sell_line) }}</span> <span class="muted small">{{ dist(row.sell_line, row.price) }}</span></template>
        </el-table-column>
        <el-table-column label="PE" width="64"><template #default="{ row }"><span class="num">{{ row.pe == null ? '--' : row.pe <= 0 ? '亏损' : num(row.pe, 1) }}</span></template></el-table-column>
        <el-table-column label="股息率" width="76"><template #default="{ row }"><span class="num">{{ pct(row.dy, 2) }}</span></template></el-table-column>
        <el-table-column label="市值" width="84"><template #default="{ row }"><span class="num">{{ row.mv ? `${(row.mv / 1e8).toFixed(0)} 亿` : '--' }}</span></template></el-table-column>
        <el-table-column label="近三年 ROE" min-width="150">
          <template #default="{ row }"><span class="num small">{{ row.roe3 ? row.roe3.map((r: number) => `${r.toFixed(1)}%`).join('、') : '--' }}</span></template>
        </el-table-column>
      </el-table>
      <EmptyState v-else-if="!loading" title="还没有策略扫描结果" description="价值数据补齐完成后，每个交易日晚上会自动扫描；也可以点右上角重新扫描。" />
    </SectionCard>
  </div>
</template>

<style scoped>
.rules {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 20px;
}
.rules ol {
  margin: 0;
  padding-left: 18px;
  line-height: 1.9;
  color: var(--c-text-2);
}
.verdict {
  background: var(--c-surface-2);
  border-radius: var(--radius-sm);
  padding: 12px 14px;
  line-height: 1.7;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.fail {
  margin-right: 10px;
}
</style>
