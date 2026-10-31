# AGENTS.md

ShrimpWatch: IoT monitoring for 6 shrimp ponds. FastAPI ingest API, a simulator and a worker write sensor
data to a MongoDB Time Series collection; the worker derives metrics (CO2, NH3, pH swing, DO drop rate),
opens and closes incidents, and everything is observed in Datadog (metrics, logs, APM).

## Layout

Layered: `api`, `worker` -> `services` -> `db.repositories` -> `db.mongo`; `services` also use `domain`.
`domain` never imports `db`, `services` or HTTP code. The simulator and the demo talk only to the HTTP API.

| Path | What it is |
|---|---|
| `app/core/` | `config.py` (settings, thresholds), `log.py` (JSON logs with trace ids), `telemetry.py` (DogStatsD, span tags), `clock.py` (naive UTC) |
| `app/domain/` | Pure logic: `models.py` (Pydantic models, enums), `water_chemistry.py` (CO2, NH3), `incident_rules.py`, `incident_lifecycle.py` (allowed state transitions), `ponds.py` |
| `app/db/` | `mongo.py` (client, collections), `schema.py` (`init_collections`, indexes, migrations), `pipelines.py` (aggregation builders), `repositories/` (one module per collection) |
| `app/services/` | The only writers: `ingest`, `derivations`, `incidents`, `ponds` |
| `app/api/` | `main.py` (app, lifespan), `schemas.py` (request bodies), `routers/` (thin, map service errors to status codes) |
| `app/worker/` | `loop.py` (30 s cycle, rollup), `pond_check.py` (snapshot -> rules -> incident service) |
| `app/simulator/` | `pond_model.py`, `runner.py`, `api_client.py` |
| `app/cli/` | `init_db`, `demo_incident`, `datadog_push` (`python -m app.cli.<name>`) |
| `datadog/` | Agent config, dashboard and monitor JSON |
| `tests/unit/` | Pure tests, no MongoDB |
| `tests/integration/` | Tests against a real MongoDB (`conftest.py` holds the DB guard) |

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

Without MongoDB the integration tests skip locally; with `CI=true` they fail instead. A skipped run is not a
passing run. `pytest tests/unit` needs no MongoDB.

Lint (same as CI, ruff 0.7.4, line length 120):

```bash
ruff check app tests
ruff format --check app tests
```

CI (`.github/workflows/ci.yml`) runs lint, tests with a coverage floor of 64% (`.coveragerc`), and a Docker
Compose smoke test (`scripts/ci/smoke_test.sh`). All three must pass.

## Conventions

- Code comments are Vietnamese without diacritics (`# Toc do sut DO ...`); identifiers and log messages are English.
- Datetimes are naive UTC (`app.core.clock.utcnow()`, `to_naive_utc`). Do not mix in timezone-aware values.
- Telemetry goes into `metrics` through `pack_ts_doc` (`app/db/repositories/metrics.py`): fields that identify a series live under `metadata`,
  the reading is `value`, time is `timestamp`. Hourly rollups live in `metrics_hourly`.
- Aggregation pipelines are built by functions in `app/db/pipelines.py` that return a list, so they can be tested.
- Business rules and formulas go in `app/domain/` as pure functions with a unit test; Mongo access goes in a
  repository; writes that also touch `events` or metrics go in a service.
- A new metric touches several places together: `MetricType` (and `MANUAL_METRIC_UNITS` if manual), a
  threshold in `app/core/config.py`, the formula in `app/domain/` and `app/services/derivations.py`, the
  Datadog dashboard/monitor JSON, a test, and the README table.
- Every incident state change goes through `app/services/incidents.py`, which also writes to `events`.
  Transitions are validated by `app/domain/incident_lifecycle.py` (invalid ones return 409).
- Keep ruff clean; do not add new dependencies without a reason in the PR.

## Review guidelines

These rules are repeated in `.github/copilot-instructions.md` for Copilot code review. Update both together.

Treat these as high priority (P1):

- Tests or scripts that could run destructive setup (`init_collections(force=True)`, `drop`, `delete_many`)
  against a non-test database, or that change the test DB guard in `tests/integration/conftest.py`.
- Secrets or real credentials in code, compose files, Datadog JSON, test fixtures or logs. See `SECURITY.md`.
- User input reaching a Mongo query as a raw dict or operator (`$where`, `$function`, `$expr` built from
  input, unvalidated `dict` bodies). Request data must go through Pydantic models or enums.
- Aggregation or `find` on `metrics` without a `metadata.pond_id` / `metadata.metric` / `timestamp` filter
  that matches the `pond_metric_ts` index, or an unbounded query on a hot path (worker loop, ingest).
- Changes to time series options, TTLs (`TS_TTL_SECONDS`, `ROLLUP_TTL_SECONDS`) or the unique index used by
  the `$merge` in `rollup_hourly_pipeline`: these are hard to undo on existing data.
- Incident lifecycle bugs: duplicate open incidents per pond, states skipped or never recovered, missing
  `events` entries, incident writes that bypass `app/services/incidents.py`.
- Wrong units or formulas in derived metrics (CO2, NH3, DO drop rate, pH swing) or thresholds in
  `app/core/config.py`.
- Metric or tag names changed in code but not in `datadog/dashboards` / `datadog/monitors` (or the reverse).

Do not flag:

- Vietnamese comments, or the hackathon demo defaults that `SECURITY.md` lists as known.
- Style issues that ruff already enforces.
