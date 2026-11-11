"""Incident lifecycle. Every incident change (worker or API) goes through here: writes incident + event + metric in one place.

The incident and its `events` entry are written in one MongoDB transaction (`run_in_transaction`), so the timeline
never misses a change and never shows one that did not happen. Datadog metrics and events are sent after the commit.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pymongo.errors import DuplicateKeyError

from app.core.clock import utcnow
from app.core.config import settings
from app.core.datadog_links import incident_links
from app.core.log import get_logger
from app.core.telemetry import distribution, event, gauge, increment, set_span_tags, tag_value
from app.db.mongo import run_in_transaction
from app.db.repositories import events as events_repo
from app.db.repositories import incidents as incidents_repo
from app.db.repositories import metrics as metrics_repo
from app.domain.conditions import REMEDIES, Remedy
from app.domain.incident_lifecycle import ensure_transition, is_escalation
from app.domain.incident_rules import Finding, Thresholds
from app.domain.models import MANUAL_METRIC_UNITS, Incident, IncidentEvidence, IncidentState, MetricType
from app.domain.remedies import suggested_remedies
from app.domain.remedy_outcome import RemedyOutcome, remedy_outcome, watched_metric
from app.services import derivations
from app.services import ponds as pond_service

logger = get_logger("services.incidents")


class IncidentNotFound(LookupError):
    pass


_ALERT_TYPE = {"critical": "error", "warning": "warning"}


def _datadog_event(incident: dict, what: str, text: str, alert_type: str | None = None) -> None:
    """Mirror an incident change as a Datadog Event, grouped by incident id, so Datadog shows the same lifecycle."""
    severity = incident.get("severity", "")
    event(
        f"[ShrimpWatch] {incident['pond_id']}: {incident.get('title', '')} ({what})",
        text,
        alert_type=alert_type or _ALERT_TYPE.get(severity, "info"),
        aggregation_key=incident["_id"],
        tags={**_timing_tags(incident), "incident_id": incident["_id"], "incident_event": what},
    )


_SEVERITY_VI = {"critical": "🔴 nghiêm trọng", "warning": "🟠 cảnh báo", "info": "🔵 thông tin"}


def _alert_text(pond_id: str, finding: Finding, escalated_from: str | None = None) -> str:
    """Event text for an opened or escalated incident: how bad, what is wrong, what to do, where to look.

    The farmer alert email shows it under the monitor message, so it is written for the farmer (Vietnamese, no
    jargon). Datadog email takes no HTML or CSS, so the layout is Markdown, which `%%%` turns on for an event.
    """
    severity = _SEVERITY_VI.get(finding.severity.value, finding.severity.value)
    if escalated_from:
        blocks = [f"**Nâng mức:** {_SEVERITY_VI.get(escalated_from, escalated_from)} → {severity}"]
    else:
        blocks = [f"**Mức độ:** {severity}"]
    blocks.append(f"**Tình trạng:** {finding.description}")
    remedies = [REMEDIES[r].label for r in suggested_remedies(finding.root_cause)]
    if remedies:
        blocks.append("**Đề xuất xử lý:**\n" + "\n".join(f"- {label}" for label in remedies))
    if settings.PUBLIC_BASE_URL:
        url = f"{settings.PUBLIC_BASE_URL.rstrip('/')}/control?ao={pond_id}"
        blocks.append(f"**[👉 Mở bảng điều khiển ao {pond_id}]({url})**")
    return "%%%\n" + "\n\n".join(blocks) + "\n%%%"


def _seconds(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds(), 1)


def _timing_tags(incident: dict) -> dict:
    return {"pond": incident["pond_id"], "severity": incident.get("severity"), "root_cause": incident.get("root_cause")}


def _update_active(existing: dict, finding: Finding, evidence: IncidentEvidence) -> str:
    """An incident keeps the most severe cause seen so far: it only escalates, never downgrades.

    E.g. pump failure (critical), then the pump restarts while DO is still falling (warning): the incident stays pump_failure.
    """
    incident_id = existing["_id"]
    fields: dict = {"evidence": evidence.model_dump()}
    current = existing.get("severity")
    if is_escalation(current, finding.severity):
        fields |= {
            "severity": finding.severity.value,
            "title": finding.title,
            "description": finding.description,
            "root_cause": finding.root_cause,
        }
        details = {
            "from_severity": current,
            "to_severity": finding.severity.value,
            "from_root_cause": existing.get("root_cause"),
            "to_root_cause": finding.root_cause,
        }

        def escalate() -> None:
            events_repo.record("incident_escalated", existing["pond_id"], incident_id=incident_id, details=details)
            incidents_repo.update_fields(incident_id, fields)

        run_in_transaction(escalate)
        increment(
            "incident.escalated",
            tags={"pond": existing["pond_id"], "severity": finding.severity.value, "root_cause": finding.root_cause},
        )
        _datadog_event(
            {**existing, **fields},
            "incident_escalated",
            _alert_text(existing["pond_id"], finding, escalated_from=current),
        )
        return incident_id
    if finding.root_cause == existing.get("root_cause"):
        # Same cause: refresh the description with the latest data
        fields["description"] = finding.description
    incidents_repo.update_fields(incident_id, fields)
    return incident_id


def open_or_update(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str:
    """Update the pond's active incident; open a new one if there is none."""
    # Twice at most: if another writer opens the pond's incident between our read and insert, update that one
    for _ in range(2):
        existing = incidents_repo.find_active(pond_id)
        if existing:
            return _update_active(existing, finding, evidence)
        incident_id = _open(pond_id, finding, evidence)
        if incident_id:
            return incident_id
    raise RuntimeError(f"could not open or update an incident for {pond_id}")


def open_or_escalate(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str | None:
    """Open an incident, or escalate the active one; leave an equally or more severe active incident untouched.

    For the fast ingest check: it must not overwrite the worker's fuller evidence every batch, nor when it loses a
    race with the worker.
    """
    for _ in range(2):
        existing = incidents_repo.find_active(pond_id)
        if existing:
            return (
                _update_active(existing, finding, evidence)
                if is_escalation(existing.get("severity"), finding.severity)
                else None
            )
        incident_id = _open(pond_id, finding, evidence)
        if incident_id:
            return incident_id
    return None


def _open(pond_id: str, finding: Finding, evidence: IncidentEvidence) -> str | None:
    """Insert a new incident; None if the pond already has an active one (unique index one_active_incident_per_pond)."""
    incident_id = f"inc-{uuid.uuid4().hex[:10]}"
    now = utcnow()
    fault_started_at = _fault_start(pond_id, now)
    incident = Incident(
        id=incident_id,
        farm_id=settings.FARM_ID,
        pond_id=pond_id,
        severity=finding.severity,
        title=finding.title,
        description=finding.description,
        evidence=evidence,
        root_cause=finding.root_cause,
        created_at=now,
        updated_at=now,
        fault_started_at=fault_started_at,
        detection_delay_sec=_seconds(fault_started_at, now) if fault_started_at else None,
    )
    doc = incident.model_dump(by_alias=True)

    def insert() -> None:
        incidents_repo.insert(doc)
        events_repo.record(
            "incident_open",
            pond_id,
            incident_id=incident_id,
            details={"severity": finding.severity.value, "title": finding.title},
        )

    try:
        run_in_transaction(insert)
    except DuplicateKeyError:
        return None
    increment(
        "incident.open", tags={"pond": pond_id, "severity": finding.severity.value, "root_cause": finding.root_cause}
    )
    _report_detection(doc)
    _datadog_event(doc, "incident_open", _alert_text(pond_id, finding))
    logger.error(
        "incident opened",
        extra={
            "incident_id": incident_id,
            "pond": pond_id,
            "severity": finding.severity.value,
            "detection_delay_sec": doc["detection_delay_sec"],
        },
    )
    return incident_id


def _fault_start(pond_id: str, now: datetime) -> datetime | None:
    """The injected fault this incident comes from: the latest one since the pond's previous incident closed."""
    since = now - timedelta(seconds=settings.FAULT_LOOKBACK_SEC)
    closed_at = incidents_repo.last_closed_at(pond_id)
    if closed_at and closed_at > since:
        since = closed_at
    return events_repo.latest_fault_start(pond_id, since, now)


def _report_detection(doc: dict) -> None:
    """Detection delay and the SLO counter; nothing when no fault is known (real sensors have no ground truth)."""
    delay = doc.get("detection_delay_sec")
    if delay is None:
        return
    tags = _timing_tags(doc)
    distribution("incident.detection_delay", delay, tags=tags)
    increment("incident.detected", tags={**tags, "within_slo": str(delay <= settings.DETECTION_SLO_SEC).lower()})


def _report_recovered(incident: dict, recovered_at: datetime) -> None:
    """Recovery time and the Datadog Event of a closed incident; shared by the worker and the operator."""
    seconds = _seconds(incident["created_at"], recovered_at)
    distribution("incident.time_to_recover", seconds, tags=_timing_tags(incident))
    _datadog_event(incident, "incident_recovered", f"Recovered after {seconds:.0f} s.", alert_type="success")


def auto_recover(pond_id: str) -> str | None:
    """The worker closes the active incident automatically once the pond is stable. Returns the incident id if any."""

    def close() -> tuple[dict, datetime] | None:
        existing = incidents_repo.find_active(pond_id)
        if not existing:
            return None
        now = utcnow()
        incidents_repo.set_state(
            existing["_id"], IncidentState.RECOVERED, action="worker_auto_recovered", fields={"recovered_at": now}
        )
        events_repo.record("incident_recovered", pond_id, incident_id=existing["_id"])
        return existing, now

    closed = run_in_transaction(close)
    if closed is None:
        return None
    incident, recovered_at = closed
    _report_recovered(incident, recovered_at)
    increment("incident.recovered", tags={"pond": pond_id})
    logger.info("incident auto-recovered", extra={"pond": pond_id, "incident_id": incident["_id"]})
    return incident["_id"]


def _write_transition(
    incident_id: str, state: IncidentState, action: str | None, remedy: Remedy | None
) -> tuple[dict, dict]:
    """Validate and write a state change and its `incident_action` event. Returns (incident before, fields set).

    Run inside a transaction: the state is read from the same snapshot it is written over, so two operators racing
    on one incident cannot both pass the check (the loser is retried, re-reads the new state and gets a 409).
    """
    incident = incidents_repo.get(incident_id)
    if not incident:
        raise IncidentNotFound(incident_id)
    pond_id = incident.get("pond_id")
    from_state = incident.get("state")
    ensure_transition(from_state, state)
    # Every transition writes to events (even without a note) so the timeline has no gaps
    details = {"from_state": from_state, "state": state.value}
    if action:
        details["action"] = action
    if remedy:
        details["remedy"] = remedy.value
    events_repo.record("incident_action", pond_id, incident_id=incident_id, details=details)

    now = utcnow()
    fields = {}
    if state == IncidentState.INVESTIGATING and from_state != IncidentState.INVESTIGATING.value:
        # Investigating again: the operator picks the remedies afresh
        fields |= {"remedies": [], "remedy_check": None}
    if from_state == IncidentState.OPEN.value and state != IncidentState.OPEN and not incident.get("acknowledged_at"):
        fields["acknowledged_at"] = now
    if state == IncidentState.RECOVERED and from_state != IncidentState.RECOVERED.value:
        fields["recovered_at"] = now
    incidents_repo.set_state(incident_id, state, action=action, fields=fields)
    return incident, fields


def _report_transition(incident: dict, state: IncidentState, action: str | None, fields: dict) -> None:
    """Datadog side of a committed transition: time to acknowledge, recovery time, the incident Event."""
    set_span_tags(pond_id=incident.get("pond_id"))
    if "acknowledged_at" in fields:
        distribution(
            "incident.time_to_acknowledge",
            _seconds(incident["created_at"], fields["acknowledged_at"]),
            tags=_timing_tags(incident),
        )
    from_state = incident.get("state")
    if "recovered_at" in fields:
        _report_recovered(incident, fields["recovered_at"])
    elif state.value != from_state:
        _datadog_event(incident, f"incident_{state.value}", action or f"{from_state} -> {state.value}")


def transition(incident_id: str, state: IncidentState, action: str | None = None, remedy: Remedy | None = None) -> None:
    """A technician moves the incident to the next step (Investigating/Resolving/Recovered), with an action note."""
    incident, fields = run_in_transaction(lambda: _write_transition(incident_id, state, action, remedy))
    _report_transition(incident, state, action, fields)


def apply_remedy(incident_id: str, remedy: Remedy, note: str | None = None) -> dict:
    """The operator applies a remedy to an incident: recorded on the incident, which moves to Resolving.

    Only new readings can prove the pond recovered, so the worker (not this call) moves it to Recovered.
    On the simulated farm the remedy also ends the simulated cause (pond_service.apply_remedy).
    """
    incident = incidents_repo.get(incident_id)
    if not incident:
        raise IncidentNotFound(incident_id)
    label = REMEDIES[remedy].label
    pond_id = incident["pond_id"]
    # The value the remedy has to improve on, read before the farm changes
    now = utcnow()
    metric = watched_metric(incident.get("root_cause"))
    baseline = _current_value(pond_id, metric, now) if metric else None
    action = f"{label}: {note}" if note else label
    check = {
        "metric": metric.value if metric else None,
        "baseline": baseline,
        "applied_at": now,
        "outcome": RemedyOutcome.PENDING.value,
    }

    def record() -> tuple[dict, dict]:
        written = _write_transition(incident_id, IncidentState.RESOLVING, action, remedy)
        incidents_repo.add_remedy(incident_id, remedy.value, check)
        return written

    # Record on the incident first: if it was closed meanwhile this raises (409) before the farm is changed.
    # The farm change itself is the physical world (here: the simulator), so it is not part of the transaction.
    before, fields = run_in_transaction(record)
    _report_transition(before, IncidentState.RESOLVING, action, fields)
    cleared = pond_service.apply_remedy(pond_id, remedy)
    return {
        "incident_id": incident_id,
        "remedy": remedy.value,
        "state": IncidentState.RESOLVING.value,
        "cleared": cleared,
    }


# Sensor readings older than this do not describe the pond now
CURRENT_VALUE_WINDOW = timedelta(minutes=10)


def _current_value(pond_id: str, metric: MetricType, now: datetime) -> float | None:
    """Latest reading of `metric`; test-kit metrics are measured a few times a day, so they stay current longer."""
    window = derivations.MANUAL_FRESHNESS if metric in MANUAL_METRIC_UNITS else CURRENT_VALUE_WINDOW
    return metrics_repo.latest_value(pond_id, metric.value, now - window)


_METRIC_VI = {"do": "DO", "nh3": "NH₃", "co2": "CO₂", "orp": "ORP", "ph": "pH", "tan": "TAN"}


def check_remedy(pond_id: str, now: datetime | None = None, still_abnormal: bool = True) -> RemedyOutcome | None:
    """Judge the latest remedy of the pond's Resolving incident once REMEDY_CHECK_SEC has passed (worker, every cycle).

    Improving: it stays Resolving until the worker sees a stable pond. Failed (the metric held or got worse): the
    operator is asked to investigate again. Judged once per remedy; None when there is nothing to judge.
    `still_abnormal` False (no rule fires any more): the metrics are back to normal, so the remedy worked.
    """
    incident = incidents_repo.find_active(pond_id)
    if incident is None or incident.get("state") != IncidentState.RESOLVING.value:
        return None
    check = incident.get("remedy_check") or {}
    if check.get("outcome") != RemedyOutcome.PENDING.value or not check.get("applied_at"):
        return None
    now = now or utcnow()
    elapsed = _seconds(check["applied_at"], now)
    if still_abnormal and elapsed < settings.REMEDY_CHECK_SEC:
        return RemedyOutcome.PENDING  # too early: skip reading the metric
    metric = MetricType(check["metric"]) if check.get("metric") in set(MetricType) else None
    current = _current_value(pond_id, metric, now) if metric else None
    baseline = check.get("baseline")
    outcome = remedy_outcome(
        metric, baseline, current, elapsed, settings.REMEDY_CHECK_SEC, Thresholds.from_settings(), still_abnormal
    )
    if outcome == RemedyOutcome.PENDING:
        return outcome
    fields = {"outcome": outcome.value, "value": current, "checked_at": now, "normal": not still_abnormal}
    details = {
        "metric": metric.value if metric else None,
        "baseline": baseline,
        "value": current,
        "remedies": incident.get("remedies", []),
    }
    what = "remedy_normal" if not still_abnormal else f"remedy_{outcome.value}"

    def record() -> bool:
        if not incidents_repo.set_remedy_outcome(incident["_id"], check["applied_at"], fields):
            return False  # another remedy or "investigate again" came first
        events_repo.record(what, pond_id, incident_id=incident["_id"], details=details)
        return True

    if not run_in_transaction(record):
        return None
    if outcome == RemedyOutcome.FAILED and metric:
        name = _METRIC_VI.get(metric.value, metric.value)
        _datadog_event(
            incident,
            "remedy_failed",
            f"{name} không cải thiện sau {elapsed:.0f} s: {baseline:g} → {current:g}. Cần điều tra lại.",
            alert_type="warning",
        )
        logger.warning("remedy failed", extra={"pond": pond_id, "incident_id": incident["_id"], **details})
    return outcome


# The Datadog incident table lists what the control panel lists: active incidents and those recovered in 24 hours
RECENT_RECOVERED = timedelta(hours=24)


def _minutes(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 60, 1)


def incident_list_series(active: list[dict], recovered: list[dict], now: datetime) -> list[tuple[str, float, dict]]:
    """One gauge per listed incident, so a Datadog query table can show the control panel's incident list.

    Active: incident.open_minutes = minutes since it opened, tagged with its state. Recovered:
    incident.recovered_minutes = minutes it took to recover. Opened/recovered times are tags in farm time
    ("09/11_14:32"); the incident id tag keeps repeats of the same cause in one pond on separate rows.
    """
    series = []
    for inc in active:
        if inc.get("created_at"):  # a malformed document must not stop the other rows
            series.append(("incident.open_minutes", _minutes(inc["created_at"], now), _list_tags(inc)))
    for inc in recovered:
        if inc.get("created_at") and inc.get("recovered_at"):
            series.append(
                ("incident.recovered_minutes", _minutes(inc["created_at"], inc["recovered_at"]), _list_tags(inc))
            )
    return series


def _farm_time(ts: datetime) -> str:
    """Naive UTC -> "dd/mm_hh:mm" in the farm's timezone: a valid tag value (Datadog keeps `/` and `:`)."""
    return ts.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(settings.FARM_TIMEZONE)).strftime("%d/%m_%H:%M")


def _list_tags(incident: dict) -> dict:
    tags = {
        "pond": incident["pond_id"],
        "severity": incident.get("severity"),
        "state": incident["state"],
        "title": tag_value(incident.get("title") or ""),
        "opened_at": _farm_time(incident["created_at"]),
        "incident_id": incident["_id"],
    }
    if incident.get("recovered_at"):
        tags["recovered_at"] = _farm_time(incident["recovered_at"])
    return tags


def report_active(pond_ids: list[str]) -> None:
    """Sent every worker cycle, so Datadog shows what is CURRENTLY going on (incident.open only counts openings).

    incident.active: 0/1 per pond, for the counts. incident.open_minutes / recovered_minutes: one series per
    incident, for the incident table; a series stops when its incident leaves the list.
    """
    now = utcnow()
    active = incidents_repo.list_active()
    active_ponds = {inc["pond_id"] for inc in active}
    for pid in pond_ids:
        gauge("incident.active", 1 if pid in active_ponds else 0, tags={"pond": pid})
    recovered = incidents_repo.recovered_since(now - RECENT_RECOVERED)
    for metric, value, tags in incident_list_series(active, recovered, now):
        gauge(metric, value, tags=tags)


def list_incidents(state: IncidentState | None = None, limit: int = 50) -> list[dict]:
    return incidents_repo.list_recent(state, limit)


def timeline(incident_id: str) -> dict:
    row = incidents_repo.timeline(incident_id)
    if row is None:
        raise IncidentNotFound(incident_id)
    set_span_tags(pond_id=row.get("pond_id"))
    now = utcnow()
    row["links"] = incident_links(
        incident_id, row["pond_id"], row.get("created_at") or now, row.get("recovered_at") or now
    )
    row["suggested_remedies"] = [
        {"remedy": r.value, "label": REMEDIES[r].label, "description": REMEDIES[r].description}
        for r in suggested_remedies(row.get("root_cause"))
    ]
    return row
