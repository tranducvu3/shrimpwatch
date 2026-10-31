import pytest

from app.cli.seed_incident_history import build_history
from app.db.pipelines import (
    INCIDENT_VECTOR_INDEX,
    similar_incidents_fallback_pipeline,
    similar_incidents_pipeline,
)
from app.db.schema import incident_vector_index_definition
from app.domain.incident_fingerprint import DIMENSIONS, fingerprint, is_valid, similarity
from app.domain.models import IncidentEvidence, IncidentState

PUMP_FAILURE = IncidentEvidence(
    do_drop_rate_mg_per_l_per_min=0.1,
    avg_pump_power_watts=0.0,
    min_do=3.1,
    last_orp_mv=150.0,
    nh3_mg_per_l=0.05,
    ph_swing_24h=0.3,
)
ALGAE_CRASH = IncidentEvidence(
    do_drop_rate_mg_per_l_per_min=0.06,
    avg_pump_power_watts=1500.0,
    min_do=3.8,
    last_orp_mv=190.0,
    nh3_mg_per_l=0.12,
    ph_swing_24h=0.9,
)


def test_fingerprint_is_normalized_and_fixed_size():
    vector = fingerprint(PUMP_FAILURE)
    assert len(vector) == DIMENSIONS
    assert all(0.0 <= x <= 1.0 for x in vector)
    assert is_valid(vector)


def test_fingerprint_clips_out_of_range_and_fills_missing_with_healthy_values():
    extreme = fingerprint(IncidentEvidence(do_drop_rate_mg_per_l_per_min=5.0, avg_pump_power_watts=-10.0))
    assert extreme[0] == 1.0
    assert extreme[1] == 0.0
    assert fingerprint(IncidentEvidence()) == fingerprint(
        IncidentEvidence(
            do_drop_rate_mg_per_l_per_min=0.0,
            avg_pump_power_watts=1500.0,
            min_do=5.8,
            last_orp_mv=280.0,
            nh3_mg_per_l=0.02,
            ph_swing_24h=0.3,
        )
    )


def test_similarity_ranks_same_root_cause_higher():
    query = fingerprint(
        IncidentEvidence(
            do_drop_rate_mg_per_l_per_min=0.09,
            avg_pump_power_watts=10.0,
            min_do=3.4,
            last_orp_mv=160.0,
            nh3_mg_per_l=0.05,
            ph_swing_24h=0.35,
        )
    )
    assert similarity(query, query) == 1.0
    assert similarity(query, fingerprint(PUMP_FAILURE)) > similarity(query, fingerprint(ALGAE_CRASH))


def test_similarity_rejects_dimension_mismatch():
    with pytest.raises(ValueError):
        similarity([0.1, 0.2], [0.1])


def test_is_valid_rejects_bad_vectors():
    assert not is_valid(None)
    assert not is_valid([0.1] * (DIMENSIONS - 1))
    assert not is_valid([float("nan")] * DIMENSIONS)


def test_vector_search_pipeline_filters_recovered_and_excludes_query_incident():
    pipeline = similar_incidents_pipeline([0.5] * DIMENSIONS, exclude_id="inc-1", k=3)
    stage = pipeline[0]["$vectorSearch"]
    assert stage["index"] == INCIDENT_VECTOR_INDEX
    assert stage["path"] == "fingerprint"
    assert stage["filter"] == {"state": "recovered"}
    assert stage["limit"] == 4
    assert stage["numCandidates"] >= stage["limit"]
    assert {"$match": {"_id": {"$ne": "inc-1"}}} in pipeline
    assert {"$limit": 3} in pipeline


def test_fallback_pipeline_is_bounded_and_uses_state_filter():
    pipeline = similar_incidents_fallback_pipeline([0.5] * DIMENSIONS, exclude_id="inc-1", k=2, scan_limit=50)
    match = pipeline[0]["$match"]
    assert match["state"] == "recovered"
    assert match["_id"] == {"$ne": "inc-1"}
    assert {"$limit": 50} in pipeline
    assert pipeline[-2] == {"$limit": 2}


def test_vector_index_matches_fingerprint_dimensions():
    fields = incident_vector_index_definition()["fields"]
    vector = next(f for f in fields if f["type"] == "vector")
    assert vector["numDimensions"] == DIMENSIONS
    assert vector["similarity"] == "euclidean"
    assert {"type": "filter", "path": "state"} in fields


def test_seed_history_is_deterministic_and_recovered():
    first, second = build_history(), build_history()
    assert [i.id for i in first] == [i.id for i in second]
    assert len({i.id for i in first}) == len(first)
    assert all(i.state == IncidentState.RECOVERED for i in first)
    assert all(i.updated_at > i.created_at for i in first)
