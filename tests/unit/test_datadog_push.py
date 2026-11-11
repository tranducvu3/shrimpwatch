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


def test_forecast_monitors_follow_datadog_rules():
    """Datadog rejects forecast monitors looking less than 12 h ahead, and threshold_windows (anomaly monitors only)."""
    import re

    hours = {"h": 1, "d": 24, "w": 168}
    for path in (datadog_push.ROOT / "datadog" / "monitors").glob("*.json"):
        monitor = json.loads(path.read_text(encoding="utf-8"))
        if "forecast(" not in monitor["query"]:
            continue
        amount, unit = re.match(r"\w+\(next_(\d+)([hdw])\)", monitor["query"]).groups()
        assert int(amount) * hours[unit] >= 12, path.name
        assert "threshold_windows" not in monitor["options"], path.name


def test_slo_is_updated_by_name_and_created_otherwise(tmp_path, monkeypatch):
    log: list[tuple[str, str]] = []
    slos = {"data": [{"id": "abc123", "name": "[ShrimpWatch] Detection"}]}
    monkeypatch.setattr(datadog_push.httpx, "get", lambda url, **_kw: _Resp(slos))
    monkeypatch.setattr(datadog_push.httpx, "post", lambda url, **_kw: log.append(("POST", url)) or _Resp({}))
    monkeypatch.setattr(datadog_push.httpx, "put", lambda url, **_kw: log.append(("PUT", url)) or _Resp({}))

    datadog_push._push("slo", _monitor(tmp_path, name="[ShrimpWatch] Detection"))
    datadog_push._push("slo", _monitor(tmp_path, name="[ShrimpWatch] Other"))
    assert log == [
        ("PUT", "https://api.datadoghq.com/api/v1/slo/abc123"),
        ("POST", "https://api.datadoghq.com/api/v1/slo"),
    ]


def test_every_datadog_file_is_valid_json_with_a_name():
    for folder, key in (("monitors", "name"), ("slos", "name"), ("dashboards", "title")):
        for path in (datadog_push.ROOT / "datadog" / folder).glob("*.json"):
            assert json.loads(path.read_text(encoding="utf-8"))[key].startswith(("[ShrimpWatch]", "ShrimpWatch")), path
    for path in (datadog_push.ROOT / "datadog" / "workflows").glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["data"]["attributes"]["name"].startswith("[ShrimpWatch]")


def _workflow_file():
    return datadog_push.ROOT / "datadog" / "workflows" / "auto_remediate.json"


def test_workflow_is_skipped_until_automation_is_configured(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "AUTOMATION_TOKEN", "")
    assert datadog_push._push("workflow", _workflow_file()) == (0, "skipped")


def test_workflow_gets_the_url_and_token_and_is_upserted_by_name(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "PUBLIC_BASE_URL", "https://farm.example/")
    monkeypatch.setattr(datadog_push.settings, "AUTOMATION_TOKEN", 'to"ken')
    name = json.loads(_workflow_file().read_text(encoding="utf-8"))["data"]["attributes"]["name"]
    existing = {"data": [{"id": "wf-1", "attributes": {"name": name}}]}
    sent = []
    monkeypatch.setattr(datadog_push.httpx, "get", lambda url, **_kw: _Resp(existing))
    monkeypatch.setattr(datadog_push.httpx, "patch", lambda url, json, **_kw: sent.append((url, json)) or _Resp({}))

    datadog_push._push("workflow", _workflow_file())
    [(url, payload)] = sent
    assert url == "https://api.datadoghq.com/api/v2/workflows/wf-1"
    params = {p["name"]: p["value"] for p in payload["data"]["attributes"]["spec"]["steps"][0]["parameters"]}
    assert params["url"] == "https://farm.example/automation/remediate"
    assert {"key": "X-Automation-Token", "value": ['to"ken']} in params["requestHeaders"]


def test_monitor_starts_the_workflow_by_its_handle():
    handle = json.loads(_workflow_file().read_text(encoding="utf-8"))["data"]["attributes"]["spec"]["handle"]
    monitor = json.loads((datadog_push.ROOT / "datadog" / "monitors" / "auto_remediate_pump.json").read_text("utf-8"))
    assert f"@workflow-{handle}(pond_id={{{{pond.name}}}})" in monitor["message"]


def test_farmer_alert_emails_are_split_trimmed_and_validated(monkeypatch):
    warned = []
    monkeypatch.setattr(datadog_push.logger, "warning", lambda _msg, extra=None: warned.append(extra))
    monkeypatch.setattr(datadog_push.settings, "FARMER_ALERT_EMAILS", "")
    assert datadog_push.farmer_alert_emails() == []

    monkeypatch.setattr(datadog_push.settings, "FARMER_ALERT_EMAILS", " a@farm.vn, not-an-email ,, b@x.example.com")
    assert datadog_push.farmer_alert_emails() == ["a@farm.vn", "b@x.example.com"]
    assert warned == [{"position": 2}]  # the position only: the entry may hold part of a real address


def test_email_handles_are_added_only_to_tagged_monitors_when_configured():
    tagged = {"message": "m", "tags": ["shrimpwatch", "notify:email"]}
    other = {"message": "m", "tags": ["shrimpwatch"]}
    emails = ["a@farm.vn", "b@farm.vn"]
    assert datadog_push.with_notify_handles(tagged, emails)["message"] == "m\n\n@a@farm.vn @b@farm.vn"
    assert datadog_push.with_notify_handles(tagged, []) == tagged
    assert datadog_push.with_notify_handles(other, emails) == other


def test_farmer_alert_monitors_match_the_event_tags_the_service_sends():
    """The incident service tags Events with incident_event:<what> and pond (app/services/incidents.py)."""
    for what, name in (
        ("incident_open", "farmer_alert_open.json"),
        ("incident_escalated", "farmer_alert_escalated.json"),
    ):
        monitor = json.loads((datadog_push.ROOT / "datadog" / "monitors" / name).read_text(encoding="utf-8"))
        assert f"service:shrimpwatch incident_event:{what}" in monitor["query"]
        assert '.by("pond")' in monitor["query"] and "notify:email" in monitor["tags"]


def test_main_passes_the_alert_emails_to_every_push(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "DD_API_KEY", "k")
    monkeypatch.setattr(datadog_push.settings, "DD_APP_KEY", "a")
    monkeypatch.setattr(datadog_push.settings, "FARMER_ALERT_EMAILS", "a@farm.vn")
    pushed = []
    monkeypatch.setattr(datadog_push, "_push", lambda kind, path, emails=(): pushed.append(emails) or (200, {}))
    assert datadog_push.main() == 0
    assert pushed and all(e == ["a@farm.vn"] for e in pushed)


def test_main_fails_when_datadog_rejects_a_file(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "DD_API_KEY", "k")
    monkeypatch.setattr(datadog_push.settings, "DD_APP_KEY", "a")
    monkeypatch.setattr(datadog_push.settings, "FARMER_ALERT_EMAILS", "")
    monkeypatch.setattr(
        datadog_push, "_push", lambda kind, path, emails=(): (400 if path.name == "do_low.json" else 200, {})
    )
    assert datadog_push.main() == 1
    monkeypatch.setattr(datadog_push, "_push", lambda kind, path, emails=(): (200, {}))
    assert datadog_push.main() == 0
