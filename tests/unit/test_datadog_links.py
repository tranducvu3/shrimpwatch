from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from app.core import datadog_links
from app.core.datadog_links import app_base_url, incident_links


def test_app_base_url_per_site():
    assert app_base_url("datadoghq.com") == "https://app.datadoghq.com"
    assert app_base_url("datadoghq.eu") == "https://app.datadoghq.eu"
    assert app_base_url("us5.datadoghq.com") == "https://us5.datadoghq.com"


def test_links_filter_by_pond_and_window(monkeypatch):
    monkeypatch.setattr(datadog_links.settings, "DD_SITE", "datadoghq.com")
    monkeypatch.setattr(datadog_links.settings, "DD_DASHBOARD_ID", "abc-123-xyz")
    links = incident_links("inc-1", "pond-03", datetime(2026, 11, 8, 12, 0), datetime(2026, 11, 8, 12, 30))

    dash = urlparse(links["dashboard"])
    assert dash.path == "/dashboard/abc-123-xyz" and parse_qs(dash.query)["tpl_var_ao"] == ["pond-03"]
    logs = parse_qs(urlparse(links["logs"]).query)
    assert logs["query"] == ["service:shrimpwatch @pond:pond-03"]
    # 15 minutes before the incident opened, 5 minutes after its last change
    assert int(logs["from_ts"][0]) == int(datetime(2026, 11, 8, 11, 45, tzinfo=timezone.utc).timestamp() * 1000)
    assert int(logs["to_ts"][0]) - int(logs["from_ts"][0]) == 50 * 60 * 1000
    assert parse_qs(urlparse(links["events"]).query)["query"] == ["incident_id:inc-1"]


def test_dashboard_link_falls_back_to_the_list(monkeypatch):
    monkeypatch.setattr(datadog_links.settings, "DD_DASHBOARD_ID", "")
    links = incident_links("inc-1", "pond-03", datetime(2026, 11, 8, 12, 0), datetime(2026, 11, 8, 12, 0))
    assert urlparse(links["dashboard"]).path == "/dashboard/lists"
