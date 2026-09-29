import random
from datetime import date, timedelta

from app.providers.base import Bar
from app.services import structure_replay


def _walk(n: int, seed: int = 3) -> list[Bar]:
    rng = random.Random(seed)
    px, out, d = 10.0, [], date(2022, 1, 3)
    while len(out) < n:
        if d.weekday() < 5:
            o = px
            px = max(1.0, px * (1 + rng.gauss(0, 0.02)))
            out.append(Bar(dt=d, open=o, high=max(o, px) * 1.01, low=min(o, px) * 0.99, close=px, volume=1e6))
        d += timedelta(days=1)
    return out


def test_rewrite_log_statuses(monkeypatch):
    bars = _walk(900)
    monkeypatch.setattr(structure_replay, "load_bars", lambda model, code, limit=None, end=None: bars[-limit:] if limit else bars)
    log = structure_replay.rewrite_log("sh600000")
    s = log["summary"]
    assert s["total"] == s["kept"] + s["rewritten"]
    assert {r["status"] for r in log["items"]} <= {"kept", "aged", "rewritten", "rewritten_after_confirm"}
    assert all(r["lag_bars"] <= structure_replay.LATE_BARS for r in log["items"])


def test_replay_cuts_to_date(monkeypatch):
    bars = _walk(900)
    on = bars[600].dt

    def fake(model, code, limit=None, end=None):
        sel = [b for b in bars if end is None or b.dt <= end]
        return sel[-limit:] if limit else sel

    monkeypatch.setattr(structure_replay, "load_bars", fake)
    p = structure_replay.replay("sh600000", on)
    assert p["as_of"] == on.isoformat()
    assert p["bars"][-1][0] == on.isoformat()
    assert all(s["dt"] <= on.isoformat() for s in p["signals"])
