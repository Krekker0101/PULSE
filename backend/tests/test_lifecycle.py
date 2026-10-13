import pytest
from app.domain.lifecycle import transition


def test_publication_requires_confirmed_state():
    assert transition('PLATFORM_PROCESSING','PUBLISHED')=='PUBLISHED'
    with pytest.raises(ValueError):transition('FAILED','PUBLISHED')
    with pytest.raises(ValueError):transition('WAITING_APPROVAL','PUBLISHED')
    with pytest.raises(ValueError):transition('PUBLISHED','SCHEDULED')
