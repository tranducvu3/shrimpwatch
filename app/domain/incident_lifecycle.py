"""Vong doi su co: Open -> Investigating -> Resolving -> Recovered."""

from app.domain.models import IncidentState, Severity

# Su co "dang mo": worker cap nhat su co nay thay vi mo su co moi, va tu dong chuyen sang Recovered khi ao on dinh.
# Resolving van la dang mo: ky thuat vien da xu ly nhung chi so lieu moi xac nhan duoc hoi phuc.
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
    """Giu nguyen trang thai (chi ghi them hanh dong) luon hop le; Recovered la trang thai cuoi."""
    try:
        state = IncidentState(current)
    except ValueError:
        return False
    return target == state or target in _NEXT[state]


_SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}


def is_escalation(current: str, new: Severity) -> bool:
    """Muc moi nghiem trong hon muc hien tai? Su co chi nang muc, khong ha; muc la coi nhu thap nhat."""
    try:
        current_rank = _SEVERITY_RANK[Severity(current)]
    except ValueError:
        current_rank = -1
    return _SEVERITY_RANK[new] > current_rank


def ensure_transition(current: str, target: IncidentState) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(f"cannot move incident from '{current}' to '{target.value}'")
