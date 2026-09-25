<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/api'
import { useJobPoller } from '@/composables/useJobPoller'
import { dt } from '@/utils/format'
import { EmptyState, PageHeader, SectionCard, StatCard } from '@/components/ui'

const emit = defineEmits<{ (e: 'auth-changed'): void }>()
const route = useRoute()
const router = useRouter()
const auth = ref<any>(null)
const overview = ref<any>(null)
const appSync = ref<any>(null)
const strategy = ref<any>(null)
const manualToken = ref('')
const showManual = ref(false)
const testing = ref(false)
const editing = ref<any>(null)
const newName = ref('')
const active = ref('auth')

const poller = useJobPoller(() => {
  loadOverview()
  loadNotices()
})

const SECTIONS = [
  { id: 'auth', title: '账户授权', icon: 'Key' },
  { id: 'sync', title: '数据同步', icon: 'Refresh' },
  { id: 'jobs', title: '任务记录', icon: 'List' },
  { id: 'schedule', title: '定时任务', icon: 'AlarmClock' },
  { id: 'mcp', title: 'MCP 与限流', icon: 'Connection' },
  { id: 'notify', title: '系统通知', icon: 'Bell' },
  { id: 'app', title: '写回 App', icon: 'Iphone' },
  { id: 'strategy', title: '策略参数', icon: 'Operation' },
]

// 策略参数：分组、中文名、说明、控件类型
type Param = { key: string; label: string; desc: string; kind?: 'ratio' | 'int' | 'num' | 'select' | 'dict' | 'list' | 'bool' | 'units'; step?: number; max?: number }
const GROUPS: { title: string; items: Param[] }[] = [
  {
    title: '开仓风控',
    items: [
      { key: 'default_equity', label: '默认账户资产', desc: '拿不到模拟账户资产时用于计算仓位（元）', kind: 'int', step: 10000 },
      { key: 'risk_per_trade', label: '单笔风险比例', desc: '每笔交易最多亏总资产的比例，股数 = 总资产 × 该比例 ÷ 1R', kind: 'ratio', max: 0.05 },
      { key: 'max_single_position', label: '单票仓位上限', desc: '单只股票市值占总资产的上限', kind: 'ratio', max: 0.5 },
      { key: 'min_reward_risk', label: '最低盈亏比', desc: '目标一距离 ÷ 1R 低于该值不开仓', kind: 'num', step: 0.1 },
      { key: 'max_stop_distance', label: '最大止损距离', desc: '止损距离超过该比例时按下一项处理', kind: 'ratio', max: 0.3 },
      { key: 'wide_stop_action', label: '止损过远时', desc: '仓位减半，或直接不开仓', kind: 'select' },
    ],
  },
  {
    title: 'A 股流动性与风险',
    items: [
      { key: 'min_avg_amount', label: '最低日均成交额（元）', desc: '近 20 日平均成交额低于该值的股票不推荐，避免流动性差、易被操纵的小票', kind: 'int', step: 10000000 },
      { key: 'min_float_mv', label: '最低流通市值（元）', desc: '流通市值低于该值的股票不推荐', kind: 'int', step: 100000000 },
      { key: 'risk_label_penalty', label: '风险标签扣分', desc: '每个扣分类风险（解禁、减持、预亏、经营现金流为负等）扣掉的基本面分', kind: 'int', step: 1 },
    ],
  },
  {
    title: '止损',
    items: [
      { key: 'hard_stop_pct', label: '硬止损', desc: '浮亏达到该比例盘中无条件清仓', kind: 'ratio', max: 0.2 },
      { key: 'stop_buffer_atr', label: '止损缓冲（ATR 倍数）', desc: '结构止损位再减去 N 倍 ATR，避免被插针打掉', kind: 'num', step: 0.1 },
      { key: 'stop_buffer_pct', label: '止损缓冲（无 ATR 时）', desc: 'K 线不足以计算 ATR 时使用的比例', kind: 'ratio', max: 0.05 },
      { key: 'time_stop_bars', label: '时间止损 K 线数', desc: '买入后 N 根日线仍未走出确认的向上笔', kind: 'int', step: 1 },
      { key: 'time_stop_reduce', label: '时间止损减仓比例', desc: '触发时间止损时减掉的仓位比例', kind: 'ratio', max: 1 },
      { key: 'b2_stop_mode', label: '二买止损口径', desc: '宽松用一买低点，严格用二买自身低点', kind: 'select' },
    ],
  },
  {
    title: '止盈与分批',
    items: [
      { key: 'batch_ratios', label: '分批止盈比例', desc: '第 1、2 批各卖出原始仓位的比例，第 3 批卖出剩余', kind: 'list' },
      { key: 'keep_base_ratio_week_uptrend', label: '周线上涨时保留底仓', desc: '日线一卖但周线仍在上涨结构时，至少保留的仓位比例', kind: 'ratio', max: 1 },
    ],
  },
  {
    title: '市场温度与仓位',
    items: [
      { key: 'regime_caps', label: '总仓位上限', desc: '强势 / 震荡 / 弱势对应的总仓位上限', kind: 'dict' },
      { key: 'regime_multiplier', label: '推荐分温度系数', desc: '不同市场温度下综合分乘的系数', kind: 'dict' },
    ],
  },
  {
    title: '打分与推荐',
    items: [
      { key: 'weights', label: '综合分权重', desc: '缠论分、基本面分、行业强度分的权重', kind: 'dict' },
      { key: 'signal_weights', label: '信号类型权重', desc: '一买、二买、三买在缠论分中的权重，可用回测结果调整', kind: 'dict' },
      { key: 'signal_recent_bars', label: '信号有效 K 线数', desc: '距今多少根日线以内的买点才参与推荐', kind: 'int', step: 1 },
      { key: 'recommend_top_n', label: '推荐数量', desc: '每次推荐保留的股票数', kind: 'int', step: 5 },
      { key: 'min_fund_score', label: '最低基本面分', desc: '基本面初筛的分数门槛', kind: 'int', step: 5 },
      { key: 'finance_time_budget', label: '财报补拉时限（秒）', desc: '每次推荐为候选股补拉财报的最长时间（受 MCP 限频）', kind: 'int', step: 60 },
      { key: 'recommend_confirmed_only', label: '主推荐只收已确认信号', desc: '开启后，信号所在的笔或线段尚未确认的放入观察池，不进主推荐、不写回 App', kind: 'bool' },
      { key: 'scope_weight', label: '线段级别信号加权', desc: '线段级别买点是真正的日线级别买点，相对笔级别给更高权重', kind: 'num', step: 0.05 },
      { key: 'resonance_weight', label: '周线共振权重', desc: '周线共振在缠论分中的权重。1000 只股票回测显示共振向上时入场反而更差，默认 0（只展示不加分）', kind: 'num', step: 0.05 },
      { key: 'regime_block', label: '按大盘状态降级的信号', desc: '勾选的"信号 × 大盘状态"组合只进观察池。大盘状态按中证 1000 相对 60 日均线划分，与回测口径一致', kind: 'units' },
      { key: 'watch_top_n', label: '观察池数量', desc: '每次推荐保留的观察池股票数', kind: 'int', step: 5 },
    ],
  },
  {
    title: '回测',
    items: [
      { key: 'slippage', label: '单边滑点', desc: '回测买入和卖出各计一次的价格滑点', kind: 'ratio', max: 0.01 },
      { key: 'backtest_split_ratio', label: '样本内比例', desc: '未指定切分日期时，回看区间前面这部分作为样本内，其余为样本外', kind: 'ratio', max: 1 },
    ],
  },
  {
    title: '下单与费用',
    items: [
      { key: 'limit_price_offset', label: '限价偏移', desc: '默认限价相对现价上浮（买）或下浮（卖）的比例', kind: 'ratio', max: 0.03 },
      { key: 'fees', label: '费用', desc: '佣金率、最低佣金、印花税（回测和预估费用使用）', kind: 'dict' },
    ],
  },
]
const UNIT_OPTIONS = ['B1', 'B2', 'B3'].flatMap((sig) =>
  [['up', '上涨'], ['range', '震荡'], ['down', '下跌']].map(([reg, name]) => ({
    value: `${sig}|${reg}`,
    label: `${{ B1: '一买', B2: '二买', B3: '三买' }[sig]} · ${name}`,
  })),
)
const OPTIONS: Record<string, { value: string; label: string }[]> = {
  wide_stop_action: [
    { value: 'half', label: '仓位减半' },
    { value: 'skip', label: '不开仓' },
  ],
  b2_stop_mode: [
    { value: 'loose', label: '宽松：一买低点' },
    { value: 'strict', label: '严格：二买自身低点' },
  ],
}
const DICT_LABELS: Record<string, string> = {
  strong: '强势', neutral: '震荡', weak: '弱势', chan: '缠论', fund: '基本面', sector: '行业',
  B1: '一买', B2: '二买', B3: '三买', commission: '佣金率', min_commission: '最低佣金', stamp_tax: '印花税',
}

const activeConfig = computed(() => strategy.value?.configs?.find((c: any) => c.is_active))
const sync = computed(() => overview.value?.sync || {})
const initStep = computed(() => {
  const s = sync.value
  if (!s.active_stocks) return 0
  if (s.stocks_with_kline < s.active_stocks * 0.95) return 1
  if (!s.latest_kline_date) return 1
  return 3
})
const JOB_GROUPS = [
  { title: '初始化', names: ['full_initialize', 'stock_pool', 'backfill'] },
  { title: '日常', names: ['daily_update', 'risk_labels', 'scores', 'fundamentals', 'recommend', 'tracking', 'evaluate_positions', 'post_close_pipeline'] },
]
const jobLabel = (name: string) => overview.value?.available_jobs?.find((j: any) => j.name === name)?.label || name
const limiters = computed(() => Object.entries(overview.value?.limiters || {}).map(([tool, v]: any) => ({ tool, ...v })))

async function loadAuth() {
  auth.value = await api.authStatus()
}
async function loadOverview() {
  overview.value = await api.overview()
  for (const n of overview.value.running || []) if (!poller.running.value[n]) poller.start(n)
}
const notices = ref<any>({ items: [], unread: 0 })
const LEVEL_TAGS: Record<string, { text: string; type: 'danger' | 'warning' | 'info' | 'primary' }> = {
  error: { text: '错误', type: 'danger' },
  important: { text: '重要', type: 'primary' },
  warning: { text: '警告', type: 'warning' },
  info: { text: '提示', type: 'info' },
}
async function loadNotices() {
  notices.value = await api.notifications(50)
}
async function readAll() {
  await api.notifyReadAll()
  await loadNotices()
  window.dispatchEvent(new Event('notify-read'))
}
function ranToday(name: string) {
  const today = new Date().toISOString().slice(0, 10)
  return (overview.value?.jobs || []).some((j: any) => j.job_name === name && j.status === 'success' && (j.started_at || '').startsWith(today))
}
async function loadAppSync() {
  appSync.value = await api.appSync()
}
async function loadStrategy() {
  strategy.value = await api.strategy()
  editing.value = JSON.parse(JSON.stringify(activeConfig.value?.params || strategy.value.defaults))
}

function scrollTo(id: string) {
  active.value = id
  document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

async function authorize() {
  const { url } = await api.authStart()
  window.location.href = url
}
async function saveToken() {
  if (!manualToken.value.trim()) return
  await api.authToken(manualToken.value.trim())
  manualToken.value = ''
  ElMessage.success('已保存 token')
  await loadAuth()
  emit('auth-changed')
}
async function logout() {
  await ElMessageBox.confirm('清除本地保存的 token？之后需要重新授权。', '确认', { type: 'warning' })
  await api.authLogout()
  await loadAuth()
  emit('auth-changed')
}
async function test() {
  testing.value = true
  try {
    await api.authTest()
    ElMessage.success('连接正常')
  } finally {
    testing.value = false
  }
}
async function probe() {
  await api.authProbe()
  ElMessage.success('已开始探测，结果保存在 backend/tests/fixtures/mcp/')
  poller.start('mcp_probe')
}
async function runJob(name: string) {
  if (name === 'post_close_pipeline' && ranToday(name)) {
    await ElMessageBox.confirm(
      '今天的收盘后流水线已经成功运行过。重跑会再次消耗 MCP 配额（财报接口可能进入冷却），并重新写回 App。确定要重跑吗？',
      '今天已运行过',
      { type: 'warning', confirmButtonText: '仍然重跑' },
    )
  } else {
    await ElMessageBox.confirm(`开始「${jobLabel(name)}」？`, '确认', { type: 'info' })
  }
  await api.startJob(name)
  poller.start(name)
  ElMessage.success('任务已开始')
}
async function cancelJob(name: string) {
  await ElMessageBox.confirm(`取消正在运行的「${jobLabel(name)}」？已完成的部分会保留，下次从断点继续。`, '确认')
  const r = await api.cancelJob(name)
  ElMessage[r.cancelled ? 'success' : 'info'](r.cancelled ? '已取消' : '任务已结束')
  loadOverview()
}
async function toggleAppSync(v: boolean) {
  if (v) await ElMessageBox.confirm('开启后，每个交易日收盘会把推荐股写入你的「缠论推荐」自选分组，并把持仓止损价、目标价写成股价提醒。', '开启写回 App')
  await api.setAppSync(v)
  await loadAppSync()
}
async function runAppSync() {
  await ElMessageBox.confirm('立即把最新推荐和持仓提醒写回腾讯自选股 App？', '确认')
  const r = await api.runAppSync()
  ElMessage.success(`完成：分组新增 ${r.group?.added ?? 0}、移出 ${r.group?.removed ?? 0}，提醒更新 ${r.alerts?.updated ?? 0}`)
  loadAppSync()
}
async function saveStrategy(asNew: boolean) {
  const name = asNew ? newName.value.trim() : activeConfig.value?.name
  if (!name) return ElMessage.warning('请输入新配置名称')
  await api.saveStrategy({ name, params: editing.value, activate: !asNew })
  ElMessage.success(asNew ? `已另存为「${name}」` : '已保存并生效')
  newName.value = ''
  loadStrategy()
}
async function activateConfig(id: number) {
  await api.activateStrategy(id)
  ElMessage.success('已切换')
  loadStrategy()
}
function resetDefaults() {
  editing.value = JSON.parse(JSON.stringify(strategy.value.defaults))
}

onMounted(async () => {
  if (route.query.auth === 'ok') {
    ElMessage.success('腾讯自选股授权成功')
    emit('auth-changed')
    router.replace('/settings')
  } else if (route.query.auth === 'fail') {
    ElMessage.error(`授权失败：${route.query.msg || ''}`)
    router.replace('/settings')
  }
  await Promise.all([loadAuth(), loadOverview(), loadAppSync(), loadStrategy(), loadNotices()])
  if (route.hash) setTimeout(() => scrollTo(route.hash.slice(1)), 100)
})
</script>

<template>
  <div class="page">
    <PageHeader title="设置" subtitle="授权、数据同步、任务与策略参数" />
    <div class="layout">
      <nav class="anchor">
        <button v-for="s in SECTIONS" :key="s.id" :class="{ active: active === s.id }" @click="scrollTo(s.id)">
          <el-icon><component :is="s.icon" /></el-icon>{{ s.title }}
        </button>
      </nav>

      <div class="sections">
        <!-- 授权 -->
        <SectionCard id="sec-auth" title="腾讯自选股授权" subtitle="westock-mcp · OAuth 2.1">
          <template v-if="auth">
            <el-alert v-if="auth.provider === 'mock'" type="warning" :closable="false" show-icon class="mb"
              title="当前为模拟数据模式（DATA_PROVIDER=mock，使用 chan_stock_mock 库）。改为 mcp 并重启后端即可使用真实数据。" />
            <div class="auth-row">
              <div class="auth-status" :class="auth.authorized ? 'ok' : 'bad'">
                <el-icon :size="22"><component :is="auth.authorized ? 'CircleCheckFilled' : 'WarningFilled'" /></el-icon>
                <div>
                  <div class="bold">{{ auth.authorized ? '已连接腾讯自选股' : '尚未授权' }}</div>
                  <div class="muted small">
                    <template v-if="auth.authorized">{{ auth.source === 'oauth' ? 'OAuth 授权' : '手动 token' }} · 有效期至 {{ dt(auth.expires_at) }}{{ auth.has_refresh_token ? '（到期自动续期）' : '' }}</template>
                    <template v-else>授权后才能使用股票池、基本面、模拟盘和写回功能</template>
                  </div>
                </div>
              </div>
              <div class="row wrap">
                <el-button type="primary" @click="authorize">{{ auth.authorized ? '重新授权' : '授权腾讯自选股' }}</el-button>
                <el-button :disabled="!auth.authorized" :loading="testing" @click="test">测试连接</el-button>
                <el-button :disabled="!auth.authorized" @click="probe">探测工具并保存样本</el-button>
                <el-button :disabled="!auth.authorized" link type="danger" @click="logout">清除 token</el-button>
              </div>
            </div>
            <div class="kv3 mt">
              <div><span>MCP 地址</span><b class="mono small">{{ auth.mcp_url }}</b></div>
              <div><span>回调地址</span><b class="mono small">{{ auth.redirect_uri }}</b></div>
            </div>
            <el-button link size="small" class="mt-8" @click="showManual = !showManual">{{ showManual ? '收起' : '回调失败？手动粘贴 token' }}</el-button>
            <div v-if="showManual" class="row mt-8">
              <el-input v-model="manualToken" placeholder="粘贴 access_token" />
              <el-button @click="saveToken">保存</el-button>
            </div>
          </template>
        </SectionCard>

        <!-- 数据同步 -->
        <SectionCard id="sec-sync" title="数据同步" :subtitle="overview ? `数据源 ${overview.provider} · 数据库 ${overview.database}` : ''">
          <template v-if="overview">
            <div class="grid grid-4">
              <StatCard label="股票池" :value="sync.active_stocks" sub="沪深 A 股 + 4 个指数" />
              <StatCard label="已有日线" :value="sync.stocks_with_kline" :sub="`最新 ${sync.latest_kline_date || '--'}`" />
              <StatCard label="有三大报表" :value="sync.stocks_with_fundamental" sub="候选股、持仓、自选" />
              <StatCard label="失败项" :value="sync.failed_items" :value-class="sync.failed_items ? 'up' : ''" sub="下次同步会重试" />
            </div>
            <el-steps :active="initStep" finish-status="success" align-center class="steps">
              <el-step title="股票池" description="申万一级行业 + ST 标签" />
              <el-step title="日线回填" description="近 5 年前复权日线" />
              <el-step title="评分与估值" description="全市场诊股评分、PE/PB" />
              <el-step title="收盘后流水线" description="市场环境、推荐、持仓建议" />
            </el-steps>
            <div v-for="g in JOB_GROUPS" :key="g.title" class="job-group">
              <span class="muted small group-title">{{ g.title }}</span>
              <el-button
                v-for="n in g.names"
                :key="n"
                size="small"
                :type="n === 'full_initialize' || n === 'post_close_pipeline' ? 'primary' : 'default'"
                :plain="n === 'post_close_pipeline'"
                :loading="!!poller.running.value[n]"
                @click="runJob(n)"
              >{{ jobLabel(n) }}</el-button>
            </div>
            <div v-for="(j, name) in poller.running.value" :key="name" class="running">
              <div class="row-between">
                <span class="bold small">{{ j.label || jobLabel(String(name)) }}</span>
                <el-button link type="danger" size="small" @click="cancelJob(String(name))">取消</el-button>
              </div>
              <el-progress :percentage="j.total ? Math.round((j.progress / j.total) * 100) : 0" :stroke-width="8" />
              <div class="muted small">{{ j.message }}</div>
            </div>
          </template>
        </SectionCard>

        <!-- 任务记录 -->
        <SectionCard id="sec-jobs" title="任务记录" subtitle="最近 20 次" flush>
          <el-table :data="overview?.jobs || []" size="small">
            <el-table-column prop="label" label="任务" width="220" />
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag size="small" effect="plain" :type="{ success: 'success', failed: 'danger', running: 'warning', cancelled: 'info', interrupted: 'info' }[row.status as string] || 'info'">
                  {{ { success: '成功', failed: '失败', running: '运行中', cancelled: '已取消', interrupted: '已中断' }[row.status as string] || row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="进度" width="110"><template #default="{ row }"><span class="num">{{ row.progress }}/{{ row.total }}</span></template></el-table-column>
            <el-table-column label="开始" width="140"><template #default="{ row }"><span class="num">{{ dt(row.started_at) }}</span></template></el-table-column>
            <el-table-column label="结束" width="140"><template #default="{ row }"><span class="num">{{ dt(row.finished_at) }}</span></template></el-table-column>
            <el-table-column prop="message" label="信息" min-width="300" show-overflow-tooltip />
          </el-table>
        </SectionCard>

        <!-- 定时任务 -->
        <SectionCard id="sec-schedule" title="定时任务" subtitle="运行前会先判断是否交易日、是否已授权" flush>
          <el-table :data="overview?.scheduled || []" size="small">
            <el-table-column prop="id" label="任务" width="180" />
            <el-table-column label="下次运行" width="180"><template #default="{ row }"><span class="num">{{ dt(row.next_run) }}</span></template></el-table-column>
            <el-table-column prop="trigger" label="触发规则" />
          </el-table>
        </SectionCard>

        <!-- MCP -->
        <SectionCard id="sec-mcp" title="MCP 调用与限流" subtitle="遇到“服务限频”会自动冷却、降速，连续成功后再逐步提速">
          <div class="grid grid-2">
            <div>
              <div class="sub-title">调用统计</div>
              <el-table :data="overview?.mcp_calls_24h || []" size="small" max-height="300">
                <el-table-column label="工具" min-width="150">
                  <template #default="{ row }">
                    {{ row.tool }}
                    <el-tag v-if="row.cooldown_until" size="small" type="warning" effect="light">配额冷却至 {{ row.cooldown_until.slice(11, 16) }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="今日 / 限频" width="96">
                  <template #default="{ row }"><span class="num">{{ row.today }}</span> / <span class="num" :class="row.limited_today ? 'up' : ''">{{ row.limited_today }}</span></template>
                </el-table-column>
                <el-table-column prop="count" label="24 小时" width="76" />
                <el-table-column label="失败" width="70"><template #default="{ row }"><span :class="row.errors ? 'up' : ''">{{ row.errors }}</span></template></el-table-column>
                <el-table-column prop="avg_ms" label="平均 ms" width="80" />
              </el-table>
            </div>
            <div>
              <div class="sub-title">当前限流状态</div>
              <el-table :data="limiters" size="small" max-height="300">
                <el-table-column prop="tool" label="工具 / 域名" />
                <el-table-column prop="rate_per_min" label="次/分钟" width="80" />
                <el-table-column label="冷却" width="70"><template #default="{ row }">{{ row.cooldown_left ? `${row.cooldown_left}s` : '--' }}</template></el-table-column>
                <el-table-column prop="limited_count" label="被限频" width="70" />
              </el-table>
            </div>
          </div>
          <div class="sub-title mt">最近错误</div>
          <el-table :data="overview?.mcp_recent_errors || []" size="small">
            <el-table-column prop="tool" label="工具" width="180" />
            <el-table-column label="时间" width="140"><template #default="{ row }"><span class="num">{{ dt(row.at) }}</span></template></el-table-column>
            <el-table-column prop="error" label="错误" show-overflow-tooltip />
          </el-table>
        </SectionCard>

        <!-- 系统通知 -->
        <SectionCard id="sec-notify" title="系统通知" :subtitle="`未读 ${notices.unread} 条 · 同一事件同一天只记一次`" flush>
          <template #extra>
            <el-button size="small" :disabled="!notices.unread" @click="readAll">全部标为已读</el-button>
          </template>
          <el-table v-if="notices.items.length" :data="notices.items" size="small" max-height="420">
            <el-table-column label="级别" width="76">
              <template #default="{ row }">
                <el-tag size="small" :type="LEVEL_TAGS[row.level]?.type || 'info'" effect="light">{{ LEVEL_TAGS[row.level]?.text || row.level }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="时间" width="140"><template #default="{ row }"><span class="num">{{ dt(row.created_at) }}</span></template></el-table-column>
            <el-table-column label="内容" min-width="360">
              <template #default="{ row }">
                <div :class="{ bold: !row.is_read }">{{ row.title }}</div>
                <div v-if="row.message" class="muted small">{{ row.message }}</div>
              </template>
            </el-table-column>
          </el-table>
          <EmptyState v-else title="暂无通知" description="任务失败、流水线没跑、数据过期、授权失效、盘中触发止损时会在这里提醒" />
        </SectionCard>

        <!-- 写回 App -->
        <SectionCard id="sec-app" title="写回腾讯自选股 App">
          <template v-if="appSync">
            <div class="row-between wrap">
              <div class="row">
                <el-switch :model-value="appSync.enabled" :disabled="appSync.mock_mode" @change="(v: any) => toggleAppSync(!!v)" />
                <span class="bold">{{ appSync.enabled ? '已开启' : '已关闭' }}</span>
                <span class="muted small">推荐分组 {{ appSync.group?.group_id ? `#${appSync.group.group_id}` : '未创建' }} · {{ appSync.group?.codes?.length || 0 }} 只 · 股价提醒 {{ Object.keys(appSync.price_alerts || {}).length }} 只</span>
              </div>
              <el-button size="small" :disabled="!appSync.enabled" @click="runAppSync">立即同步</el-button>
            </div>
            <div class="note">只改动「缠论推荐」分组；落选股票若也在你的其他分组里则不删除。止损价、目标价写成股价提醒时保留你原有的其他提醒，平仓后恢复原值。</div>
            <el-table :data="appSync.logs" size="small" max-height="300" class="mt">
              <el-table-column label="时间" width="140"><template #default="{ row }"><span class="num">{{ dt(row.created_at) }}</span></template></el-table-column>
              <el-table-column prop="action" label="动作" width="140" />
              <el-table-column prop="code" label="股票" width="200" show-overflow-tooltip />
              <el-table-column label="结果" width="70"><template #default="{ row }"><el-tag size="small" effect="plain" :type="row.ok ? 'success' : 'danger'">{{ row.ok ? '成功' : '失败' }}</el-tag></template></el-table-column>
              <el-table-column prop="message" label="信息" show-overflow-tooltip />
            </el-table>
          </template>
        </SectionCard>

        <!-- 策略参数 -->
        <SectionCard id="sec-strategy" title="策略参数" subtitle="止盈止损、仓位与打分规则；保存后下一次评估和推荐生效">
          <template #extra>
            <el-select :model-value="activeConfig?.id" size="small" style="width: 200px" @change="(v: number) => activateConfig(v)">
              <el-option v-for="c in strategy?.configs || []" :key="c.id" :value="c.id" :label="`${c.name}${c.is_active ? '（启用中）' : ''}`" />
            </el-select>
          </template>
          <template v-if="editing">
            <div v-for="g in GROUPS" :key="g.title" class="param-group">
              <div class="group-head">{{ g.title }}</div>
              <div v-for="p in g.items" :key="p.key" class="param">
                <div class="param-label">
                  <div class="bold">{{ p.label }}</div>
                  <div class="muted small">{{ p.desc }}</div>
                </div>
                <div class="param-ctrl">
                  <template v-if="p.kind === 'ratio'">
                    <el-slider v-model="editing[p.key]" :min="0" :max="p.max ?? 1" :step="(p.max ?? 1) / 100" :format-tooltip="(v: number) => `${(v * 100).toFixed(1)}%`" class="grow" />
                    <span class="num ratio-v">{{ (editing[p.key] * 100).toFixed(1) }}%</span>
                  </template>
                  <el-switch v-else-if="p.kind === 'bool'" v-model="editing[p.key]" />
                  <el-checkbox-group v-else-if="p.kind === 'units'" v-model="editing[p.key]" class="units">
                    <el-checkbox v-for="u in UNIT_OPTIONS" :key="u.value" :value="u.value" size="small">{{ u.label }}</el-checkbox>
                  </el-checkbox-group>
                  <el-select v-else-if="p.kind === 'select'" v-model="editing[p.key]" style="width: 220px">
                    <el-option v-for="o in OPTIONS[p.key]" :key="o.value" :value="o.value" :label="o.label" />
                  </el-select>
                  <div v-else-if="p.kind === 'dict'" class="dict">
                    <span v-for="(_v, k) in editing[p.key]" :key="k" class="dict-item">
                      <span class="muted small">{{ DICT_LABELS[k] || k }}</span>
                      <el-input-number v-model="editing[p.key][k]" :step="k === 'min_commission' ? 1 : 0.05" :precision="k === 'min_commission' ? 1 : 4" :min="0" controls-position="right" size="small" style="width: 120px" />
                    </span>
                  </div>
                  <div v-else-if="p.kind === 'list'" class="dict">
                    <span v-for="(_v, i) in editing[p.key]" :key="i" class="dict-item">
                      <span class="muted small">第 {{ Number(i) + 1 }} 批</span>
                      <el-input-number v-model="editing[p.key][i]" :step="0.05" :precision="4" :min="0" :max="1" controls-position="right" size="small" style="width: 120px" />
                    </span>
                  </div>
                  <el-input-number v-else v-model="editing[p.key]" :step="p.step ?? 0.1" :precision="p.kind === 'int' ? 0 : 2" :min="0" controls-position="right" style="width: 180px" />
                </div>
              </div>
            </div>
            <div class="save-bar">
              <el-button link @click="resetDefaults">恢复系统默认值</el-button>
              <span class="grow" />
              <el-input v-model="newName" placeholder="新配置名称" size="default" style="width: 180px" />
              <el-button @click="saveStrategy(true)">另存为新配置</el-button>
              <el-button type="primary" @click="saveStrategy(false)">保存到「{{ activeConfig?.name }}」</el-button>
            </div>
          </template>
        </SectionCard>
      </div>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 168px minmax(0, 1fr);
  gap: var(--gap);
  align-items: start;
}
.anchor {
  position: sticky;
  top: 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: var(--radius);
  padding: 8px;
}
.anchor button {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 10px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--c-text-2);
  cursor: pointer;
  font-size: 13px;
  text-align: left;
}
.anchor button:hover {
  background: var(--c-surface-2);
}
.anchor button.active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 600;
}
.sections {
  display: flex;
  flex-direction: column;
  gap: var(--gap);
}
.sections > * {
  scroll-margin-top: 12px;
}
.mb {
  margin-bottom: 12px;
}
.mt-8 {
  margin-top: 8px;
}
.auth-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.auth-status {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--radius);
}
.auth-status.ok {
  background: var(--c-down-soft);
  color: var(--c-down);
}
.auth-status.bad {
  background: var(--c-up-soft);
  color: var(--c-up);
}
.auth-status .muted {
  color: var(--c-text-2);
}
.kv3 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 24px;
}
.kv3 div {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.kv3 span {
  font-size: 11px;
  color: var(--c-text-3);
}
.steps {
  margin: 20px 0 16px;
}
.job-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.group-title {
  width: 48px;
}
.running {
  margin-top: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--c-surface-2);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.sub-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 6px;
}
.note {
  margin-top: 10px;
  font-size: 12px;
  color: var(--c-text-2);
  background: var(--c-surface-2);
  border-radius: 8px;
  padding: 8px 12px;
  line-height: 1.6;
}
.param-group {
  margin-bottom: 18px;
}
.group-head {
  font-weight: 650;
  font-size: 13px;
  padding: 6px 0;
  border-bottom: 1px solid #f0f2f6;
  margin-bottom: 4px;
}
.param {
  display: grid;
  grid-template-columns: minmax(220px, 360px) minmax(0, 1fr);
  gap: 24px;
  align-items: center;
  padding: 10px 0;
  border-bottom: 1px dashed #f0f2f6;
}
.param-ctrl {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.ratio-v {
  width: 56px;
  text-align: right;
}
.dict {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 16px;
}
.dict-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.save-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  position: sticky;
  bottom: 0;
  background: var(--c-surface);
  padding-top: 12px;
  border-top: 1px solid var(--c-border);
}
.units {
  display: grid;
  grid-template-columns: repeat(3, auto);
  column-gap: 12px;
}
</style>
