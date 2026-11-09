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
