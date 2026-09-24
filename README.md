# 缠论股票分析系统

以日线 + 周线缠论为核心，结合市场环境和基本面，提供 A 股智能选股、买卖点分析、持仓策略（分层止损 / 跟踪止损 / 分批止盈）、腾讯自选股模拟盘和历史回测。

- 后端：Python 3.11 + FastAPI + SQLAlchemy + MySQL + APScheduler，目录 `backend/`
- 前端：Vue3 + Vite + TypeScript + Element Plus + ECharts，目录 `frontend/`
- 数据源：腾讯自选股 [westock-mcp](https://github.com/infometa/workbuddyskills/tree/main/connectors/westock-mcp)（streamable HTTP，OAuth 授权）+ 腾讯公开行情接口
- 范围：沪深 A 股（`EXCHANGES=sh,sz`）

## 数据来源分工

westock-mcp 的行情类工具（K 线、行情、财报等）实测每个约每分钟 3~5 次，全市场回填不可行，因此：

| 数据 | 来源 | 说明 |
| --- | --- | --- |
| 历史日线（前复权）、每日行情快照、PE/PB/市值 | 腾讯公开接口 `ifzq.gtimg.cn` / `qt.gtimg.cn` | 无需授权；三个域名轮换、自适应限速（`KLINE_SOURCE=public`） |
| 股票池（申万一级 + 条件选股）、ST 标签、交易日历 | MCP | |
| 诊股评分（综合 / 基本面 / 风险） | MCP `data_score` | 每次 100 只，全市场约 50 次调用 |
| 三大报表 | MCP `data_finance` | 每次只能可靠返回 5 只，所以只拉候选股、持仓、自选，按季度缓存 |
| 市场宽度、板块排行、市场画像 | MCP | |
| 模拟盘、自选分组、股价提醒 | MCP `portfolio_*` | |

基本面分两级：全市场用腾讯诊股评分 + 行业估值分位做硬过滤和初步打分；推荐时对排名靠前的候选股补拉三大报表，用 ROE、增速、现金流等重新打分，并补做"连续亏损、商誉过高"等依赖财报的过滤。

MCP 调用遇到"服务限频"会自动冷却重试、按工具降速；设置页可以看到每个工具的当前速率。

## 快速开始

```bash
cp .env.example .env            # 默认 root/123456@127.0.0.1:3306，库名 chan_stock

# 后端（启动时自动建库并执行迁移）
cd backend
uv sync
uv run uvicorn app.main:app --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev                      # http://localhost:5173
```

首次使用：

1. 打开「设置」→「授权腾讯自选股」，在腾讯页面登录并同意授权，完成后自动回到设置页。
2. 点「测试连接」确认可用，再点「探测工具并保存样本」，真实返回会保存到 `backend/tests/fixtures/mcp/`，用于核对字段解析。
3. 点「首次初始化」：同步股票池（约 25 分钟，受 MCP 限频）→ 回填近 5 年日线（公开接口，约 1 小时，可断点续传）→ 同步评分、估值和候选股财报。进度在设置页可见，运行中的任务可以取消，重启后端后重新运行会从断点继续。
4. 初始化完成后点「收盘后流水线（强制运行）」生成第一份市场环境、推荐和持仓建议。之后每个交易日自动运行。

## 未授权时开发：模拟数据模式

```bash
DATA_PROVIDER=mock uv run uvicorn app.main:app --port 8000
```

使用本地合成的 300 只股票数据，数据库自动切换为 `chan_stock_mock`，不会污染真实库；模拟盘使用仅供联调的本地撮合，写回 App 自动关闭。

## 定时任务（Asia/Shanghai，任务前先判断交易日和授权状态）

| 时间 | 任务 |
| --- | --- |
| 交易日 15:30 | `data_quote` 批量补当日日线；昨收与本地不一致视为除权，重拉该股全部历史；更新周线 |
| 交易日 16:00 | 市场环境 → 全市场缠论推荐 → 持仓评估 → 模拟盘对账快照 → 写回 App |
| 每周一 08:30 | 刷新股票池（新股、ST、行业） |
| 每周六 10:00 | 同步全市场诊股评分与估值 |
| 4/8/10 月末、5/9/11 月初 18:00 | 评分估值 + 持仓、自选、最近推荐股的财报 |
| 交易时段每 5 分钟 | 实时价检查硬止损，记录预警 |
| 交易时段每分钟 | 有待成交委托时同步模拟盘成交 |

## 主要模块

- `backend/app/chan/`：缠论引擎（纯计算）。包含关系 → 分型 → 笔（新笔/老笔）→ 线段（特征序列，含缺口情况）→ 笔中枢/线段中枢 → 走势类型 → MACD 背驰 → 一二三类买卖点；周线与日线联立。最后一笔标记为未确认，未确认信号只作预警。
- `backend/app/analysis/`：市场温度（指数缠论状态 50% + 市场宽度 35% + 量能 15%，映射到仓位上限）和基本面（硬过滤 + 0~100 打分）。
- `backend/app/services/position_strategy.py`：开仓按风险反推仓位；每日按「硬止损 → 结构/跟踪止损 → 环境止损 → 结构卖点 → 跟踪止损上移 → 时间止损 → 加仓」评估；状态机 初始 → 保本 → 跟踪 → 减仓 → 清仓。建议需人工确认后下单。
- `backend/app/services/sim_trade.py`：`portfolio_paper_*` 网关、本地委托镜像（关联开仓信号）、复盘统计。
- `backend/app/services/app_sync.py`：推荐股写入「缠论推荐」自选分组（在你其他分组里的股票不会被删除）；止损价/目标价写为股价提醒（先查询再全量覆盖，平仓后恢复原值）。
- `backend/app/services/backtest.py`：逐根 K 线向前推进重算结构的回测，按信号类型统计并给出建议权重。
- 策略参数集中在 `strategy_config` 表，设置页可编辑、另存和切换。

## 开发规范

规范写在 `AGENTS.md` 中（Cursor、Codex、Claude Code 等 AI 编程工具都会自动读取，也适合人直接阅读）：

- [AGENTS.md](AGENTS.md)：整体结构、常用命令、数据来源分工、安全约束、验证流程
- [frontend/AGENTS.md](frontend/AGENTS.md)：浅色专业风的视觉规范、页面结构、组件选用、前端代码规范
- [backend/AGENTS.md](backend/AGENTS.md)：分层、MCP 调用、数据库、后台任务、接口与错误处理

## 测试

```bash
cd backend && uv run pytest
```
