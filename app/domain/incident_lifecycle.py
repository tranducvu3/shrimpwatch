"""Vong doi su co: Open -> Investigating -> Resolving -> Recovered."""

from app.domain.models import IncidentState

# Su co "dang mo": worker cap nhat su co nay thay vi mo su co moi.
ACTIVE_STATES = (IncidentState.OPEN, IncidentState.INVESTIGATING)

_NEXT: dict[IncidentState, set[IncidentState]] = {
    IncidentState.OPEN: {IncidentState.INVESTIGATING, IncidentState.RESOLVING, IncidentState.RECOVERED},
    IncidentState.INVESTIGATING: {IncidentState.RESOLVING, IncidentState.RECOVERED},
    IncidentState.RESOLVING: {IncidentState.INVESTIGATING, IncidentState.RECOVERED},
    IncidentState.RECOVERED: set(),
}


class InvalidTransition(ValueError):
    pass


def can_transition(current: str, target: IncidentState) -> bool:
    """Giu nguyen trang thai (chi ghi them hanh dong) luon hop le; Recovered la trang thai cuoi."""
    try:
        state = IncidentState(current)
    except ValueError:
        return False
    return target == state or target in _NEXT[state]


def ensure_transition(current: str, target: IncidentState) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(f"cannot move incident from '{current}' to '{target.value}'")
