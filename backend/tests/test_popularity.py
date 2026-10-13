from app.domain.popularity import viral_score


def test_no_fabricated_score():
    assert viral_score(None)[0] is None
    assert viral_score(20)[0] is None


def test_relative_popularity():
    a,_=viral_score(100_000,5000,100,[2_000_000]*10)
    b,_=viral_score(30_000,5000,100,[5_000]*10)
    assert b>a
    assert 0<=a<=100 and 0<=b<=100
