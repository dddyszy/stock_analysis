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
- 已知限制：`data_finance` 单次只能可靠返回 5 只；`data_score` 单次 100 只；`data_sector` 的成份股 code 字段有误，股票池改用 `tool_filter` 按行业获取。

## 数据库

- 使用 SQLAlchemy 2 类型注解模型。数值列用 `Double`，不要用 `Float`（MySQL 会建成单精度）。
- 读写用 `session_scope()`；批量写用 `sqlalchemy.dialects.mysql.insert(...).on_duplicate_key_update(...)`，每批不超过 1000 行。
- 修改模型后用 Alembic autogenerate 生成迁移；服务启动时 `main.migrate()` 会自动升级。

## 后台任务

- 耗时逻辑写成 `async def xxx(ctx: JobContext)`，用 `ctx.update(done, total, message)` 报告进度，每个步骤都要更新 message。
- 接口触发用 `start_job(name, fn)`，定时任务用 `run_job`；同名任务互斥。任务必须能断点续传（借助 `sync_progress` 或检查已有数据）。重启时未完成的任务会自动标记为 `interrupted`。
- CPU 密集的部分（全市场缠论扫描、回测）放进 `asyncio.to_thread`。

## 推荐、回测与跟踪

- 涨跌停相关计算一律调用 `services/ashare_rules.py`（`limit_prices`、`is_one_price_limit_up` 等），传入 `is_st`；新增板块规则只改这个模块，并补 `tests/test_ashare_rules.py`。
- 推荐流程：基本面与风险标签（硬排除 / 扣分）→ 流动性（20 日均成交额、流通市值）→ 缠论信号打分 → 按 `confirmed` 分主推荐和观察池。被过滤的原因要计入 `stats`，便于前端解释。
- 缠论信号分笔级别（`scope="bi"`）和线段级别（`scope="seg"`）。背驰结果放在 `extra.divergence`（面积必须满足，DIF、斜率、量能至少两项同意才算强背驰），背驰离开段的日期范围放在 `extra.leave_range`。
- 回测参数权重建议只能用样本内交易计算；结果必须同时给出样本外、随机对照组的优势和自助法置信区间。
- 信号与随机组比较一律用 `matched_edge`（随机组按信号所在的市场状态或年份加权），避免把大盘择时误当成买点优势。每笔交易的入场属性（市场状态、周线共振、背驰强度）存在 `backtest_trade.tags`，只能用入场前可见的数据计算。
- 想用回测结果收紧选股规则时，先看「逐年滚动检验」（`walk_forward`）：只有用过去数据挑出的组合在之后年份仍跑赢随机组，才算可用。
- 推荐跟踪（`services/recommend_tracking.py`）每天收盘后更新最近 60 天的推荐，记录 5/10/20 日收益、相对沪深 300 和中证 1000 的超额、最大回撤、先触及止损还是目标。

## 接口

- 会触发后台任务或调用 MCP 的路由写成 `async def`；只查数据库的写成普通 `def`。
- 错误用 `HTTPException(400/404, "中文说明")`。MCP 相关异常由 `main.py` 统一转换：未授权 401、业务错误 400、网络错误 502。
- 配置只从 `core/config.py` 的 `Settings`（读取 `.env`）获取，新增配置要同步更新 `.env.example`。策略参数放在 `services/strategy_config.py` 的 `DEFAULT_PARAMS`，并在前端设置页的 `LABELS` 中补充中文名称。

## 注释

只写代码本身表达不了的约束（例如"data_finance 单次只能可靠返回 5 只"），不写"下一行做什么"。
