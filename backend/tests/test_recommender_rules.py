from datetime import date
from types import SimpleNamespace

from app.analysis.fundamental import FundamentalView
from app.chan.types import Signal
from app.services.recommender import score_candidate
from app.services.strategy_config import DEFAULT_PARAMS


def _analysis(resonance: float):
    day = SimpleNamespace(last_close=10.0, closes=[10.0] * 20, volumes=[1e6] * 20)
    view = SimpleNamespace(resonance=resonance, notes=[])
    return SimpleNamespace(code="sz000001", day=day, view=view)


def _signal(sig_type: str) -> Signal:
    return Signal(type=sig_type, level="day", raw_idx=100, dt=date(2026, 9, 1), price=9.5, bi_idx=3, confirmed=True,
                  strength=0.8, stop_price=9.0, target1=13.0)


FUND = FundamentalView(code="sz000001", passed=True, score=70)


def test_resonance_weight_zero_ignores_weekly():
    params = dict(DEFAULT_PARAMS)
    up = score_candidate(_analysis(1.0), FUND, 50, params, "neutral", _signal("B2"), index_regime="range")
    down = score_candidate(_analysis(-1.0), FUND, 50, params, "neutral", _signal("B2"), index_regime="range")
    assert up["chan_score"] == down["chan_score"] <= 100
    params["resonance_weight"] = 0.2
    up = score_candidate(_analysis(1.0), FUND, 50, params, "neutral", _signal("B2"), index_regime="range")
    down = score_candidate(_analysis(-1.0), FUND, 50, params, "neutral", _signal("B2"), index_regime="range")
    assert up["chan_score"] > down["chan_score"]


def test_regime_block_moves_to_watch_pool():
    params = dict(DEFAULT_PARAMS)
    b1_up = score_candidate(_analysis(0), FUND, 50, params, "neutral", _signal("B1"), index_regime="up")
    assert b1_up["pool"] == "watch" and any("跑输随机入场" in r for r in b1_up["reasons"])
    b1_down = score_candidate(_analysis(0), FUND, 50, params, "neutral", _signal("B1"), index_regime="down")
    b3_down = score_candidate(_analysis(0), FUND, 50, params, "neutral", _signal("B3"), index_regime="down")
    assert b1_down["pool"] == "main" and b3_down["pool"] == "watch"
    params["regime_block"] = []
    assert score_candidate(_analysis(0), FUND, 50, params, "neutral", _signal("B1"), index_regime="up")["pool"] == "main"
