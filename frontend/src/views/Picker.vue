<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useJobPoller } from '@/composables/useJobPoller'
import { colorClass, num } from '@/utils/format'
import { EmptyState, PageHeader, SectionCard, SignalBadge } from '@/components/ui'

const router = useRouter()
const data = ref<any>(null)
const rates = ref<any>(null)
const loading = ref(false)
const BUY_STATES = ['B1_pending', 'B1_confirmed', 'B2_pending', 'B2_confirmed', 'B3_pending', 'B3_confirmed']
const selected = ref<string[]>([...BUY_STATES])
const industry = ref('')
const excludeRisk = ref(false)

const poller = useJobPoller((job) => {
  if (job.job_name === 'recommend' || job.job_name === 'post_close_pipeline') {
    ElMessage[job.status === 'success' ? 'success' : 'error'](job.message || '扫描结束')
    load()
  }
})

const names = computed<Record<string, string>>(() => data.value?.state_names || {})
const counts = computed<Record<string, number>>(() => data.value?.counts || {})
const industries = computed(() => Array.from(new Set<string>((data.value?.items || []).map((i: any) => i.industry).filter(Boolean))).sort())
const rows = computed(() =>
  (data.value?.items || []).map((r: any) => ({
    ...r,
    up_dist: r.price && r.upper ? (r.upper / r.price - 1) * 100 : null,
    low_dist: r.price && r.lower ? (r.lower / r.price - 1) * 100 : null,
  })),
)
const OUTCOME_COLS: Record<string, [string, string]> = { up: ['continue', 'exception'], down: ['continue', 'exception'] }
const rateRows = computed(() => {
  const all = (rates.value?.rows || []).filter((r: any) => r.regime === 'all' && r.horizon === 10)
  return selected.value
    .map((k) => all.find((r: any) => r.state === k))
    .filter(Boolean)
    .map((r: any) => {
      const inside = r.state === 'inside'
      const [a, b] = inside ? ['break_up', 'break_down'] : OUTCOME_COLS.up
      return { ...r, name: names.value[r.state] || r.state, first: r.outcomes[a], second: r.outcomes[b], inside }
    })
})

function toggleState(k: string) {
  selected.value = selected.value.includes(k) ? selected.value.filter((x) => x !== k) : [...selected.value, k]
}
function pctOf(v: number | null | undefined) {
  return v == null ? '--' : `${Math.round(v * 100)}%`
}
function dist(v: number | null) {
  return v == null ? '--' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}

async function load() {
  loading.value = true
  try {
    data.value = await api.recommendStructures({ states: selected.value.join(','), industry: industry.value || undefined, exclude_risk: excludeRisk.value })
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
  await api.addWatch(row.code, row.state_name)
  ElMessage.success(`已把 ${row.name} 加入自选`)
}

watch([selected, industry, excludeRisk], load)
onMounted(() => {
  load()
  api.baseRates().then((r) => (rates.value = r)).catch(() => undefined)
  poller.tick().then(() => {
    if (Object.keys(poller.running.value).length) poller.start()
  })
})
</script>

<template>
  <div class="page">
    <PageHeader title="结构筛选" :subtitle="data?.date ? `数据日期 ${data.date} · 基本面与风险初筛通过的股票，按日线结构状态分类` : ''">
      <el-button type="primary" :icon="'Search'" :loading="!!poller.running.value.recommend" @click="trigger">
        {{ poller.running.value.recommend ? `扫描中 ${poller.running.value.recommend.progress}/${poller.running.value.recommend.total}` : '重新扫描全市场' }}
      </el-button>
    </PageHeader>

    <el-alert
      type="info"
      show-icon
      :closable="false"
      class="position-note"
      title="按结构状态筛选，不打分、不排名。点股票名称进入个股页，查看延续 / 例外两种情形、认错位和同类结构的历史比例。"
    />

    <SectionCard padding="12px 16px">
      <div class="chips">
        <span
          v-for="(n, k) in counts"
          :key="k"
          class="chip"
          :class="{ on: selected.includes(String(k)) }"
          @click="toggleState(String(k))"
        >
          {{ names[String(k)] || k }} <b class="num">{{ n }}</b>
        </span>
      </div>
      <div class="toolbar">
        <el-button size="small" @click="selected = [...BUY_STATES]">只看买点结构</el-button>
        <el-button size="small" @click="selected = Object.keys(counts)">全部状态</el-button>
        <el-select v-model="industry" clearable placeholder="全部行业" size="small" style="width: 150px">
          <el-option v-for="i in industries" :key="i" :value="i" :label="i" />
        </el-select>
        <el-switch v-model="excludeRisk" size="small" active-text="排除带风险标签的股票" />
        <span class="muted small grow">共 {{ data?.total ?? 0 }} 只{{ (data?.total || 0) > (data?.items?.length || 0) ? `，显示前 ${data.items.length} 只` : '' }}</span>
      </div>
    </SectionCard>

    <SectionCard v-if="rateRows.length" title="同类结构的历史比例" subtitle="逐日回放、只用当时可见的数据统计；10 个交易日内先走出哪种情形。样本是当前在市股票，存在幸存者偏差，比例偏乐观" class="mt" flush>
      <el-table :data="rateRows" size="small">
        <el-table-column prop="name" label="结构状态" min-width="150" />
        <el-table-column prop="n" label="样本" width="80" />
        <el-table-column label="延续 / 向上突破" width="130"><template #default="{ row }"><span class="num up">{{ pctOf(row.first) }}</span></template></el-table-column>
        <el-table-column label="例外 / 向下跌破" width="130"><template #default="{ row }"><span class="num down">{{ pctOf(row.second) }}</span></template></el-table-column>
        <el-table-column label="未分出" width="90"><template #default="{ row }"><span class="num">{{ pctOf(row.outcomes.undecided) }}</span></template></el-table-column>
        <el-table-column label="10 日平均收益" width="120"><template #default="{ row }"><span class="num" :class="colorClass(row.mean_ret)">{{ num(row.mean_ret, 2) }}%</span></template></el-table-column>
        <el-table-column label="相对中证 1000" width="120"><template #default="{ row }"><span class="num" :class="colorClass(row.mean_excess)">{{ num(row.mean_excess, 2) }}%</span></template></el-table-column>
      </el-table>
    </SectionCard>

    <SectionCard v-loading="loading" class="mt" flush>
      <el-table v-if="rows.length" :data="rows" size="small">
        <el-table-column label="股票" min-width="150">
          <template #default="{ row }">
            <router-link :to="`/stock/${row.code}`" class="bold">{{ row.name }}</router-link>
            <div class="muted small num">{{ row.code }} · {{ row.industry || '--' }}</div>
          </template>
        </el-table-column>
        <el-table-column label="日线结构" min-width="150"><template #default="{ row }"><el-tag size="small" effect="plain">{{ row.state_name }}</el-tag></template></el-table-column>
        <el-table-column label="周线结构" min-width="140"><template #default="{ row }"><span class="muted small">{{ row.weekly_state_name || '--' }}</span></template></el-table-column>
        <el-table-column label="信号" width="120">
          <template #default="{ row }">
            <SignalBadge v-if="row.signal" :type="row.signal.type" :date="row.signal.date" :confirmed="row.signal.confirmed" size="sm" />
            <span v-else class="muted">--</span>
          </template>
        </el-table-column>
        <el-table-column label="现价" width="80"><template #default="{ row }"><span class="num">{{ num(row.price) }}</span></template></el-table-column>
        <el-table-column label="上方价位" width="140" sortable :sort-by="(r: any) => r.up_dist ?? 999">
          <template #default="{ row }"><span class="num">{{ num(row.upper) }}</span> <span class="muted small num">{{ dist(row.up_dist) }}</span></template>
        </el-table-column>
        <el-table-column label="下方价位" width="140" sortable :sort-by="(r: any) => r.low_dist ?? -999">
          <template #default="{ row }"><span class="num">{{ num(row.lower) }}</span> <span class="muted small num">{{ dist(row.low_dist) }}</span></template>
        </el-table-column>
        <el-table-column label="风险标签" min-width="140">
          <template #default="{ row }">
            <span v-for="r in row.risk_labels" :key="r" class="risk-chip">{{ r }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/stock/${row.code}`)">结构解读</el-button>
            <el-button link @click="addWatch(row)">加自选</el-button>
          </template>
        </el-table-column>
      </el-table>
      <EmptyState v-else-if="!loading" title="没有符合条件的股票" description="换个结构状态试试；每个交易日收盘后会自动更新，也可以点右上角重新扫描。" />
    </SectionCard>
  </div>
</template>

<style scoped>
.position-note {
  margin-bottom: var(--gap);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.chip {
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--c-border);
  font-size: 12px;
  cursor: pointer;
  color: var(--c-text-2);
  user-select: none;
}
.chip b {
  margin-left: 4px;
}
.chip.on {
  border-color: var(--c-primary);
  color: var(--c-primary);
  background: var(--c-primary-soft);
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.risk-chip {
  display: inline-block;
  margin: 1px 4px 1px 0;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  color: #b45309;
  background: var(--c-warn-soft);
}
</style>
