import json

import pytest

from app.cli import datadog_push


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


@pytest.fixture
def calls(monkeypatch):
    log: list[tuple[str, str]] = []
    monitors = [
        {"id": 7, "name": "[ShrimpWatch] Existing", "query": "avg:a < 1"},
        {"id": 8, "name": "[ShrimpWatch] Existing (copy)", "query": "avg:b < 1"},
    ]
    monkeypatch.setattr(datadog_push.httpx, "get", lambda url, **_kw: _Resp(monitors))
    monkeypatch.setattr(datadog_push.httpx, "post", lambda url, **_kw: log.append(("POST", url)) or _Resp({}))
    monkeypatch.setattr(datadog_push.httpx, "put", lambda url, **_kw: log.append(("PUT", url)) or _Resp({}))
    return log


def _monitor(tmp_path, **payload):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_existing_monitor_is_updated_by_exact_name(tmp_path, calls):
    datadog_push._push("monitor", _monitor(tmp_path, name="[ShrimpWatch] Existing"))
    assert calls == [("PUT", "https://api.datadoghq.com/api/v1/monitor/7")]


def test_new_monitor_is_created(tmp_path, calls):
    datadog_push._push("monitor", _monitor(tmp_path, name="[ShrimpWatch] New", query="avg:c < 1"))
    assert calls == [("POST", "https://api.datadoghq.com/api/v1/monitor")]


def test_renamed_monitor_is_updated_by_query(tmp_path, calls):
    datadog_push._push("monitor", _monitor(tmp_path, name="[ShrimpWatch] Renamed · Đổi tên", query="avg:b < 1"))
    assert calls == [("PUT", "https://api.datadoghq.com/api/v1/monitor/8")]


def test_recipients_are_added_to_monitors_tagged_notify_email(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "DD_NOTIFY_EMAILS", " ops@example.com, ,not-an-email,vet@example.org ")
    payload = {"message": "Incident", "tags": ["shrimpwatch", "notify:email"]}
    result = datadog_push._with_recipients(payload)
    assert result["message"] == "Incident\n\nNotify: @ops@example.com @vet@example.org"
    assert payload["message"] == "Incident"


def test_recipients_skip_untagged_monitors_and_empty_setting(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "DD_NOTIFY_EMAILS", "ops@example.com")
    untagged = {"message": "Low DO", "tags": ["shrimpwatch"]}
    assert datadog_push._with_recipients(untagged) == untagged
    monkeypatch.setattr(datadog_push.settings, "DD_NOTIFY_EMAILS", "")
    tagged = {"message": "Incident", "tags": ["notify:email"]}
    assert datadog_push._with_recipients(tagged) == tagged


def test_incident_monitor_watches_the_worker_gauge_and_holds_no_addresses():
    path = datadog_push.ROOT / "datadog" / "monitors" / "incident_open.json"
    monitor = json.loads(path.read_text(encoding="utf-8"))
    assert "shrimpwatch.incident.active" in monitor["query"]
    assert datadog_push.NOTIFY_EMAIL_TAG in monitor["tags"]
    assert not datadog_push._EMAIL.search(monitor["message"])
