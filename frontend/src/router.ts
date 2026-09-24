import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('./views/Dashboard.vue'), meta: { title: '市场总览' } },
    { path: '/picker', name: 'picker', component: () => import('./views/Picker.vue'), meta: { title: '智能选股' } },
    { path: '/stock/:code', name: 'stock', component: () => import('./views/StockDetail.vue'), meta: { title: '个股分析' } },
    { path: '/tracking', name: 'tracking', component: () => import('./views/Tracking.vue'), meta: { title: '推荐跟踪' } },
    { path: '/portfolio', name: 'portfolio', component: () => import('./views/Portfolio.vue'), meta: { title: '持仓与策略' } },
    { path: '/sim', name: 'sim', component: () => import('./views/SimTrade.vue'), meta: { title: '模拟盘' } },
    { path: '/backtest', name: 'backtest', component: () => import('./views/Backtest.vue'), meta: { title: '策略回测' } },
    { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue'), meta: { title: '设置' } },
  ],
})

router.afterEach((to) => {
  document.title = `${(to.meta.title as string) || ''} · 缠论股票分析系统`
})

export default router
