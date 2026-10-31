# AGENTS.md

ShrimpWatch: IoT monitoring for 6 shrimp ponds. FastAPI ingest API, a simulator and a worker write sensor
data to a MongoDB Time Series collection; the worker derives metrics (CO2, NH3, pH swing, DO drop rate),
opens and closes incidents, and everything is observed in Datadog (metrics, logs, APM).

## Layout

| Path | What it is |
|---|---|
| `app/api/__init__.py` | FastAPI app and all HTTP routes |
| `app/db.py` | Mongo client, collection setup (`init_collections`), all aggregation pipelines |
| `app/worker.py` | Derived metrics, incident open/update/recover logic |
| `app/simulator.py`, `app/demo_incident.py` | Synthetic farm data and the pump-failure demo |
| `app/models.py` | Pydantic v2 models and enums (`MetricType`, `IncidentState`, ...) |
| `app/config.py` | Settings and alert thresholds (env vars, `.env`) |
| `app/metrics.py`, `app/logging_setup.py`, `app/datadog_push.py` | DogStatsD metrics, JSON logs with trace ids, dashboard/monitor upload |
| `datadog/` | Agent config, dashboard and monitor JSON |
| `tests/test_pipeline.py` | Integration tests against a real MongoDB |

## Setup and checks

Python 3.11. Install with `pip install -r requirements-dev.txt`.

Tests need a real MongoDB 7. **The test fixture calls `init_collections(force=True)`, which drops every
collection.** Always run tests against a throwaway local database, never the URI in `.env` (it may point at
Atlas). Environment variables override `.env`, so set them explicitly:

```bash
mkdir -p /tmp/db && mongod --fork --logpath /tmp/mongod.log --dbpath /tmp/db   # if Mongo is not running
MONGODB_URI=mongodb://localhost:27017 MONGODB_DB=shrimpwatch_test DD_TRACE_ENABLED=false \
  pytest -v -rs --cov tests/
```

Without MongoDB the tests skip locally; with `CI=true` they fail instead. A skipped run is not a passing run.

Lint (same as CI, ruff 0.7.4, line length 120):

```bash
ruff check app tests
ruff format --check app tests
```

CI (`.github/workflows/ci.yml`) runs lint, tests with a coverage floor of 64% (`.coveragerc`), and a Docker
Compose smoke test (`scripts/ci/smoke_test.sh`). All three must pass.

## Conventions

- Code comments are Vietnamese without diacritics (`# Toc do sut DO ...`); identifiers and log messages are English.
- Datetimes are naive UTC (`datetime.utcnow()`, `_naive_utc` in the API). Do not mix in timezone-aware values.
- Telemetry goes into `metrics` through `pack_ts_doc`: fields that identify a series live under `metadata`,
  the reading is `value`, time is `timestamp`. Hourly rollups live in `metrics_hourly`.
- Aggregation pipelines are built by functions in `app/db.py` that return a list, so they can be tested.
- A new metric touches several places together: `MetricType` (and `MANUAL_METRIC_UNITS` if manual), a
  threshold in `config.py`, worker logic, the Datadog dashboard/monitor JSON, a test, and the README table.
- Every incident state change also writes a document to `events`.
- Keep ruff clean; do not add new dependencies without a reason in the PR.

## Review guidelines

Treat these as high priority (P1):

- Tests or scripts that could run destructive setup (`init_collections(force=True)`, `drop`, `delete_many`)
  against a non-test database, or that change the test DB guard in `tests/conftest.py`.
- Secrets or real credentials in code, compose files, Datadog JSON, test fixtures or logs. See `SECURITY.md`.
- User input reaching a Mongo query as a raw dict or operator (`$where`, `$function`, `$expr` built from
  input, unvalidated `dict` bodies). Request data must go through Pydantic models or enums.
- Aggregation or `find` on `metrics` without a `metadata.pond_id` / `metadata.metric` / `timestamp` filter
  that matches the `pond_metric_ts` index, or an unbounded query on a hot path (worker loop, ingest).
- Changes to time series options, TTLs (`TS_TTL_SECONDS`, `ROLLUP_TTL_SECONDS`) or the unique index used by
  the `$merge` in `rollup_hourly_pipeline`: these are hard to undo on existing data.
- Incident lifecycle bugs: duplicate open incidents per pond, states skipped or never recovered, missing
  `events` entries.
- Wrong units or formulas in derived metrics (CO2, NH3, DO drop rate, pH swing) or thresholds in `config.py`.
- Metric or tag names changed in code but not in `datadog/dashboards` / `datadog/monitors` (or the reverse).

Do not flag:

- Vietnamese comments, or the hackathon demo defaults that `SECURITY.md` lists as known.
- Style issues that ruff already enforces.
