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


def test_telegram_webhook_needs_token_and_chat(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(datadog_push.settings, "TELEGRAM_CHAT_ID", "12345")
    assert datadog_push.telegram_webhook() is None

    monkeypatch.setattr(datadog_push.settings, "TELEGRAM_BOT_TOKEN", "123:secret")
    hook = datadog_push.telegram_webhook()
    assert hook["url"] == "https://api.telegram.org/bot123:secret/sendMessage"
    assert json.loads(hook["payload"]) == {
        "chat_id": "12345",
        "text": "$TEXT_ONLY_MSG",
        "disable_web_page_preview": True,
    }


def test_webhook_push_never_logs_the_token(monkeypatch):
    logged = []
    monkeypatch.setattr(datadog_push.settings, "TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setattr(datadog_push.settings, "TELEGRAM_CHAT_ID", "12345")
    echo = _Resp({"url": "https://api.telegram.org/bot123:secret/sendMessage"})
    monkeypatch.setattr(datadog_push.httpx, "get", lambda url, **_kw: _Resp({}, status=404))
    monkeypatch.setattr(datadog_push.httpx, "post", lambda url, **_kw: echo)
    monkeypatch.setattr(datadog_push.logger, "info", lambda msg, extra=None: logged.append((msg, extra)))

    assert datadog_push._push_telegram_webhook(datadog_push.telegram_webhook()) == 200
    assert "secret" not in json.dumps(logged)


def test_telegram_handle_is_added_only_to_tagged_monitors_when_configured():
    tagged = {"message": "m", "tags": ["shrimpwatch", "notify:telegram"]}
    other = {"message": "m", "tags": ["shrimpwatch"]}
    assert datadog_push.with_notify_handles(tagged, telegram=True)["message"] == "m\n\n@webhook-shrimpwatch-telegram"
    assert datadog_push.with_notify_handles(tagged, telegram=False) == tagged
    assert datadog_push.with_notify_handles(other, telegram=True) == other


def test_farmer_alert_monitors_match_the_event_tags_the_service_sends():
    """The incident service tags Events with incident_event:<what> and pond (app/services/incidents.py)."""
    for what, name in (
        ("incident_open", "farmer_alert_open.json"),
        ("incident_escalated", "farmer_alert_escalated.json"),
    ):
        monitor = json.loads((datadog_push.ROOT / "datadog" / "monitors" / name).read_text(encoding="utf-8"))
        assert f"service:shrimpwatch incident_event:{what}" in monitor["query"]
        assert '.by("pond")' in monitor["query"] and "notify:telegram" in monitor["tags"]


def test_main_fails_when_the_configured_webhook_cannot_be_pushed(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "DD_API_KEY", "k")
    monkeypatch.setattr(datadog_push.settings, "DD_APP_KEY", "a")
    monkeypatch.setattr(datadog_push.settings, "TELEGRAM_BOT_TOKEN", "123:secret")
    monkeypatch.setattr(datadog_push.settings, "TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setattr(datadog_push, "_push_telegram_webhook", lambda hook: 403)
    pushed = []
    monkeypatch.setattr(datadog_push, "_push", lambda kind, path, telegram=False: pushed.append(telegram) or (200, {}))
    assert datadog_push.main() == 1
    assert pushed and not any(pushed)  # everything else still pushed, without the handle


def test_main_fails_when_datadog_rejects_a_file(monkeypatch):
    monkeypatch.setattr(datadog_push.settings, "DD_API_KEY", "k")
    monkeypatch.setattr(datadog_push.settings, "DD_APP_KEY", "a")
    monkeypatch.setattr(datadog_push.settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(
        datadog_push, "_push", lambda kind, path, telegram=False: (400 if path.name == "do_low.json" else 200, {})
    )
    assert datadog_push.main() == 1
    monkeypatch.setattr(datadog_push, "_push", lambda kind, path, telegram=False: (200, {}))
    assert datadog_push.main() == 0
