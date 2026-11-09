"""The Datadog incident table: the gauges the worker sends and the dashboard queries that read them."""

import json
import re
from datetime import datetime, timedelta

from app.cli import datadog_push
from app.core.config import settings
from app.core.telemetry import tag_value
from app.domain.incident_lifecycle import ACTIVE_STATES
from app.services.incidents import incident_list_series

NOW = datetime(2026, 11, 9, 14, 33)


def _dashboard_widgets() -> dict[int, dict]:
    dashboard = json.loads(
        (datadog_push.ROOT / "datadog" / "dashboards" / "shrimpwatch_overview.json").read_text(encoding="utf-8")
    )
    found = {}

    def walk(widgets):
        for w in widgets:
            found[w["id"]] = w["definition"]
            walk(w["definition"].get("widgets", []))

    walk(dashboard["widgets"])
    return found


def test_titles_become_readable_tag_values():
    assert tag_value("Cảnh báo: Oxy giảm nhanh bất thường") == "cảnh_báo_oxy_giảm_nhanh_bất_thường"
    assert tag_value("SỰ CỐ: Quạt nước hỏng → oxy sụt") == "sự_cố_quạt_nước_hỏng_oxy_sụt"
    assert tag_value("Cảnh báo: NH₃ độc cao") == "cảnh_báo_nh3_độc_cao"
    assert not set("|,# ") & set(tag_value("a|b, c#d"))


def test_times_are_shown_in_farm_time(monkeypatch):
    monkeypatch.setattr(settings, "FARM_TIMEZONE", "UTC")
    inc = {"_id": "inc-a", "pond_id": "pond-01", "state": "open", "title": "t", "created_at": NOW}
    assert incident_list_series([inc], [], NOW)[0][2]["opened_at"] == "09/11_14:33"


def test_active_incidents_report_minutes_open_and_recovered_ones_minutes_to_recover():
    active = [
        {
            "_id": "inc-a",
            "pond_id": "pond-05",
            "state": "open",
            "severity": "warning",
            "title": "Cảnh báo: Oxy giảm nhanh bất thường",
            "created_at": NOW - timedelta(minutes=1, seconds=30),
        }
    ]
    recovered = [
        {
            "_id": "inc-b",
            "pond_id": "pond-02",
            "state": "recovered",
            "severity": "warning",
            "title": "Cảnh báo: NH₃ độc cao",
            "created_at": NOW - timedelta(hours=3),
            "recovered_at": NOW - timedelta(hours=3) + timedelta(seconds=33),
        }
    ]
    assert incident_list_series(active, recovered, NOW) == [
        (
            "incident.open_minutes",
            1.5,
            {
                "pond": "pond-05",
                "severity": "warning",
                "state": "open",
                "title": "cảnh_báo_oxy_giảm_nhanh_bất_thường",
                "opened_at": "09/11_21:31",
                "incident_id": "inc-a",
            },
        ),
        (
            "incident.recovered_minutes",
            0.6,
            {
                "pond": "pond-02",
                "severity": "warning",
                "state": "recovered",
                "title": "cảnh_báo_nh3_độc_cao",
                "opened_at": "09/11_18:33",
                "incident_id": "inc-b",
                "recovered_at": "09/11_18:33",
            },
        ),
    ]


def test_dashboard_tables_read_the_series_the_worker_sends():
    widgets = _dashboard_widgets()
    open_queries = [q["query"] for q in widgets[801]["requests"][0]["queries"]]
    # One column per active state, so every active incident shows up
    for state in ACTIVE_STATES:
        assert any(f"shrimpwatch.incident.open_minutes{{state:{state.value}," in q for q in open_queries)
    recovered_queries = [q["query"] for q in widgets[802]["requests"][0]["queries"]]
    assert recovered_queries[0].startswith("max:shrimpwatch.incident.recovered_minutes{")
    sample = {"_id": "inc-a", "pond_id": "pond-01", "state": "open", "title": "t", "created_at": NOW}
    recovered = {**sample, "state": "recovered", "recovered_at": NOW}
    open_tags, recovered_tags = (set(row[2]) for row in incident_list_series([sample], [recovered], NOW))
    for queries, sent_tags in ((open_queries, open_tags), (recovered_queries, recovered_tags)):
        for query in queries:
            group_by = re.search(r" by \{([^}]*)\}", query).group(1).split(",")
            assert group_by[0] == "incident_id"  # first column, the id shown in the control panel
            # Datadog fills a gap for 5 minutes by default: an ended incident would stay on the table
            assert query.endswith(".fill(null)")
            assert set(group_by) <= sent_tags
    # Both tables can be searched like the control panel list
    assert widgets[801]["has_search_bar"] == widgets[802]["has_search_bar"] == "always"
    # Links only use the grouped tags, and a host placeholder that datadog_push fills in
    for link in widgets[801]["custom_links"] + widgets[802]["custom_links"]:
        assert link["link"].startswith(("$DD_APP_URL/", "$PUBLIC_BASE_URL/"))
        assert set(re.findall(r"\{\{(\w+)\.value\}\}", link["link"])) <= open_tags


def test_custom_link_placeholders_are_filled_or_dropped():
    payload = {
        "widgets": [
            {
                "id": 1,
                "definition": {
                    "type": "group",
                    "widgets": [
                        {
                            "id": 2,
                            "definition": {
                                "type": "query_table",
                                "custom_links": [
                                    {"label": "panel", "link": "$PUBLIC_BASE_URL/control?ao={{pond.value}}"},
                                    {"label": "events", "link": "$DD_APP_URL/event/explorer?query=x"},
                                ],
                            },
                        }
                    ],
                },
            }
        ]
    }
    filled = datadog_push.with_link_bases(payload, {"$DD_APP_URL": "https://app.dd", "$PUBLIC_BASE_URL": ""})
    links = filled["widgets"][0]["definition"]["widgets"][0]["definition"]["custom_links"]
    assert links == [{"label": "events", "link": "https://app.dd/event/explorer?query=x"}]
    # The input is left untouched (the JSON file is the source)
    assert len(payload["widgets"][0]["definition"]["widgets"][0]["definition"]["custom_links"]) == 2
