"""基本面：硬过滤 + 0~100 打分。缺数据的项给中性分，避免因数据缺失误杀。"""

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.models import FundamentalQuarterly, KlineDaily, StockBasic, StockRiskLabel, ValuationSnapshot
from app.db.session import session_scope
from app.services.strategy_config import get_active_params

FINANCIAL_INDUSTRIES = {"银行", "非银金融"}


@dataclass
class FundamentalView:
    code: str
    passed: bool
    score: float
    reasons: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "passed": self.passed,
            "score": round(self.score, 1),
            "reasons": self.reasons,
            "highlights": self.highlights,
            "metrics": self.metrics,
        }


def _scale(v: float | None, lo: float, hi: float, points: float) -> float:
    if v is None:
        return points / 2
    if v <= lo:
        return 0.0
    if v >= hi:
        return points
    return (v - lo) / (hi - lo) * points


def _percentile_rank(values: list[float], v: float) -> float:
    if not values:
        return 0.5
    below = sum(1 for x in values if x < v)
    return below / len(values)


def load_views(codes: list[str] | None = None, today: date | None = None) -> dict[str, FundamentalView]:
    today = today or date.today()
    with session_scope() as db:
        q = select(StockBasic).where(StockBasic.is_index.is_(False), StockBasic.active.is_(True))
        if codes:
            q = q.where(StockBasic.code.in_(codes))
        basics = {s.code: s for s in db.execute(q).scalars()}
        fq = select(FundamentalQuarterly).where(FundamentalQuarterly.report_date >= today - timedelta(days=800))
        if codes:
            fq = fq.where(FundamentalQuarterly.code.in_(codes))
        fund: dict[str, list[FundamentalQuarterly]] = {}
        for r in db.execute(fq.order_by(FundamentalQuarterly.report_date)).scalars():
            fund.setdefault(r.code, []).append(r)
        latest_val_date = db.scalar(select(func.max(ValuationSnapshot.trade_date)))
        vals: dict[str, ValuationSnapshot] = {}
        if latest_val_date:
            vq = select(ValuationSnapshot).where(ValuationSnapshot.trade_date >= latest_val_date - timedelta(days=10))
            for v in db.execute(vq.order_by(ValuationSnapshot.trade_date)).scalars():
                prev = vals.get(v.code)
                if prev is None:
                    vals[v.code] = v
                else:
                    for attr in ("pe_ttm", "pb", "total_mv", "float_mv", "comp_score", "funm_score", "risk_score", "dividend_yield", "roe_ttm"):
                        if getattr(v, attr) is not None:
                            setattr(prev, attr, getattr(v, attr))
        first_bar = dict(
            db.execute(select(KlineDaily.code, func.min(KlineDaily.trade_date)).group_by(KlineDaily.code)).all()
        )
        last_day = db.scalar(select(func.max(KlineDaily.trade_date)))
        last_close = dict(db.execute(select(KlineDaily.code, KlineDaily.close).where(KlineDaily.trade_date == last_day)).all()) if last_day else {}
        rq = select(StockRiskLabel)
        if codes:
            rq = rq.where(StockRiskLabel.code.in_(codes))
        risk_labels: dict[str, list[StockRiskLabel]] = {}
        for r in db.execute(rq).scalars():
            risk_labels.setdefault(r.code, []).append(r)
    penalty_each = float(get_active_params().get("risk_label_penalty", 8))

    # 行业内估值分位
    pe_by_ind: dict[str, list[float]] = {}
    pb_by_ind: dict[str, list[float]] = {}
    for code, s in basics.items():
        v = vals.get(code)
        if v and s.industry:
            if v.pe_ttm and v.pe_ttm > 0:
                pe_by_ind.setdefault(s.industry, []).append(v.pe_ttm)
            if v.pb and v.pb > 0:
                pb_by_ind.setdefault(s.industry, []).append(v.pb)

    out: dict[str, FundamentalView] = {}
    for code, s in basics.items():
        recs = fund.get(code, [])
        v = vals.get(code)
        reasons: list[str] = []
        highlights: list[str] = []
        latest = recs[-1] if recs else None

        if s.is_st:
            reasons.append("ST 股票")
        listed = s.list_date or first_bar.get(code)
        if listed and listed > today - timedelta(days=365):
            reasons.append("上市不满一年")
        profits = [r.net_profit for r in recs if r.net_profit is not None]
        if len(profits) >= 2 and profits[-1] < 0 and profits[-2] < 0:
            reasons.append("净利润连续亏损")
        if latest and latest.goodwill and latest.total_equity and latest.total_equity > 0:
            if latest.goodwill / latest.total_equity > 0.5:
                reasons.append("商誉超过净资产的 50%")
        if latest and latest.debt_ratio and latest.debt_ratio > 85 and s.industry not in FINANCIAL_INDUSTRIES:
            reasons.append(f"资产负债率 {latest.debt_ratio:.0f}% 过高")
        if latest and latest.total_equity is not None and latest.total_equity <= 0:
            reasons.append("净资产为负")
        funm = v.funm_score if v else None
        risk = v.risk_score if v else None
        if risk is not None and risk < 20:
            reasons.append(f"腾讯风险评分 {risk:.0f}，风险过高")

        # 退市新规红线：财务类（营收低于 3 亿且亏损）、市值类（低于 5 亿）、面值类（低于 1 元）
        annual = [r for r in recs if r.report_date.month == 12]
        if annual and annual[-1].revenue is not None and annual[-1].net_profit is not None:
            if annual[-1].revenue < 3e8 and annual[-1].net_profit < 0:
                reasons.append(f"触及财务类退市指标：{annual[-1].report_date.year} 年营收低于 3 亿元且亏损")
        if v and v.total_mv and v.total_mv < 5e8:
            reasons.append("总市值低于 5 亿元（市值类退市风险）")
        close = last_close.get(code)
        if close is not None and close < 1:
            reasons.append("股价低于 1 元（面值退市风险）")

        labels = risk_labels.get(code, [])
        seen_names: set[str] = set()
        penalties: list[str] = []
        for lab in labels:
            if lab.name in seen_names:
                continue
            seen_names.add(lab.name)
            if lab.severity == "hard":
                if lab.label != "risk_st" or not s.is_st:
                    reasons.append(f"风险：{lab.name}")
            else:
                penalties.append(lab.name)

        roe = latest.roe if latest else None
        rev_yoy = latest.revenue_yoy if latest else None
        profit_yoy = latest.profit_yoy if latest else None
        margins = [r.gross_margin for r in recs[-4:] if r.gross_margin is not None]
        margin_std = statistics.pstdev(margins) if len(margins) >= 3 else None
        cf_ratio = None
        if latest and latest.op_cashflow is not None and latest.net_profit and latest.net_profit > 0:
            cf_ratio = latest.op_cashflow / latest.net_profit

        pe_pct = None
        if v and v.pe_ttm and v.pe_ttm > 0 and s.industry in pe_by_ind:
            pe_pct = _percentile_rank(pe_by_ind[s.industry], v.pe_ttm)
        if recs:
            source = "financials"
            score = 0.0
            score += _scale(roe, 0, 20, 25)
            score += _scale(rev_yoy, -10, 30, 15)
            score += _scale(profit_yoy, -20, 40, 15)
            score += 10 - _scale(margin_std, 1, 8, 10) if margin_std is not None else 5
            score += _scale(cf_ratio, 0, 1.2, 15)
            if pe_pct is not None:
                score += (1 - pe_pct) * 10
            else:
                score += 3 if v and v.pe_ttm is not None and v.pe_ttm <= 0 else 5
            score += _scale(funm if funm is not None else (v.comp_score if v else None), 30, 90, 15)
        else:
            # 没有拉取精细财报的股票，用腾讯诊股评分近似，估值分位作小幅修正
            source = "tencent_score"
            parts = [(funm, 0.6), (v.comp_score if v else None, 0.2), (risk, 0.2)]
            known = [(x, w) for x, w in parts if x is not None]
            score = sum(x * w for x, w in known) / sum(w for _, w in known) if known else 50.0
            if pe_pct is not None:
                score += (0.5 - pe_pct) * 10
        score -= penalty_each * len(penalties)
        score = max(0.0, min(100.0, score))

        if roe is not None and roe >= 15:
            highlights.append(f"ROE {roe:.1f}%")
        if profit_yoy is not None and profit_yoy >= 20:
            highlights.append(f"净利润同比 +{profit_yoy:.0f}%")
        if rev_yoy is not None and rev_yoy >= 20:
            highlights.append(f"营收同比 +{rev_yoy:.0f}%")
        if cf_ratio is not None and cf_ratio >= 1:
            highlights.append("经营现金流覆盖净利润")
        if pe_pct is not None and pe_pct <= 0.3:
            highlights.append("估值处于行业低位")
        if funm is not None and funm >= 75:
            highlights.append(f"腾讯基本面评分 {funm:.0f}")

        out[code] = FundamentalView(
            code=code,
            passed=not reasons,
            score=score,
            reasons=reasons,
            highlights=highlights,
            metrics={
                "report_date": latest.report_date.isoformat() if latest else None,
                "roe": _r(roe),
                "revenue_yoy": _r(rev_yoy),
                "profit_yoy": _r(profit_yoy),
                "gross_margin": _r(latest.gross_margin if latest else None),
                "gross_margin_std": _r(margin_std),
                "debt_ratio": _r(latest.debt_ratio if latest else None),
                "cashflow_ratio": _r(cf_ratio),
                "pe_ttm": _r(v.pe_ttm if v else None),
                "pb": _r(v.pb if v else None),
                "pe_industry_pct": _r(pe_pct * 100 if pe_pct is not None else None),
                "total_mv": v.total_mv if v else None,
                "comp_score": _r(v.comp_score if v else None),
                "float_mv": v.float_mv if v else None,
                "risk_labels": penalties,
                "funm_score": _r(funm),
                "risk_score": _r(risk),
                "source": source,
                "industry": s.industry,
                "list_date": listed.isoformat() if listed else None,
            },
        )
    return out


def _r(v: float | None, n: int = 2) -> float | None:
    return round(v, n) if v is not None else None
