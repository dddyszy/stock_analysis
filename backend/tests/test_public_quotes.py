from datetime import date

from app.providers.tencent_public import _parse_kline, _parse_quotes

QT_SAMPLE = (
    'v_sh600519="1~贵州茅台~600519~1237.00~1251.24~1250.01~31239~13918~17321~1237.00~13~1236.95~4~1236.85~1~1236.83~1~'
    "1236.51~2~1237.05~1~1237.50~1~1237.70~1~1237.90~1~1237.97~1~~20260924153914~-14.24~-1.14~1256.13~1231.05~"
    "1237.00/31239/3867310920~31239~386731~0.25~18.99~~1256.13~1231.05~2.00~15463.51~15463.51~6.15~1376.36~1126.12~"
    '1.27~16~1237.96~17.37~18.78~~~0.07~386731.0920~74.2200~6~   A~GP-A~-8.31";'
)


def test_parse_quotes_fields():
    q = _parse_quotes(QT_SAMPLE)["sh600519"]
    assert q.name == "贵州茅台"
    assert (q.price, q.prev_close, q.open, q.high, q.low) == (1237.0, 1251.24, 1250.01, 1256.13, 1231.05)
    assert q.change_pct == -1.14 and q.pe_ttm == 18.99 and q.pb == 6.15
    assert q.dt == date(2026, 9, 24)
    assert abs(q.total_mv - 15463.51e8) < 1


def test_parse_kline_qfq_and_plain():
    doc = {"code": 0, "data": {"sh600519": {"qfqday": [["2026-09-23", "1255.03", "1251.24", "1271.50", "1250.89", "30981.00"]]}}}
    b = _parse_kline(doc, "sh600519", "day", "qfq")[0]
    assert (b.open, b.close, b.high, b.low) == (1255.03, 1251.24, 1271.5, 1250.89)
    doc = {"code": 0, "data": {"sh000001": {"day": [["2026-09-24", "3925.32", "3888.37", "3930.50", "3888.37", "438530412"]]}}}
    assert _parse_kline(doc, "sh000001", "day", "")[0].close == 3888.37
