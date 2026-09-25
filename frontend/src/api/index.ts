import http from './http'

type Any = any

const get = <T = Any>(url: string, params?: Any, silent = false): Promise<T> => http.get(url, { params, silent }) as Promise<T>
const post = <T = Any>(url: string, data?: Any): Promise<T> => http.post(url, data) as Promise<T>
const put = <T = Any>(url: string, data?: Any): Promise<T> => http.put(url, data) as Promise<T>
const del = <T = Any>(url: string): Promise<T> => http.delete(url) as Promise<T>

export const api = {
  health: () => get('/health'),
  // 授权
  authStatus: () => get('/auth/mcp/status'),
  authStart: () => post<{ url: string }>('/auth/mcp/start'),
  authToken: (access_token: string, expires_in?: number) => post('/auth/mcp/token', { access_token, expires_in }),
  authLogout: () => post('/auth/mcp/logout'),
  authTest: () => post('/auth/mcp/test'),
  authProbe: () => post('/auth/mcp/probe'),
  // 市场
  marketEnv: () => get('/market/env'),
  marketEnvHistory: (days = 120) => get('/market/env/history', { days }),
  marketRefresh: () => post('/market/env/refresh'),
  marketLive: () => get('/market/live', undefined, true),
  indicesLive: () => get('/market/indices/live', undefined, true),
  indicesSeries: (days = 60) => get('/market/indices/series', { days }, true),
  // 个股
  searchStocks: (q: string) => get('/stocks/search', { q, limit: 15 }),
  stockInfo: (code: string) => get(`/stocks/${code}`),
  stockChan: (code: string, level: string, bars = 400) => get(`/stocks/${code}/chan`, { level, bars }),
  stockQuote: (code: string) => get(`/stocks/${code}/quote`, undefined, true),
  stockFundamentals: (code: string) => get(`/stocks/${code}/fundamentals`),
  watchlist: () => get('/watchlist'),
  addWatch: (code: string, note?: string) => post('/watchlist', { code, note }),
  delWatch: (code: string) => del(`/watchlist/${code}`),
  // 推荐
  recommendLatest: () => get('/recommend/latest'),
  recommendRuns: () => get('/recommend/runs'),
  recommendRun: (id: number) => get(`/recommend/runs/${id}`),
  recommendTrigger: () => post('/recommend/run'),
  recommendTracking: (days = 180) => get('/recommend/tracking', { days }),
  recommendRunPerf: (id: number) => get(`/recommend/runs/${id}/perf`),
  trackingRefresh: () => post('/recommend/tracking/refresh'),
  // 持仓
  entryPreview: (body: Any) => post('/portfolio/entry-preview', body),
  createPlan: (body: Any) => post('/portfolio/plans', body),
  plans: (status = 'open') => get('/portfolio/plans', { status }),
  plan: (id: number) => get(`/portfolio/plans/${id}`),
  executeAdvice: (id: number) => post(`/portfolio/plans/${id}/execute`),
  closeManual: (id: number, price?: number, reason?: string) => post(`/portfolio/plans/${id}/close-manual`, { price, reason }),
  evaluate: () => post('/portfolio/evaluate'),
  events: (limit = 100, level?: string) => get('/portfolio/events', { limit, level }),
  // 模拟盘
  simAccount: () => get('/sim/account'),
  simPositions: () => get('/sim/positions'),
  simProfit: () => get('/sim/profit', undefined, true),
  simOrders: () => get('/sim/orders'),
  simPlace: (body: Any) => post('/sim/orders', body),
  simLimitPrice: (code: string, direction: string) => get('/sim/limit-price', { code, direction }),
  simCancel: (orderId: string) => post(`/sim/orders/${orderId}/cancel`),
  simSync: (range = 'today') => post(`/sim/sync?range=${range}`),
  simSnapshot: () => post('/sim/snapshot'),
  simStats: () => get('/sim/stats'),
  // 回测
  backtestStart: (body: Any) => post('/backtest', body),
  backtestRuns: () => get('/backtest/runs'),
  backtestRun: (id: number) => get(`/backtest/runs/${id}`),
  backtestApply: (id: number, activate: boolean) => post(`/backtest/runs/${id}/apply`, { activate }),
  backtestExperimentStart: (body: Any) => post('/backtest/experiments/exit', body),
  backtestExperimentLatest: () => get('/backtest/experiments/latest'),
  // 设置
  overview: () => get('/settings/overview'),
  jobs: (limit = 30) => get('/settings/jobs', { limit }),
  notifications: (limit = 50) => get('/notify', { limit }),
  notifyUnread: () => get('/notify/unread', undefined, true),
  notifyReadAll: () => post('/notify/read-all', {}),
  startJob: (name: string) => post(`/settings/jobs/${name}`),
  cancelJob: (name: string) => post(`/settings/jobs/${name}/cancel`),
  strategy: () => get('/settings/strategy'),
  saveStrategy: (body: Any) => post('/settings/strategy', body),
  activateStrategy: (id: number) => post(`/settings/strategy/${id}/activate`),
  appSync: () => get('/settings/app-sync'),
  setAppSync: (enabled: boolean) => put('/settings/app-sync', { enabled }),
  runAppSync: () => post('/settings/app-sync/run'),
}
