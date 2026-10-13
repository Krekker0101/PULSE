"""Pure, three-valued filter expression evaluator; no infrastructure dependencies."""
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Result(Enum):
    TRUE = 'match'
    FALSE = 'no_match'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class Evaluation:
    result: Result
    reason: str


FIELDS = {'views', 'reactions', 'replies', 'duration', 'age_hours', 'viral_score', 'width', 'height'}
OPS = {'>=', '>', '<=', '<', '==', '!=', 'BETWEEN', 'IN', 'IS_UNKNOWN'}


def evaluate(node: dict, values: dict[str, Any], depth: int = 0) -> Evaluation:
    if depth > 12:
        raise ValueError('Too many nested conditions')
    if not isinstance(node, dict):
        raise ValueError('Expression node must be object')
    op = node.get('op')
    if op in ('AND', 'OR'):
        children = node.get('children')
        if not isinstance(children, list) or not 1 <= len(children) <= 30:
            raise ValueError('Group needs 1–30 children')
        outcomes = [evaluate(child, values, depth + 1) for child in children]
        if op == 'AND':
            result = Result.FALSE if any(x.result is Result.FALSE for x in outcomes) else (Result.UNKNOWN if any(x.result is Result.UNKNOWN for x in outcomes) else Result.TRUE)
        else:
            result = Result.TRUE if any(x.result is Result.TRUE for x in outcomes) else (Result.UNKNOWN if any(x.result is Result.UNKNOWN for x in outcomes) else Result.FALSE)
        return Evaluation(result, f'{op}: ' + '; '.join(x.reason for x in outcomes))
    if op == 'NOT':
        item = evaluate(node.get('child'), values, depth + 1)
        return Evaluation({Result.TRUE: Result.FALSE, Result.FALSE: Result.TRUE, Result.UNKNOWN: Result.UNKNOWN}[item.result], 'NOT '+item.reason)
    if op not in OPS or node.get('field') not in FIELDS:
        raise ValueError('Unsupported field or comparison')
    field = node['field']
    x = values.get(field)
    if op == 'IS_UNKNOWN':
        return Evaluation(Result.TRUE if x is None else Result.FALSE, f'{field} is unknown: {x is None}')
    if x is None:
        return Evaluation(Result.UNKNOWN, f'{field}: not available')
    y = node.get('value')
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError(f'{field} must be numeric')
    try:
        if op == 'BETWEEN':
            valid = isinstance(y, list) and len(y) == 2 and all(type(v) in (int, float) for v in y)
            if not valid: raise ValueError('BETWEEN requires numeric [min,max]')
            ok = y[0] <= x <= y[1]
        elif op == 'IN':
            if not isinstance(y, list) or not 1 <= len(y) <= 100 or not all(type(v) in (int, float) for v in y):
                raise ValueError('IN requires numeric array')
            ok = x in y
        else:
            if type(y) not in (int, float): raise ValueError('Comparison requires numeric value')
            ok = {'>=': lambda: x >= y, '>': lambda: x > y, '<=': lambda: x <= y, '<': lambda: x < y, '==': lambda: x == y, '!=': lambda: x != y}[op]()
    except (TypeError, OverflowError) as e:
        raise ValueError('Invalid comparison') from e
    return Evaluation(Result.TRUE if ok else Result.FALSE, f'{field} {op} {y}: {x}')


def defaults(source, video):
    checks = []
    for field, limit in [('views', source.min_views), ('reactions', source.min_reactions), ('replies', source.min_replies), ('duration', source.min_duration)]:
        if limit is not None:
            checks.append({'op': '>=', 'field': field, 'value': limit})
    if source.max_duration is not None:
        checks.append({'op': '<=', 'field': 'duration', 'value': source.max_duration})
    if source.max_age_hours is not None:
        checks.append({'op': '<=', 'field': 'age_hours', 'value': source.max_age_hours})
    if source.format_mode == 'SHORT':
        checks.append({'op': '<=', 'field': 'duration', 'value': 180})
    elif source.format_mode == 'LONG':
        checks.append({'op': '>=', 'field': 'duration', 'value': 180.00001})
    if source.filter_tree:
        checks.append(source.filter_tree)
    return {'op': 'AND', 'children': checks} if checks else None


def values_for(video, now=None):
    now = now or datetime.now(timezone.utc)
    published = video.published_at
    if published is not None and published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return {'views': video.views, 'reactions': video.reactions, 'replies': video.replies,
            'duration': video.duration, 'width': video.width, 'height': video.height,
            'viral_score': video.viral_score,
            'age_hours': max(0, (now - published).total_seconds()/3600) if published else None}


def qualify(source, video, unknown_policy='review'):
    if unknown_policy not in ('review', 'exclude', 'skip'):
        raise ValueError('Invalid unknown metric policy')
    expression = defaults(source, video)
    if expression is None:
        return Evaluation(Result.TRUE, 'No filters set')
    result = evaluate(expression, values_for(video))
    if result.result is Result.UNKNOWN and unknown_policy == 'exclude':
        return Evaluation(Result.FALSE, result.reason + ' (excluded)')
    if result.result is Result.UNKNOWN and unknown_policy == 'skip':
        return Evaluation(Result.TRUE, result.reason + ' (allowed missing metric)')
    return result
