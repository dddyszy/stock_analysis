<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api'
import { num } from '@/utils/format'
import ChanChart from '@/components/ChanChart.vue'
import { SectionCard } from '@/components/ui'

const props = defineProps<{ code: string; dates: string[]; todayBis: any[]; autoOpen?: boolean }>()

const open = ref(false)
const idx = ref(0)
const overlay = ref(true)
const replay = ref<any>(null)
const loading = ref(false)
const log = ref<any>(null)
const logLoading = ref(false)
const statusFilter = ref('')

const date = computed(() => props.dates[idx.value])
const STATUS_TYPES: Record<string, 'success' | 'danger' | 'warning' | 'info'> = {
  kept: 'success', rewritten: 'warning', rewritten_after_confirm: 'danger', aged: 'info',
}
const logItems = computed(() => (log.value?.items || []).filter((x: any) => !statusFilter.value || x.status === statusFilter.value))

let seq = 0
async function loadReplay() {
  if (!open.value || !date.value) return
  const my = ++seq
  loading.value = true
  try {
    const res = await api.stockReplay(props.code, date.value)
    if (my === seq) replay.value = res
  } finally {
    if (my === seq) loading.value = false
  }
}

async function loadLog() {
  logLoading.value = true
  try {
    log.value = await api.stockRewriteLog(props.code)
  } finally {
    logLoading.value = false
  }
}

function toggle() {
  open.value = !open.value
  if (open.value) {
    if (!idx.value) idx.value = Math.max(0, props.dates.length - 61)
    loadReplay()
    if (!log.value) loadLog()
  }
}
function step(n: number) {
  idx.value = Math.min(props.dates.length - 1, Math.max(0, idx.value + n))
}
function jump(d: string) {
  const i = props.dates.findIndex((x) => x >= d)
  if (i >= 0) idx.value = i
}

onMounted(() => {
  if (props.autoOpen) toggle()
})

let timer: number | undefined
watch(idx, () => {
  window.clearTimeout(timer)
  timer = window.setTimeout(loadReplay, 250)
})
watch(() => props.code, () => {
  replay.value = null
  log.value = null
  if (open.value) {
    loadReplay()
    loadLog()
  }
})
</script>

<template>
  <SectionCard title="结构回放" subtitle="拖到过去某一天，看「当时能看到的」笔、中枢和买卖点；灰色虚线是今天回看的笔" class="mt">
    <template #extra>
      <el-button size="small" @click="toggle">{{ open ? '收起' : '展开回放' }}</el-button>
    </template>
    <template v-if="open">
      <div class="bar">
        <el-button size="small" @click="step(-5)">-5 天</el-button>
        <el-button size="small" @click="step(-1)">-1 天</el-button>
        <el-slider v-model="idx" :min="0" :max="Math.max(0, dates.length - 1)" :show-tooltip="false" class="grow" />
        <el-button size="small" @click="step(1)">+1 天</el-button>
        <el-button size="small" @click="step(5)">+5 天</el-button>
        <b class="num">{{ date }}</b>
        <el-switch v-model="overlay" active-text="叠加今天回看的笔" />
      </div>
      <div v-if="replay" class="muted small mt-8">
        那一天的日线结构：<b>{{ replay.state?.name }}</b>
        <template v-if="replay.state?.signal"> · {{ replay.state.signal.name }}（{{ replay.state.signal.date }}，{{ replay.state.signal.confirmed ? '已确认' : '未确认' }}）</template>
        · 上方 {{ num(replay.state?.upper) }} / 下方 {{ num(replay.state?.lower) }}
      </div>
      <div v-loading="loading">
        <ChanChart v-if="replay" :data="replay" :overlay-bis="overlay ? todayBis : []" height="440px" :zoom-bars="120" />
      </div>

      <div class="log-head mt">
        <span class="bold">改写记录</span>
        <span v-if="log" class="muted small">
          {{ log.from }} 至 {{ log.to }} 逐日回放（每天只用当时最近 {{ log.window }} 根日线）：
          {{ log.summary.total }} 个买卖点，保留至今 {{ log.summary.kept }} 个，被改写 {{ log.summary.rewritten }} 个（{{ log.summary.rewritten_ratio == null ? '--' : (log.summary.rewritten_ratio * 100).toFixed(0) + '%' }}）；
          已确认的 {{ log.summary.confirmed }} 个中，后来被改写 {{ log.summary.rewritten_after_confirm }} 个；平均在信号日之后 {{ log.summary.avg_lag_bars }} 根 K 线才出现
        </span>
        <el-radio-group v-model="statusFilter" size="small" class="grow-right">
          <el-radio-button value="">全部</el-radio-button>
          <el-radio-button value="kept">保留至今</el-radio-button>
          <el-radio-button value="rewritten">被改写</el-radio-button>
          <el-radio-button value="rewritten_after_confirm">确认后被改写</el-radio-button>
        </el-radio-group>
      </div>
      <el-table v-loading="logLoading" :data="logItems" size="small" max-height="360" class="mt-8">
        <el-table-column prop="name" label="信号" width="70" />
        <el-table-column prop="signal_date" label="信号所在 K 线" width="110" />
        <el-table-column label="价格" width="80"><template #default="{ row }"><span class="num">{{ num(row.price) }}</span></template></el-table-column>
        <el-table-column prop="first_seen" label="首次出现" width="110" />
        <el-table-column label="首次确认" width="110"><template #default="{ row }">{{ row.confirmed_at || '--' }}</template></el-table-column>
        <el-table-column prop="last_seen" label="最后出现" width="110" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }"><el-tag size="small" effect="plain" :type="STATUS_TYPES[row.status]">{{ row.status_name }}</el-tag></template>
        </el-table-column>
        <el-table-column label="回放" width="80">
          <template #default="{ row }"><el-button link type="primary" @click="jump(row.first_seen)">看当时</el-button></template>
        </el-table-column>
      </el-table>
    </template>
  </SectionCard>
</template>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  gap: 10px;
}
.log-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.grow-right {
  margin-left: auto;
}
</style>
