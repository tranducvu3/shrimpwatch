"""Incident lifecycle: Open -> Investigating -> Resolving -> Recovered."""

from app.domain.models import IncidentState, Severity

# "Active" incidents: the worker updates this incident instead of opening a new one, and moves it to Recovered once the pond is stable.
# Resolving is still active: the technician has acted, but only new data can confirm recovery.
ACTIVE_STATES = (IncidentState.OPEN, IncidentState.INVESTIGATING, IncidentState.RESOLVING)

_NEXT: dict[IncidentState, set[IncidentState]] = {
    IncidentState.OPEN: {IncidentState.INVESTIGATING, IncidentState.RESOLVING, IncidentState.RECOVERED},
    IncidentState.INVESTIGATING: {IncidentState.RESOLVING, IncidentState.RECOVERED},
    IncidentState.RESOLVING: {IncidentState.INVESTIGATING, IncidentState.RECOVERED},
    IncidentState.RECOVERED: set(),
}


class InvalidTransition(ValueError):
    pass


def can_transition(current: str, target: IncidentState) -> bool:
    """Staying in the same state (only adding an action note) is always valid; Recovered is final."""
    try:
        state = IncidentState(current)
    except ValueError:
        return False
    return target == state or target in _NEXT[state]


_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


def is_escalation(current: str, new: Severity) -> bool:
    """Is the new severity higher than the current one? Incidents only escalate, never downgrade; an unknown severity counts as the lowest."""
    try:
        current_rank = _SEVERITY_RANK[Severity(current)]
    except ValueError:
        current_rank = -1
    return _SEVERITY_RANK[new] > current_rank


def ensure_transition(current: str, target: IncidentState) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(f"cannot move incident from '{current}' to '{target.value}'")
