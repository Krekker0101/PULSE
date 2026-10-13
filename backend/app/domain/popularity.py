"""Explainable heuristic; NULL input is omitted, never interpreted as zero."""
from math import log1p
from statistics import median


def viral_score(views, reactions=None, replies=None, history=None, growth_per_hour=None):
    if views is None:
        return None, ['Просмотры недоступны: оценка не рассчитана']
    history = [v for v in (history or []) if v is not None and v >= 0]
    baseline = median(history) if history else None
    reasons = []
    components = []
    if baseline is not None:
        relative = max(0, min(1, (views / max(1, baseline)) / 5))
        components.append((relative, .45))
        reasons.append(f'Просмотры относительно медианы канала: {views/max(1,baseline):.1f}×')
    else:
        reasons.append('Нет исторической медианы канала')
    if views > 0 and (reactions is not None or replies is not None):
        ratio = ((reactions or 0) + (replies or 0)*2) / views
        components.append((min(1, ratio / .08), .35))
        reasons.append(f'Измеренная вовлечённость: {ratio*100:.2f}%')
    if growth_per_hour is not None and growth_per_hour >= 0:
        rate = growth_per_hour / max(1, views)
        components.append((min(1, rate/.30), .20))
        reasons.append(f'Рост просмотров: {growth_per_hour:.0f}/ч')
    if not components:
        return None, reasons + ['Недостаточно сопоставимых данных']
    return round(100*sum(v*w for v,w in components)/sum(w for _,w in components), 1), reasons
