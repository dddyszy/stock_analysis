"""容错解析工具。westock 的返回字段以 probe 样本为准，这里按常见别名匹配，
拿到样本后只需在别名表里补充字段名即可。"""

import re
from datetime import date, datetime
from typing import Any, Iterable

_LIST_KEYS = (
    "list", "items", "records", "rows", "data", "klines", "kline", "qfqday", "day", "week",
    "stocks", "result", "results", "constituents", "sectors", "tips", "positions", "orders",
)


def _norm(key: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", str(key).lower())


def pick(d: dict, *aliases: str, default: Any = None) -> Any:
    if not isinstance(d, dict):
        return default
    normalized = {_norm(k): v for k, v in d.items()}
    for alias in aliases:
        v = normalized.get(_norm(alias))
        if v not in (None, "", "--", "-"):
            return v
    return default


def pick_contains(d: dict, *fragments: str, default: Any = None) -> Any:
    """字段名包含某个片段即可命中，用于中文财务科目这种命名不固定的场景。"""
    if not isinstance(d, dict):
        return default
    for frag in fragments:
        nf = _norm(frag)
        for k, v in d.items():
            if nf in _norm(k) and v not in (None, "", "--", "-"):
                return v
    return default


def to_float(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if s in ("", "--", "-", "None", "null", "NaN"):
        return None
    mult = 1.0
    if s.endswith("%"):
        s = s[:-1]
    for suffix, m in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4)):
        if s.endswith(suffix):
            s, mult = s[: -len(suffix)], m
            break
    try:
        return float(s) * mult
    except ValueError:
        return None


def to_int(v: Any) -> int | None:
    f = to_float(v)
    return int(f) if f is not None else None


def to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):
        n = int(v)
        if 19000101 <= n <= 21001231:
            return date(n // 10000, n // 100 % 100, n % 100)
        if n > 10**12:
            n //= 1000
        if n > 10**8:
            return datetime.fromtimestamp(n).date()
        return None
    s = str(v).strip()
    m = re.match(r"^(\d{4})[-/]?(\d{1,2})[-/]?(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def normalize_code(code: Any) -> str | None:
    if code is None:
        return None
    s = str(code).strip()
    if not s:
        return None
    low = s.lower()
    m = re.match(r"^(sh|sz|bj|hk|us)(.+)$", low)
    if m:
        return m.group(1) + m.group(2).upper() if m.group(1) == "us" else low
    m = re.match(r"^(\d{6})\.(sh|sz|bj|ss)$", low)
    if m:
        ex = "sh" if m.group(2) == "ss" else m.group(2)
        return ex + m.group(1)
    if re.fullmatch(r"\d{6}", s):
        if s[0] in "69" or s.startswith("5"):
            return "sh" + s
        if s[0] in "48" or s.startswith("92"):
            return "bj" + s
        return "sz" + s
    return low


def exchange_of(code: str) -> str:
    return code[:2]


def find_records(obj: Any, prefer: Iterable[str] = ()) -> list:
    """在嵌套结构里找第一段非空列表（元素为 dict 或 list）。"""
    keys = tuple(prefer) + _LIST_KEYS

    def _walk(o: Any, depth: int) -> list | None:
        if depth > 5:
            return None
        if isinstance(o, list):
            if o and isinstance(o[0], (dict, list)):
                return o
            return None
        if isinstance(o, dict):
            normalized = {_norm(k): k for k in o}
            for k in keys:
                real = normalized.get(_norm(k))
                if real is not None:
                    found = _walk(o[real], depth + 1)
                    if found:
                        return found
            for v in o.values():
                found = _walk(v, depth + 1)
                if found:
                    return found
        return None

    return _walk(obj, 0) or []


def records_by_code(obj: Any) -> dict[str, dict]:
    """批量接口可能返回 {code: {...}} 或 [{code: ..}, ...]，统一成 {code: dict}。"""
    out: dict[str, dict] = {}
    if isinstance(obj, dict):
        direct = {k: v for k, v in obj.items() if isinstance(v, dict) and normalize_code(k) and re.search(r"\d", k)}
        if direct and len(direct) >= max(1, len(obj) // 2):
            for k, v in direct.items():
                out[normalize_code(k)] = v
            return out
        if pick(obj, "code", "symbol", "stock_code"):
            return {normalize_code(pick(obj, "code", "symbol", "stock_code")): obj}
    for rec in find_records(obj):
        if isinstance(rec, dict):
            code = normalize_code(pick(rec, "code", "symbol", "stock_code", "secu_code", "stockcode"))
            if code:
                out[code] = rec
    return out
