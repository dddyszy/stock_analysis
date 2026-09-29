"""多策略：每个策略提供单只股票判定和每日筛选；缠论的判定见 analysis/structure.py 与推荐页。"""

STRATEGY_NAMES = {"chan": "缠论", "dianjin_v1": "点金术 · 原版", "dianjin_v2": "点金术 · 改良版"}


def strategy_name(key: str | None) -> str | None:
    return STRATEGY_NAMES.get(key or "", key) if key else None
