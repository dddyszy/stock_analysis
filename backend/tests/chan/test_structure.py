from app.chan import analyze
from app.chan.kline import merge_inclusion
from app.chan.types import DOWN, UP
from tests.chan.helpers import zigzag


def test_merge_inclusion_up_and_down():
    highs = [10, 12, 11.5, 13, 12, 11, 11.2]
    lows = [8, 9, 9.5, 10, 9, 8, 7.5]
    merged = merge_inclusion(highs, lows)
    # 第 3 根被第 2 根包含，向上合并取高高低高
    assert (merged[1].high, merged[1].low) == (12, 9.5)
    assert merged[1].start == 1 and merged[1].end == 2
    # 最后一根包含前一根，向下合并取低低高低
    assert (merged[-1].high, merged[-1].low) == (11, 7.5)
    assert merged[-1].low_raw == 6


def test_fractals_and_bis_on_zigzag():
    pivots = [10, 20, 15, 25, 18, 30]
    r = analyze(zigzag(pivots))
    assert [f.kind for f in r.fractals] == ["top", "bottom", "top", "bottom"]
    # 首尾两个拐点不形成分型，中间 4 个分型连成 3 笔
    assert len(r.bis) == 3
    assert [b.direction for b in r.bis] == [DOWN, UP, DOWN]
    assert r.bis[0].start.price == 20.2 and r.bis[0].end.price == 14.8
    assert not r.bis[-1].confirmed and r.bis[0].confirmed


def test_short_wiggle_is_not_a_bi():
    # 20 → 19 只有 2 根 K 线的小回调，不满足新笔的最小间隔
    pivots = [10, 20, 19, 26, 16, 24]
    r = analyze(zigzag(pivots, [6, 2, 6, 6, 6]))
    tops = [b.end.price for b in r.bis if b.direction == UP]
    assert 20.2 not in [b.start.price for b in r.bis]
    assert all(t > 20 for t in tops)


def test_zhongshu_and_third_buy():
    pivots = [5, 10, 20, 14, 18, 15, 25, 21, 30, 26]
    r = analyze(zigzag(pivots))
    zs = r.bi_zhongshus[0]
    assert zs.zg == 18.2 and zs.zd == 14.8
    b3 = [s for s in r.signals if s.type == "B3"]
    assert b3, [s.type for s in r.signals]
    assert b3[0].price == 20.8
    assert b3[0].stop_price == zs.zg
    assert b3[0].target1 > b3[0].price


def test_segments_up_then_down():
    pivots = [5, 10, 20, 15, 25, 20, 30, 22, 27, 18, 24, 12, 16, 8, 14, 11]
    r = analyze(zigzag(pivots))
    assert len(r.segments) >= 2
    up = r.segments[0]
    assert up.direction == UP and up.confirmed
    assert up.end_price == 30.2
    assert r.segments[1].direction == DOWN
    assert r.segments[1].start_price == 30.2
