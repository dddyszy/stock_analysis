from datetime import date

from app.analysis.market_env import index_regime_map
from app.providers.mock import UNIVERSE, _series
from app.research.base_rates import HORIZONS, _aggregate, _one
from app.services.strategy_config import DEFAULT_PARAMS


def test_replay_and_aggregate_on_mock_data():
    bench_bars = list(_series(UNIVERSE[0].code, date(2026, 9, 1)))[-700:]
    bench = {b.dt: b.close for b in bench_bars}
    regimes = index_regime_map(bench_bars)
    recs = []
    for s in UNIVERSE[1:6]:
        bars = list(_series(s.code, date(2026, 9, 1)))[-700:]
        recs += _one((s.code, bars, dict(DEFAULT_PARAMS), bench, sorted(bench), regimes))
    assert recs and all(set(r["h"]) == set(HORIZONS) for r in recs)
    rows = _aggregate(recs)
    assert {r["regime"] for r in rows} >= {"all"}
    for r in rows:
        assert abs(sum(r["outcomes"].values()) - 1) < 1e-3
        if r["state"] == "inside":
            assert r["outcomes"]["continue"] == 0 and r["outcomes"]["exception"] == 0
        else:
            assert r["outcomes"]["break_up"] == 0 and r["outcomes"]["break_down"] == 0
    # 持有期越长，分出结果的比例不会更低
    alls = {(r["state"], r["horizon"]): r for r in rows if r["regime"] == "all"}
    for (state, h), r in alls.items():
        if h == 5 and (state, 20) in alls:
            assert alls[(state, 20)]["outcomes"]["undecided"] <= r["outcomes"]["undecided"] + 1e-9
