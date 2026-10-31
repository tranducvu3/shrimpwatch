import pytest

from app.domain.incident_lifecycle import InvalidTransition, can_transition, ensure_transition
from app.domain.models import IncidentState as S


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("open", S.INVESTIGATING),
        ("investigating", S.RESOLVING),
        ("resolving", S.RECOVERED),
        ("open", S.RECOVERED),
        ("resolving", S.INVESTIGATING),
        ("investigating", S.INVESTIGATING),  # chi ghi them hanh dong
    ],
)
def test_allowed_transitions(current, target):
    assert can_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [("recovered", S.OPEN), ("recovered", S.INVESTIGATING), ("investigating", S.OPEN), ("bogus", S.RECOVERED)],
)
def test_rejected_transitions(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransition):
        ensure_transition(current, target)
