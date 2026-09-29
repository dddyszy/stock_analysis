"""结构回放与改写记录：看「当时能看到的」结构和「今天回看」差多少。

- 回放：截到某一天的日线，做和平时一样的分析（最近 STATE_BARS 根），得到那一天的笔、中枢、买卖点和结构状态。
- 改写记录：最近 LOG_BARS 个交易日逐日回放，记下每个买卖点第一次出现、第一次确认和最后一次出现的日期；
  最后一天仍然存在的算「保留至今」，中途消失的算「被改写」。窗口和回测、基准率一致。
单次分析不到 1 毫秒，都按需计算，不做缓存。
"""

from datetime import date

from app.analysis.structure import STATE_BARS, STATE_NAMES, classify
from app.chan import TYPE_NAMES
from app.chan.analyzer import analyze
from app.db.models import KlineDaily
from app.services.chan_service import chan_config
from app.services.sync import load_bars

LOG_BARS = 500
SHOW_BARS = 400
LATE_BARS = 30  # 信号日之后这么多根才第一次出现的，是回放窗口边缘的产物，不统计
EDGE_BARS = 60  # 消失时离窗口起点不到这么多根的，算移出回看窗口，不算改写


def replay(code: str, on: date, bars: int = SHOW_BARS) -> dict | None:
    series = load_bars(KlineDaily, code, STATE_BARS, end=on)
    if len(series) < 60:
        return None
    res = analyze(series, "day", chan_config())
    payload = res.to_dict(include_bars=True)
    if len(payload["bars"]) > bars:
        cut = payload["bars"][-bars][0]
        payload["bars"] = payload["bars"][-bars:]
        for k in ("dif", "dea", "hist"):
            payload["macd"][k] = payload["macd"][k][-bars:]
        for k, f in (("bis", "end_dt"), ("segments", "end_dt"), ("zhongshus", "end_dt"), ("signals", "dt")):
            payload[k] = [x for x in payload[k] if x[f] >= cut]
    st = classify(res)
    payload["as_of"] = series[-1].dt.isoformat()
    payload["state"] = {**st.to_dict(), "name": STATE_NAMES.get(st.key, st.key)}
    return payload


def rewrite_log(code: str, bars: int = LOG_BARS) -> dict | None:
    series = load_bars(KlineDaily, code)
    if len(series) < STATE_BARS + 20:
        return None
    cfg = chan_config()
    start = max(STATE_BARS - 1, len(series) - bars)
    seen: dict[tuple, dict] = {}
    last_keys: set = set()
    for t in range(start, len(series)):
        res = analyze(series[max(0, t - STATE_BARS + 1) : t + 1], "day", cfg)
        day = series[t].dt
        keys = set()
        for s in res.signals:
            key = (s.type, s.dt)
            keys.add(key)
            rec = seen.get(key)
            if rec is None:
                rec = seen[key] = {"type": s.type, "name": TYPE_NAMES.get(s.type, s.type), "signal_date": s.dt.isoformat(),
                                   "price": round(s.price, 3), "first_seen": day.isoformat(), "confirmed_at": None,
                                   "last_seen": day.isoformat(), "appearances": 0}
            rec["last_seen"] = day.isoformat()
            rec["appearances"] += 1
            if s.confirmed and rec["confirmed_at"] is None:
                rec["confirmed_at"] = day.isoformat()
        last_keys = keys
    first_day = series[start].dt
    idx = {b.dt: i for i, b in enumerate(series)}
    items = []
    for key, rec in seen.items():
        i_sig = idx.get(key[1])
        i_first = idx[date.fromisoformat(rec["first_seen"])]
        # 回放开始前就已出现的，或信号日之后很久才在窗口边缘冒出来的，首次出现日期没有意义
        if i_sig is None or (rec["first_seen"] == first_day.isoformat() and key[1] < first_day) or i_first - i_sig > LATE_BARS:
            continue
        rec["lag_bars"] = i_first - i_sig
        if key in last_keys:
            rec["status"], rec["status_name"] = "kept", "保留至今"
        elif idx[date.fromisoformat(rec["last_seen"])] + 1 - i_sig >= STATE_BARS - EDGE_BARS:
            rec["status"], rec["status_name"] = "aged", "移出回看窗口"
        elif rec["confirmed_at"]:
            rec["status"], rec["status_name"] = "rewritten_after_confirm", "确认后被改写"
        else:
            rec["status"], rec["status_name"] = "rewritten", "被改写"
        items.append(rec)
    items.sort(key=lambda r: r["first_seen"], reverse=True)
    judged = [r for r in items if r["status"] != "aged"]
    total = len(judged)
    rewritten = sum(r["status"] != "kept" for r in judged)
    confirmed = [r for r in judged if r["confirmed_at"]]
    by_type: dict[str, dict] = {}
    for r in judged:
        b = by_type.setdefault(r["name"], {"total": 0, "rewritten": 0})
        b["total"] += 1
        b["rewritten"] += int(r["status"] != "kept")
    return {
        "from": first_day.isoformat(), "to": series[-1].dt.isoformat(), "window": STATE_BARS, "items": items,
        "summary": {
            "total": total, "kept": total - rewritten, "rewritten": rewritten, "aged": len(items) - total,
            "rewritten_ratio": round(rewritten / total, 3) if total else None,
            "confirmed": len(confirmed),
            "confirmed_rewritten_ratio": round(sum(r["status"] == "rewritten_after_confirm" for r in confirmed) / len(confirmed), 3) if confirmed else None,
            "avg_lag_bars": round(sum(r["lag_bars"] for r in judged) / total, 1) if total else None,
            "rewritten_after_confirm": sum(r["status"] == "rewritten_after_confirm" for r in items),
            "by_type": by_type,
        },
    }
