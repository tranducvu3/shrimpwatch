import pytest

from app.domain.incident_lifecycle import (
    ACTIVE_STATES,
    InvalidTransition,
    can_transition,
    ensure_transition,
    is_escalation,
)
from app.domain.models import IncidentState as S
from app.domain.models import Severity


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


@pytest.mark.parametrize(
    ("current", "new", "expected"),
    [
        ("warning", Severity.CRITICAL, True),
        ("info", Severity.WARNING, True),
        ("critical", Severity.WARNING, False),
        ("warning", Severity.WARNING, False),
        ("bogus", Severity.INFO, True),
    ],
)
def test_incident_only_escalates(current, new, expected):
    assert is_escalation(current, new) is expected


def test_resolving_is_active_so_worker_keeps_tracking_it():
    assert S.RESOLVING in ACTIVE_STATES
    assert S.RECOVERED not in ACTIVE_STATES
