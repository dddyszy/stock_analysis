# AGENTS.md · 后端规范

同时遵守根目录 [AGENTS.md](../AGENTS.md)。

## 分层（依赖只能向下）

`api/routers` → `services` / `jobs` → `analysis` → `chan`（纯计算）。外部数据只经 `providers`（`create_provider()`）和 `mcp`。

- `chan/` 不访问数据库和网络：输入是按时间升序的 bars，输出结构化结果。修改缠论规则必须同时补充 `tests/chan/` 用例。
- 业务代码不直接写 MCP 工具名，统一通过 `DataProvider`、`SimTradeGateway`、`services/app_sync.py` 封装。

## MCP 调用

- 一律走 `McpSession.call`。它负责：全局限流和按工具的自适应限流、"服务限频"冷却重试、临时错误重试、401 自动续期、记录 `mcp_call_log`。
- 字段解析用 `providers/parsing.py` 的 `pick` / `find_records` / `to_float` 等容错函数。接入新工具时，先运行 `uv run python -m app.mcp.probe` 保存真实样本到 `tests/fixtures/mcp/`，再按真实字段写解析，并在 `tests/test_real_fixtures.py` 补用例。
- 可选数据（风险、市场画像、实时宽度等）必须用 `asyncio.wait_for` 设超时，失败就降级，不能让任务卡住。
- 已知限制：`data_finance` 单次只能可靠返回 5 只，限频最紧（见根目录 AGENTS.md 的实测值），批量任务被限频时要原地等待重试，不能跳过；`data_score` 单次 100 只；`data_sector` 的成份股 code 字段有误，股票池改用 `tool_filter` 按行业获取。

## 数据库

- 使用 SQLAlchemy 2 类型注解模型。数值列用 `Double`，不要用 `Float`（MySQL 会建成单精度）。
- 读写用 `session_scope()`；批量写用 `sqlalchemy.dialects.mysql.insert(...).on_duplicate_key_update(...)`，每批不超过 1000 行。
- 修改模型后用 Alembic autogenerate 生成迁移；服务启动时 `main.migrate()` 会自动升级。

## 后台任务

- 耗时逻辑写成 `async def xxx(ctx: JobContext)`，用 `ctx.update(done, total, message)` 报告进度，每个步骤都要更新 message。
- 接口触发用 `start_job(name, fn)`，定时任务用 `run_job`；同名任务互斥。任务必须能断点续传（借助 `sync_progress` 或检查已有数据）。重启时未完成的任务会自动标记为 `interrupted`。
- CPU 密集的部分（全市场缠论扫描、回测）放进 `asyncio.to_thread`。
- 需要让用户知道的异常（任务失败、步骤部分失败、数据过期、授权失效、盘中止损等）调用 `services/notify.py` 的 `notify(event, title, message, level, key)`；同一 `event + key` 每天只记一次。任务失败和重启中断已在 `services/jobs.py` 自动通知，不要重复发。
- 限频严格的 MCP 工具在一次运行中被限频过多时，用 `services/runtime_state.py` 的 `set_cooldown` 记下冷却时间，后续运行先查 `cooldown_until` 再决定是否调用。

## 多策略

- 策略放在 `app/strategies/`，每个策略提供单只股票的判定函数（如 `dianjin.evaluate(series, params, i)`），实盘扫描（`services/strategy_scan.py`）和回测必须调用同一个函数，第 i 根 K 线的判定只能用到第 i 根及以前的数据。
- 策略页必须显示该策略的回测结论（未检验时显示「尚未检验」）和幸存者偏差提示；新策略不能进入「推荐」页的综合分。

## 价值类指标

- 股息率、PE、市值、近三年 ROE、MA120 只能用 `analysis/value.py` 的 `build_series()` 计算，实盘判定和回测共用。
- `kline_daily` 统一存**等比前复权**（最新一天等于原价）：腾讯接口给的前复权是按除权公式逐次变换的（只有派息时等于整体减常数），拉取时由 `services/price_adjust.py` 用分红送转事件还原成原价再按比例复权；配股等没有分红文字的除权，用前复权价和原价拟合出等效系数。已存的旧日线由 `convert_stored_klines` 一次性换算，按 `sync_progress` 的 `kline_ratio` 记进度，不能重复换算。
- 价值类计算（股息率、PE、市值）用 `kline_daily_raw`（不复权）加 `analysis/value.py` 的乘法复权因子。
- 分红按除权日生效，财报按公告日（`publ_date`，缺失时用法定披露截止日）生效；不得使用当天还没公告的数据。

## 结构分析（v0.6 起的核心）

- 结构状态只能用 `analysis/structure.py` 的 `classify()` 判定，并且只看最近 `STATE_BARS`（401）根日线（`StockAnalysis.day_state`）；实盘报告（`services/structure_report.py`）、每日状态表（`structure_state_daily`）和历史基准率（`research/base_rates.py`）必须共用它，保证页面上的历史比例统计的是同一种结构。新增状态要同时补 `STATE_NAMES`、`scenarios()` 的文案和 `tests/test_structure.py`。
- 每个状态的上方 / 下方价位必须满足「下方 ≤ 现价 ≤ 上方」；「延续 / 例外」按之后收盘价先越过哪条线判定（`first_touch` / `outcome`）。
- 历史比例只能按当时可见的数据逐日回放统计，样本少于 `MIN_CELL` 的格子不展示；报告文案只描述结构和比例，不写「建议买入」「看涨」之类的预测性结论。

## 快照与测试数据

- 只保留最新值的表（`stock_basic`、`stock_risk_label`）必须同时写每日快照（`stock_status_daily`、`stock_risk_label_daily`）；回测读取历史状态一律用 `services/snapshot.py` 的 `snapshot_at(日期)`。
- 财报的可获得日期记在 `fundamental_quarterly.first_seen`（首次拉到与法定披露截止日取早者，截止日来自 `ashare_rules.disclosure_deadline`），回测只能用 `first_seen` 不晚于当天的报表。
- 股票池刷新：只要有行业拉取失败就只新增、不下线；连续两次完整刷新缺席且行情也查不到，才记 `delisted_on`。已退市股票的 K 线不删除。
- `tests/conftest.py` 强制 `DATA_PROVIDER=mock`，所有测试只能连 `chan_stock_mock`；需要数据库的测试使用 `mock_db` fixture，并清理自己写入的数据。

## 结构候选、回测与跟踪（代码与表名沿用 recommend_*）

- 涨跌停相关计算一律调用 `services/ashare_rules.py`（`limit_prices`、`is_one_price_limit_up` 等），传入 `is_st`；新增板块规则只改这个模块，并补 `tests/test_ashare_rules.py`。
- 候选流程：基本面与风险标签（硬排除 / 扣分）→ 流动性（20 日均成交额、流通市值）→ 缠论信号打分 → 按 `confirmed` 和 `regime_block` 分主候选和观察池。被过滤的原因要计入 `stats`，便于前端解释。
- 买卖点只在笔级别生成（`scope` 恒为 `bi`）；线段和线段中枢只用于结构展示。线段级别买点和周线共振打分已按回测结论删除（前者 4 年只有个位数样本，后者两份样本都显著跑输），不要再加回来。同理，「盈亏比 ≥ 2」的开仓过滤也已删除（两份样本都显示它让结果从接近随机变为显著跑输），盈亏比只展示、不参与筛选和开仓判断。背驰结果放在 `extra.divergence`（面积必须满足，DIF、斜率、量能至少两项同意才算强背驰），背驰离开段的日期范围放在 `extra.leave_range`。
- 回测参数权重建议只能用样本内交易计算；结果必须同时给出样本外、随机对照组的优势和自助法置信区间。
- 信号与随机组比较一律用 `matched_edge`（随机组按信号所在的市场状态或年份加权），避免把大盘择时误当成买点优势。每笔交易的入场属性（市场状态、背驰强度、行业、因子值）存在 `backtest_trade.tags`，只能用入场前可见的数据计算。
- 想用回测结果收紧选股规则时，先看「逐年滚动检验」（`walk_forward`）：只有用过去数据挑出的组合在之后年份仍跑赢随机组，才算可用。
- 指数市场状态（中证 1000 相对 60 日均线）只从 `analysis/market_env.py` 的 `index_regime_map` / `latest_index_regime` 取，回测诊断和选股降级（`regime_block`）必须用同一口径。
- 回测按股票多进程并行（`_run_parallel`），每只股票的随机数种子由"种子 + 代码"决定；`backtest_series` 必须保持无副作用、可序列化，不能在里面访问数据库。
- 比较规则变体用 `run_param_experiment`（实验组定义在 `EXPERIMENTS`）：同一批股票、同一种子，各组存为带 `label` / `experiment` 的回测记录，再用 `compare_runs` 对比。
- 入场因子只能注册在 `research/factors.py` 的 `FACTORS` 里，计算函数只能读取 `ctx.t` 及之前的数据（`tests/test_research.py` 会检查改动未来 K 线后因子值不变）；与信号无关的因子要设 `for_control=True`，随机组同样计算。
- `matched_edge` 按入场月份整体重抽样（同期交易并不独立），不要改回按单笔重抽样；新规则进选股前必须通过 `research/analysis.py` 的六条门槛（含 BH 校正）。
- 回测入场必须与实盘选股一致：次日开盘价不高于结构止损位的买点视为失效；1R 不得小于入场价的 `MIN_RISK_PCT`。
- 候选跟踪（`services/recommend_tracking.py`）每天收盘后更新最近 60 天的候选，记录 5/10/20 日收益、相对沪深 300 和中证 1000 的超额、最大回撤、先触及止损还是目标。

## 接口

- 会触发后台任务或调用 MCP 的路由写成 `async def`；只查数据库的写成普通 `def`。
- 错误用 `HTTPException(400/404, "中文说明")`。MCP 相关异常由 `main.py` 统一转换：未授权 401、业务错误 400、网络错误 502。
- 配置只从 `core/config.py` 的 `Settings`（读取 `.env`）获取，新增配置要同步更新 `.env.example`。策略参数放在 `services/strategy_config.py` 的 `DEFAULT_PARAMS`，并在前端设置页的 `LABELS` 中补充中文名称。

## 注释

只写代码本身表达不了的约束（例如"data_finance 单次只能可靠返回 5 只"），不写"下一行做什么"。
