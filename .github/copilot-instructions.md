# Copilot instructions for ShrimpWatch

FastAPI + MongoDB (Time Series) + Datadog, Python 3.11. Full context: `AGENTS.md` (setup, conventions) and
`SECURITY.md` (secrets, known gaps). This file repeats the review rules because Copilot code review reads
only this file. Keep the two in sync.

## Code review

Prioritize correctness and data safety over style. Ruff (line length 120) already enforces style; do not
comment on formatting. Code comments are Vietnamese without diacritics on purpose; do not flag them.

Flag as important:

- **Destructive DB operations outside the test DB**: `init_collections(force=True)`, `drop`, `delete_many`,
  or changes to the MongoDB guard in `tests/integration/conftest.py`. Tests must use `MONGODB_DB=shrimpwatch_test`.
- **Secrets**: `DD_API_KEY`, `DD_APP_KEY`, or a Mongo URI with a real password in code, compose files,
  Datadog JSON, tests, docs or log/response output.
- **NoSQL injection**: request data reaching a Mongo query or update without a Pydantic model or enum;
  operators such as `$where`, `$function`, `$expr`, `$regex` built from user input.
- **Query performance**: queries on `metrics` that do not filter by `metadata.pond_id` + `metadata.metric` +
  `timestamp` (index `pond_metric_ts`); unbounded `find`/`aggregate` in the worker loop or ingest path;
  list endpoints without a `limit`; removing the 1000-record cap on `/ingest/batch`.
- **Irreversible schema changes**: time series options, TTLs (`TS_TTL_SECONDS`, `ROLLUP_TTL_SECONDS`), or the
  unique index used by the `$merge` in `rollup_hourly_pipeline`.
- **Incident lifecycle**: more than one open incident per pond, skipped or stuck states, state changes that
  do not also write to `events`, or incident writes outside `app/services/incidents.py`.
- **Derived metrics**: wrong units or formulas for CO2, NH3, DO drop rate or pH swing; threshold changes in
  `app/core/config.py` without a reason.
- **Datadog drift**: metric or tag names changed in `app/core/telemetry.py` / `app/services/` but not in
  `datadog/dashboards` and `datadog/monitors`, or the reverse.
- **Datetimes**: timezone-aware values mixed with the naive UTC used everywhere (`app.core.clock.utcnow()`).
- **New metric checklist**: `MetricType` in `app/domain/models.py`, threshold in `app/core/config.py`, formula
  in `app/domain/` + `app/services/derivations.py`, Datadog JSON, tests in `tests/unit/` and
  `tests/integration/`, and the README metrics table.
- **Layering**: `app/domain/` importing `app.db`, `app.services` or FastAPI; routers querying Mongo directly.
- **CI and dependencies**: unpinned or downgraded packages in `requirements*.txt`; wider workflow
  `permissions`, `pull_request_target`, or untrusted input in `run:` steps.

Do not re-report the known demo gaps in `SECURITY.md` (no API auth, `allow_origins=["*"]`, local Docker
passwords, broad `shrimp_app` role) unless the change makes them worse.
