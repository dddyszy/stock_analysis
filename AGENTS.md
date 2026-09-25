# AGENTS.md · 缠论股票分析系统

本文件是给 AI 编程助手（Cursor、Codex、Claude Code 等）和开发者的项目约定。
子目录规范见 [frontend/AGENTS.md](frontend/AGENTS.md) 和 [backend/AGENTS.md](backend/AGENTS.md)，编辑对应目录时同时遵守。

## 项目结构

- 后端 `backend/`：Python 3.11 + FastAPI + SQLAlchemy 2 + MySQL（库名 `chan_stock`），uv 管理依赖。
- 前端 `frontend/`：Vue3 + Vite + TypeScript + Element Plus + ECharts，浅色专业风。
- 范围只做沪深 A 股（`EXCHANGES=sh,sz`），股票代码统一为 `sh600519` / `sz000001` 格式。
- 界面文案、代码注释、提交说明一律使用简体中文。

## 常用命令

```bash
# 后端（启动时自动建库、执行迁移）
cd backend && uv run uvicorn app.main:app --port 8000
# 前端（/api 代理到 8000）
cd frontend && npm run dev
# 未授权时用合成数据开发（自动使用独立的 chan_stock_mock 库）
cd backend && DATA_PROVIDER=mock uv run uvicorn app.main:app --port 8000
```

## 数据来源分工（不要随意更改）

| 数据 | 来源 |
| --- | --- |
| 历史日线、行情快照、PE/PB/市值 | 腾讯公开接口（`backend/app/providers/tencent_public.py`，自适应限速、多域名轮换） |
| 股票池、ST、交易日历、诊股评分、市场宽度与画像 | westock-mcp |
| 三大报表 | MCP `data_finance`，每批最多 5 只，只拉候选股并缓存 20 天 |
| 模拟盘、自选分组、股价提醒 | MCP `portfolio_*` |

| 风险标签与风险事件 | MCP `tool_label` / `tool_event` 批量名单（部分标签上游只返回前 200 只） |

westock-mcp 的行情类工具每个约每分钟只允许 3～5 次调用。新增 MCP 调用必须考虑限频：能批量就批量；可选数据必须设超时，拿不到就降级。

## 投资口径（不要随意更改）

- A 股交易规则（板块、涨跌幅比例、涨跌停价、一字板判断）只从 `backend/app/services/ashare_rules.py` 取，任何模块都不能自己写死 10% 或 20%。
- 回测、推荐跟踪的收益都按"信号次日开盘价 + 滑点"入场；一字涨停无法买入、一字跌停无法卖出。
- 衡量策略时同时看超额收益（相对中证 1000）、样本外表现和随机对照组，不单独用胜率或平均 R 下结论。回测从当前在市股票抽样，存在幸存者偏差，结果页必须提示这一点。
- 主推荐只收已确认的信号，未确认的进观察池；只有主推荐写回 App。

## 安全约束

- 写入用户真实的腾讯账户（自选分组、股价提醒）前，第一次必须征得用户同意；设置页的写回开关必须能关闭这一功能。
- 模拟盘下单、执行持仓建议，都必须在前端让用户确认后才调用。
- 不删除用户自己的自选股和提醒；覆盖前先保存原值，平仓后恢复。加入推荐分组前已在自选里的股票记为用户原有（`user_owned`），推荐结束后保留；「全部」「沪深」等系统分组不算用户分组。
- 合成数据只能写入 `chan_stock_mock`，绝不能进入 `chan_stock`。

## 完成修改前必须验证

1. 后端：`cd backend && uv run pytest -q` 全部通过（推送到 `dev`、`master` 后 GitHub Actions 会自动再跑一遍后端测试和前端构建）。
2. 前端：`cd frontend && npm run build` 通过（包含 `vue-tsc` 类型检查）。
3. 改了页面：用 `.shots/shot.sh <名称> <路径>` 通过无头 Chrome 截图检查（输出到 `/tmp/chan_shots/`）。
4. 改了表结构：`uv run alembic revision --autogenerate -m "说明"` 生成迁移，并对 mock 库执行 `DATA_PROVIDER=mock uv run alembic upgrade head`。
