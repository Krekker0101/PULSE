import pytest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from app.domain.filters import evaluate, Result, qualify


def test_three_valued_and_or_not():
    yes={'op':'>=','field':'views','value':100}
    no={'op':'<','field':'views','value':100}
    unknown={'op':'>=','field':'replies','value':10}
    values={'views':150,'replies':None}
    assert evaluate({'op':'AND','children':[yes,unknown]},values).result is Result.UNKNOWN
    assert evaluate({'op':'AND','children':[no,unknown]},values).result is Result.FALSE
    assert evaluate({'op':'OR','children':[yes,unknown]},values).result is Result.TRUE
    assert evaluate({'op':'NOT','child':unknown},values).result is Result.UNKNOWN
    assert evaluate({'op':'IS_UNKNOWN','field':'replies'},values).result is Result.TRUE


def test_nested_rule_and_bounds():
    n={'op':'AND','children':[{'op':'BETWEEN','field':'duration','value':[10,80]},
       {'op':'OR','children':[{'op':'>=','field':'views','value':50000},
                              {'op':'>=','field':'reactions','value':100}]}]}
    assert evaluate(n,{'duration':55,'views':None,'reactions':140}).result is Result.TRUE
    with pytest.raises(ValueError):evaluate({'op':'>=','field':'__class__','value':2}, {})
    with pytest.raises(ValueError):evaluate({'op':'AND','children':[]}, {})


def test_qualify_missing_and_age():
    s=SimpleNamespace(min_views=50000,min_reactions=None,min_replies=None,
                      min_duration=10,max_duration=90,max_age_hours=48,format_mode='SHORT',filter_tree=None)
    v=SimpleNamespace(views=None,reactions=None,replies=None,duration=30,
                      width=1080,height=1920,viral_score=None,
                      published_at=datetime.now(timezone.utc)-timedelta(hours=10))
    assert qualify(s,v).result is Result.UNKNOWN
    assert qualify(s,v,'exclude').result is Result.FALSE
    v.views=75000
    assert qualify(s,v).result is Result.TRUE
    v.duration=300
    assert qualify(s,v).result is Result.FALSE


def test_bad_values_rejected():
    with pytest.raises(ValueError):evaluate({'op':'>=','field':'views','value':'100'}, {'views':200})
    with pytest.raises(ValueError):evaluate({'op':'BETWEEN','field':'views','value':[1]}, {'views':200})
