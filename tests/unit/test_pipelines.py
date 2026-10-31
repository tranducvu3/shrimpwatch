from datetime import datetime

from app.db.pipelines import (
    compute_do_drop_rate_pipeline,
    compute_ph_swing_pipeline,
    latest_readings_pipeline,
    rollup_hourly_pipeline,
)

NOW = datetime(2026, 10, 31, 12, 34, 56)


def test_do_drop_rate_window_is_time_based():
    match, window = compute_do_drop_rate_pipeline(["pond-01"], window_sec=300, now=NOW)[:2]
    assert match["$match"]["timestamp"]["$lte"] == NOW
    assert window["$setWindowFields"]["output"]["do_rate"]["window"] == {"range": [-300, 0], "unit": "second"}


def test_rollup_covers_whole_hours_and_merges_idempotently():
    pipeline = rollup_hourly_pipeline(NOW, NOW)
    assert pipeline[0]["$match"]["timestamp"] == {
        "$gte": datetime(2026, 10, 31, 12),
        "$lt": datetime(2026, 10, 31, 13),
    }
    assert pipeline[-1]["$merge"]["whenMatched"] == "replace"


def test_ph_swing_reads_last_24_hours_of_rollups():
    match = compute_ph_swing_pipeline("pond-01", now=NOW)[0]["$match"]
    assert match["hour"] == {"$gte": datetime(2026, 10, 30, 12)}


def test_latest_readings_filter_matches_compound_index():
    match = latest_readings_pipeline(["pond-01"], ["do", "ph"], NOW)[0]["$match"]
    assert match == {
        "metadata.pond_id": {"$in": ["pond-01"]},
        "metadata.metric": {"$in": ["do", "ph"]},
        "timestamp": {"$gte": NOW},
    }
