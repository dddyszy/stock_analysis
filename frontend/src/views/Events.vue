<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/api'
import { useJobPoller } from '@/composables/useJobPoller'
import { num } from '@/utils/format'
import { EmptyState, PageHeader, SectionCard } from '@/components/ui'

const data = ref<any>(null)
const loading = ref(false)
const days = ref(30)
const onlyImportant = ref(false)
const keyword = ref('')

const poller = useJobPoller((job) => {
  if (job.job_name === 'watch_events') {
    ElMessage[job.status === 'success' ? 'success' : 'error'](job.message?.slice(0, 80) || '检测完成')
    load()
  }
})

const items = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  return (data.value?.items || []).filter((x: any) => !k || x.code.includes(k) || (x.name || '').toLowerCase().includes(k))
})
const byDate = computed(() => {
  const groups: Record<string, any[]> = {}
  for (const x of items.value) (groups[x.trade_date] ||= []).push(x)
  return Object.entries(groups)
})

let seq = 0
async function load() {
  const my = ++seq
  loading.value = true
  try {
    const res = await api.structureEvents({ days: days.value, level: onlyImportant.value ? 'important' : undefined })
    if (my === seq) data.value = res
  } finally {
    if (my === seq) loading.value = false
  }
}

async function detect() {
  await api.startJob('watch_events')
  ElMessage.success('已开始检测自选股结构异动')
  poller.start('watch_events')
}

watch([days, onlyImportant], load)
onMounted(load)
</script>

<template>
  <div class="page">
    <PageHeader title="结构异动" subtitle="你的自选股（本地自选 + 腾讯 App 里你自己加的）和上一个交易日相比的结构变化；只描述变化，不预测涨跌">
      <el-radio-group v-model="days" size="small">
        <el-radio-button :value="7">近 7 天</el-radio-button>
        <el-radio-button :value="30">近 30 天</el-radio-button>
        <el-radio-button :value="90">近 90 天</el-radio-button>
      </el-radio-group>
      <el-button type="primary" :icon="'Refresh'" :loading="!!poller.running.value.watch_events" @click="detect">立即检测</el-button>
    </PageHeader>

    <SectionCard padding="12px 16px">
      <div class="toolbar">
        <el-switch v-model="onlyImportant" active-text="只看重要异动（跌破下方价位、买点失效）" />
        <el-input v-model="keyword" placeholder="按代码或名称筛选" clearable size="small" style="width: 200px" />
        <span class="muted small grow">共 {{ items.length }} 条；每个交易日收盘链会自动检测，重要异动同时发系统通知</span>
      </div>
    </SectionCard>

    <div v-loading="loading" class="mt">
      <SectionCard v-for="[d, rows] in byDate" :key="d" :title="d" :subtitle="`${rows.length} 条`" class="mt" flush>
        <el-table :data="rows" size="small">
          <el-table-column label="股票" min-width="130">
            <template #default="{ row }">
              <router-link :to="`/stock/${row.code}`" class="bold">{{ row.name }}</router-link>
              <div class="muted small num">{{ row.code }}</div>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="120">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :type="row.level === 'important' ? 'danger' : 'info'">{{ row.event_name }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="title" label="异动" min-width="280" />
          <el-table-column label="前一交易日 → 当天" min-width="240">
            <template #default="{ row }">
              <span class="small">{{ row.detail?.before?.state_name }} → {{ row.detail?.after?.state_name }}</span>
            </template>
          </el-table-column>
          <el-table-column label="收盘价" width="90"><template #default="{ row }"><span class="num">{{ num(row.detail?.after?.price) }}</span></template></el-table-column>
          <el-table-column label="上方 / 下方价位" width="140">
            <template #default="{ row }"><span class="num small">{{ num(row.detail?.after?.upper) }} / {{ num(row.detail?.after?.lower) }}</span></template>
          </el-table-column>
        </el-table>
      </SectionCard>
      <SectionCard v-if="!loading && !items.length">
        <EmptyState title="这段时间没有结构异动" :description="`已跟踪的 App 自选 ${data?.watch_cached ?? 0} 只；可以在个股页点「加自选」加入本地自选。`" />
      </SectionCard>
    </div>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
</style>
