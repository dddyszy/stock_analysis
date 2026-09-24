<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api'
import { REGIME_COLORS, num, ratioPct } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const auth = ref<any>(null)
const env = ref<any>(null)
const indices = ref<any[]>([])
const keyword = ref('')
const collapsed = ref(localStorage.getItem('sidebar-collapsed') === '1')
let timer: number | undefined

const menus = [
  { path: '/', title: '市场总览', icon: 'DataBoard' },
  { path: '/picker', title: '智能选股', icon: 'MagicStick' },
  { path: '/tracking', title: '推荐跟踪', icon: 'DataLine' },
  { path: '/portfolio', title: '持仓策略', icon: 'Wallet' },
  { path: '/sim', title: '模拟盘', icon: 'Coin' },
  { path: '/backtest', title: '策略回测', icon: 'TrendCharts' },
  { path: '/settings', title: '设置', icon: 'Setting' },
]

const activeMenu = computed(() => (route.path.startsWith('/stock') ? '' : route.path))

function toggle() {
  collapsed.value = !collapsed.value
  localStorage.setItem('sidebar-collapsed', collapsed.value ? '1' : '0')
}

async function querySearch(q: string, cb: (items: any[]) => void) {
  if (!q.trim()) return cb([])
  const rows = await api.searchStocks(q).catch(() => [])
  cb(rows.map((r: any) => ({ value: `${r.name} ${r.code}`, code: r.code, name: r.name, industry: r.industry })))
}

function onSelect(item: any) {
  keyword.value = ''
  router.push(`/stock/${item.code}`)
}

async function loadAuth() {
  auth.value = await api.authStatus().catch(() => null)
}
async function loadTicker() {
  indices.value = await api.indicesLive().catch(() => indices.value)
}

onMounted(() => {
  loadAuth()
  loadTicker()
  api.marketEnv().then((e) => (env.value = e)).catch(() => undefined)
  timer = window.setInterval(loadTicker, 60000)
})
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="shell" :class="{ collapsed }">
    <aside class="sidebar">
      <div class="brand" @click="router.push('/')">
        <div class="logo">缠</div>
        <div v-if="!collapsed" class="brand-text">
          <div class="brand-name">缠论分析</div>
          <div class="brand-sub">A 股 · 沪深</div>
        </div>
      </div>
      <nav class="nav">
        <router-link
          v-for="m in menus"
          :key="m.path"
          :to="m.path"
          class="nav-item"
          :class="{ active: activeMenu === m.path }"
          :title="collapsed ? m.title : ''"
        >
          <el-icon :size="17"><component :is="m.icon" /></el-icon>
          <span v-if="!collapsed">{{ m.title }}</span>
        </router-link>
      </nav>
      <button class="collapse-btn" @click="toggle">
        <el-icon><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
        <span v-if="!collapsed">收起</span>
      </button>
    </aside>

    <div class="main-col">
      <header class="topbar">
        <el-autocomplete
          v-model="keyword"
          :fetch-suggestions="querySearch"
          placeholder="搜索股票代码或名称"
          clearable
          class="search"
          popper-class="search-popper"
          @select="onSelect"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
          <template #default="{ item }">
            <div class="sugg">
              <span class="bold">{{ item.name }}</span>
              <span class="muted num">{{ item.code }}</span>
              <span class="muted small grow" style="text-align: right">{{ item.industry }}</span>
            </div>
          </template>
        </el-autocomplete>
        <div class="top-right">
          <div v-if="env" class="regime" :style="{ '--rc': REGIME_COLORS[env.regime] }" @click="router.push('/')">
            <span class="regime-dot" />
            市场{{ env.regime_name }} <b class="num">{{ num(env.score, 1) }}</b>
            <span class="muted">仓位上限 {{ ratioPct(env.position_cap) }}</span>
          </div>
          <template v-if="auth">
            <span v-if="auth.provider === 'mock'" class="auth warn">模拟数据</span>
            <span v-else-if="auth.authorized" class="auth ok"><el-icon><CircleCheckFilled /></el-icon>腾讯自选股已连接</span>
            <span v-else class="auth bad" @click="router.push('/settings')"><el-icon><WarningFilled /></el-icon>未授权</span>
          </template>
        </div>
      </header>
      <div class="ticker">
        <div v-for="i in indices" :key="i.code" class="tick" @click="router.push(`/stock/${i.code}`)">
          <span class="tick-name">{{ i.name }}</span>
          <span class="num bold" :class="(i.change_pct ?? 0) >= 0 ? 'up' : 'down'">{{ i.price?.toFixed(2) ?? '--' }}</span>
          <span class="num small" :class="(i.change_pct ?? 0) >= 0 ? 'up' : 'down'">
            {{ (i.change ?? 0) >= 0 ? '+' : '' }}{{ i.change?.toFixed(2) ?? '--' }}
            {{ (i.change_pct ?? 0) >= 0 ? '+' : '' }}{{ i.change_pct?.toFixed(2) ?? '--' }}%
          </span>
        </div>
        <span class="muted small ticker-time">{{ indices[0]?.dt || '' }}</span>
      </div>
      <main class="content">
        <router-view :key="route.fullPath" @auth-changed="loadAuth" />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: flex;
  height: 100%;
}
.sidebar {
  width: 196px;
  flex-shrink: 0;
  background: var(--c-surface);
  border-right: 1px solid var(--c-border);
  display: flex;
  flex-direction: column;
  transition: width 0.15s;
}
.collapsed .sidebar {
  width: 64px;
}
.brand {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  cursor: pointer;
  border-bottom: 1px solid #f0f2f6;
}
.logo {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: linear-gradient(135deg, #2f6bff, #5b8cff);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  flex-shrink: 0;
}
.brand-name {
  font-weight: 700;
  font-size: 15px;
}
.brand-sub {
  font-size: 11px;
  color: var(--c-text-3);
}
.nav {
  flex: 1;
  padding: 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 38px;
  padding: 0 12px;
  border-radius: 8px;
  color: var(--c-text-2);
  text-decoration: none !important;
  font-size: 13.5px;
}
.collapsed .nav-item {
  justify-content: center;
  padding: 0;
}
.nav-item:hover {
  background: var(--c-surface-2);
  color: var(--c-text);
}
.nav-item.active {
  background: var(--c-primary-soft);
  color: var(--c-primary);
  font-weight: 600;
}
.collapse-btn {
  margin: 10px;
  height: 34px;
  border: none;
  background: transparent;
  color: var(--c-text-3);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 8px;
  cursor: pointer;
}
.collapse-btn:hover {
  background: var(--c-surface-2);
}
.main-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.topbar {
  height: 60px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 24px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
}
.search {
  width: 380px;
}
.search :deep(.el-input__wrapper) {
  border-radius: 999px;
  background: var(--c-surface-2);
  box-shadow: 0 0 0 1px var(--c-border) inset;
}
.sugg {
  display: flex;
  gap: 10px;
  align-items: center;
}
.top-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.regime {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 999px;
  background: var(--c-surface-2);
  border: 1px solid var(--c-border);
  cursor: pointer;
  font-size: 12px;
  white-space: nowrap;
}
.auth {
  white-space: nowrap;
}
.regime b {
  color: var(--rc);
}
.regime-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--rc);
}
.auth {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  padding: 5px 10px;
  border-radius: 999px;
}
.auth.ok {
  color: var(--c-down);
  background: var(--c-down-soft);
}
.auth.warn {
  color: #b45309;
  background: var(--c-warn-soft);
}
.auth.bad {
  color: var(--c-up);
  background: var(--c-up-soft);
  cursor: pointer;
}
.ticker {
  height: 38px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 0 24px;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
  overflow-x: auto;
}
.tick {
  display: flex;
  align-items: baseline;
  gap: 8px;
  cursor: pointer;
  white-space: nowrap;
}
.tick-name {
  color: var(--c-text-2);
  font-size: 12px;
}
.ticker-time {
  margin-left: auto;
}
.content {
  flex: 1;
  overflow-y: auto;
}
</style>
