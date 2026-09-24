"""A 股交易规则的唯一出处：涨跌停幅度、涨跌停价、一字板判断。

- 主板 10%，主板 ST 5%；创业板（300/301）、科创板（688/689）20%（含 ST）；北交所 30%。
- 涨跌停价 = 昨收 ×（1 ± 幅度），四舍五入到分。
- 回测用的是前复权价格，和真实成交价有细微差异，所以判断时留 0.1% 的容差。
"""

from decimal import ROUND_HALF_UP, Decimal

TOLERANCE = 0.001


def board(code: str) -> str:
    num = code[2:] if code[:2] in ("sh", "sz", "bj") else code
    if code.startswith("bj") or num[:1] in ("4", "8") or num.startswith("92"):
        return "bj"
    if num.startswith(("300", "301")):
        return "gem"
    if num.startswith(("688", "689")):
        return "star"
    return "main"


def volume_lot(code: str) -> int:
    """腾讯日线成交量每单位对应的股数：科创板按股计，其余按手（100 股）计。"""
    return 1 if board(code) == "star" else 100


def limit_pct(code: str, is_st: bool = False) -> float:
    b = board(code)
    if b in ("gem", "star"):
        return 0.20
    if b == "bj":
        return 0.30
    return 0.05 if is_st else 0.10


def _round_cent(v: float) -> float:
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def limit_prices(prev_close: float, code: str, is_st: bool = False) -> tuple[float, float]:
    pct = limit_pct(code, is_st)
    return _round_cent(prev_close * (1 + pct)), _round_cent(prev_close * (1 - pct))


def is_limit_up(prev_close: float | None, price: float | None, code: str, is_st: bool = False) -> bool:
    if not prev_close or price is None:
        return False
    up, _ = limit_prices(prev_close, code, is_st)
    return price >= up * (1 - TOLERANCE)


def is_limit_down(prev_close: float | None, price: float | None, code: str, is_st: bool = False) -> bool:
    if not prev_close or price is None:
        return False
    _, down = limit_prices(prev_close, code, is_st)
    return price <= down * (1 + TOLERANCE)


def is_one_price_limit_up(bar, prev_close: float | None, code: str, is_st: bool = False) -> bool:
    """一字涨停：全天只有一个价格且在涨停价，买不进。"""
    return abs(bar.high - bar.low) < 1e-9 and is_limit_up(prev_close, bar.close, code, is_st)


def is_one_price_limit_down(bar, prev_close: float | None, code: str, is_st: bool = False) -> bool:
    """一字跌停：全天只有一个价格且在跌停价，卖不出。"""
    return abs(bar.high - bar.low) < 1e-9 and is_limit_down(prev_close, bar.close, code, is_st)


def clamp_to_limits(price: float, prev_close: float | None, code: str, is_st: bool = False) -> float:
    """把委托价限制在当日涨跌停价之内。"""
    if not prev_close:
        return round(price, 2)
    up, down = limit_prices(prev_close, code, is_st)
    return round(min(max(price, down), up), 2)
