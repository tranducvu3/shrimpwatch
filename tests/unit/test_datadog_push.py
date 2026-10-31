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
    monitors = [{"id": 7, "name": "[ShrimpWatch] Existing"}, {"id": 8, "name": "[ShrimpWatch] Existing (copy)"}]
    monkeypatch.setattr(datadog_push.httpx, "get", lambda url, **kw: _Resp(monitors))
    monkeypatch.setattr(datadog_push.httpx, "post", lambda url, **kw: log.append(("POST", url)) or _Resp({}))
    monkeypatch.setattr(datadog_push.httpx, "put", lambda url, **kw: log.append(("PUT", url)) or _Resp({}))
    return log


def test_existing_monitor_is_updated_by_exact_name(tmp_path, calls):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"name": "[ShrimpWatch] Existing"}), encoding="utf-8")
    datadog_push._push("monitor", path)
    assert calls == [("PUT", "https://api.datadoghq.com/api/v1/monitor/7")]


def test_new_monitor_is_created(tmp_path, calls):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"name": "[ShrimpWatch] New"}), encoding="utf-8")
    datadog_push._push("monitor", path)
    assert calls == [("POST", "https://api.datadoghq.com/api/v1/monitor")]
