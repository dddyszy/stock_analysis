# AGENTS.md · 前端规范

同时遵守根目录 [AGENTS.md](../AGENTS.md)。

## 一、视觉设计（浅色专业风）

- 浅灰底（`--c-bg`）+ 白色卡片 + 细边框 + 轻阴影，圆角 6/10/14px（`--radius-sm` / `--radius` / `--radius-lg`）。
- 只有一个主色：蓝色 `--c-primary`。涨跌遵循 A 股习惯：红涨 `--c-up`、绿跌 `--c-down`，只用于数字、图形、徽标，不做大面积铺色。
- 颜色、间距、阴影一律使用 `src/styles/theme.css` 中的变量，不在组件里写死色值；ECharts 使用 `src/utils/echartsTheme.ts` 的 `COLORS` 和 `chanLight` 主题。
- 数字（价格、涨跌、分数、数量）加 `num` 类，使用等宽数字字体。
- 只保证 1280px 以上宽度显示正常，不做移动端适配，不做深色主题。

```vue
<!-- 错误 -->
<span style="color:#e53935">+2.1%</span>
<!-- 正确 -->
<PriceChange :pct="2.1" />
```

## 二、页面结构

- 每个页面：`<div class="page">` → `PageHeader`（标题、副标题、右侧操作）→ 内容。
- 信息层级：顶部放关键数字（`StatCard`），中间放主图表，底部放明细表。
- 内容块一律用 `SectionCard` 包裹；栅格使用 `grid grid-2/3/4` 工具类，间距使用 `--gap`。
- 外壳在 `App.vue`：左侧可折叠导航、顶栏（搜索、市场温度徽标、授权状态）、指数行情条。新页面只需在 `router.ts` 注册并加到导航菜单。

## 三、组件选用（`src/components/ui/`，从 `@/components/ui` 引入）

| 场景 | 组件 |
| --- | --- |
| 价格与涨跌 | `PriceChange` |
| 缠论买卖点 | `SignalBadge`（区分已确认、未确认、已失效；传 `scope` 显示线段级别"段"，传 `strong` 显示强背驰） |
| 综合分 / 分项分 | `ScoreRing` / `ScoreBar` |
| 止损、现价、目标价 | `RiskRewardBar`（自动标注盈亏比） |
| 持仓状态机 | `StatusSteps` |
| 迷你走势 | `Sparkline` |
| 空数据 | `EmptyState`，并给出下一步操作按钮 |

能画出来的就不用纯文字：评分用环形图和条形图，行业强弱用矩形树图，涨跌家数用堆叠条，持仓进度用步骤条。缺少合适的组件时，先在 `components/ui/` 新增通用组件再使用。

## 四、代码规范

- 组件一律使用 `<script setup lang="ts">`，路径别名 `@/`。
- 后端请求只通过 `@/api` 的 `api.xxx()`；新增接口先在 `src/api/index.ts` 加方法。错误提示由 `api/http.ts` 拦截器统一处理，页面里不要重复 `ElMessage.error`。
- 非关键数据（行情条、实时宽度等）使用静默请求（`get(url, params, true)`），失败不打扰用户。
- 格式化统一用 `@/utils/format`（`num`、`pct`、`ratioPct`、`money`、`dt`）；信号名、走势类型等中文映射也在这里维护。

### ECharts

必须使用 `useChart`，它负责主题、容器延迟出现（`v-if`）、resize 和销毁。模板里用 `:ref="xxx.el"` 绑定，不要用字符串 ref（`vue-tsc` 会报未使用变量）：

```ts
const trend = useChart(() => (data.value.length ? { series: [/* ... */] } : null), () => data.value)
```

```vue
<div :ref="trend.el" style="height: 280px" />
```

### 后台任务

触发耗时任务（同步、推荐、回测等）后使用 `useJobPoller`：`start(jobName)` 轮询进度，结束回调里刷新数据。进度要显示 `progress/total` 和 `message`，运行中的任务提供"取消"按钮。

### 交互与安全

- 下单、执行建议、清除授权、写回 App 等操作，先用 `ElMessageBox.confirm` 说明要做什么。
- 北交所代码禁止模拟下单，相关按钮直接禁用。

### 提交前

`npm run build` 必须通过。已开启 `noUnusedLocals`，未使用的变量和导入要删除；确实不用的循环变量以 `_` 开头。
