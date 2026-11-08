# ShrimpWatch — Smart Shrimp Pond Monitoring System

> A project for the **MUG Vietnam Mini Hackathon 2026**.
> **ShrimpWatch** is an IoT monitoring system for whiteleg shrimp farms. It combines **MongoDB Atlas (Time Series)** to store sensor data with **Datadog** to observe the farm and raise incident alerts in real time.

| Component | Technology | Role |
|---|---|---|
| Database | **MongoDB Atlas M0** (Time Series, Aggregation, TTL) | Primary operational data platform |
| Observability | **Datadog** (Metrics, Logs, APM, Traces) | Dashboard, monitors/alerts and incident investigation |
| Backend | FastAPI + Pydantic v2 (Python 3.11) | Data ingestion, incident lifecycle management |
| Infrastructure | Docker Compose (5 containers) | Packaging and consistent runtime |

---

## Submission Information

| Item | Details |
|---|---|
| Project name | **ShrimpWatch** — water quality monitoring for whiteleg shrimp ponds |
| Problem | A paddlewheel aerator that fails at night, or heavy rain, can make dissolved oxygen (DO) or salinity crash within a few tens of minutes; detecting it late can kill a whole pond. ShrimpWatch detects it early, supports the investigation with data, records how the incident was handled and proves that the pond has recovered |
| Team | Trần Đức Vũ ([tranducvu3](https://github.com/tranducvu3)) · Hồ Ngọc Dũng ([HoNgocDung1503](https://github.com/HoNgocDung1503)) · Quang Anh |
| Contact | Trần Đức Vũ — vu.tran@techcoop.vn |
| Repository | https://github.com/tranducvu3/shrimpwatch (public) |
| Demo video | Pending: _link to be added before 23:59 31/10/2026_ |
| Presentation slides | Pending: _link to be added before 23:59 31/10/2026_ |
| Demonstrated incident | Pond 1 aerator fails → DO drops → a **CRITICAL `pump_failure`** incident opens → Investigate (Vector Search suggests past incidents) → Resolve → the worker closes the incident by itself once readings are stable. Secondary scenario: heavy rain pushes pond 3 salinity below 10 ppt. Details: [Demo scenario](#incident-demo-scenario-incident-lifecycle), [31/10 verification](#0-end-to-end-verification-on-31102026) |
| Reuse & AI declaration | [Section 11](#declaration-of-reused-components-datasets--ai-tools) |

---

## Table of Contents

0. [Submission Information](#submission-information)
1. [Project Overview](#project-overview)
2. [System Architecture](#system-architecture)
3. [MongoDB Data Model](#mongodb-data-model)
4. [Installation & Startup](#installation--startup)
5. [Usage & Operations](#usage--operations)
6. [Incident Demo Scenario (Incident Lifecycle)](#incident-demo-scenario-incident-lifecycle)
7. [Verification Results (Real Data)](#verification-results-real-data)
8. [Mapping to the Organizers' Technical Requirements](#mapping-to-the-organizers-technical-requirements)
9. [Evidence Catalog & Screenshot Guide](#evidence-catalog--screenshot-guide)
10. [Automated Tests](#automated-tests)
11. [Declaration of Reused Components, Datasets & AI Tools](#declaration-of-reused-components-datasets--ai-tools)

---

## Project Overview

ShrimpWatch simulates a farm with **6 ponds**. Environmental data is updated **every 30 seconds** (42 sensor records per cycle, about 1.4 records/second).

Data groups collected:

| Group | Metrics | Source (`metadata.source`) |
|---|---|---|
| IoT sensors | Temperature, pH, dissolved oxygen (DO), **salinity**, **ORP** | `sensor` |
| Operating equipment | Aerator power (pump_power), aerator status (pump_status) | `sensor` |
| Manual entry | Alkalinity, **total ammonia nitrogen (TAN)**, **nitrite** from test kits, 4 times a day | `manual` |
| Automatically derived | Dissolved CO₂ (pH + alkalinity), **toxic NH₃** (TAN + pH + temperature), **24h pH swing** (aggregation `$group` max − min) | `derived` |

### Additional Metrics

| Metric | Unit | Source | Operational meaning | Alert threshold |
|---|---|---|---|---|
| `salinity` | ppt | sensor | Salinity drops after heavy rain → shrimp go into shock, molting goes wrong | < 10 ppt |
| `orp` | mV | sensor | A proxy for the pond bottom: ORP falls together with DO when the aerator fails → anaerobic decomposition, H₂S risk | < 100 mV |
| `tan` | mg/L | manual | Total ammonia from the test kit, input for the NH₃ calculation | — |
| `nitrite` | mg/L | manual | NO₂⁻ causes oxygen deficiency in shrimp blood | > 1 mg/L |
| `nh3` | mg/L | derived | Toxic fraction of ammonia: `TAN × 1/(1+10^(pKa−pH))`, `pKa = 0.09018 + 2729.92/T(K)` (Emerson 1975) | > 0.1 mg/L |
| `ph_swing` | pH | derived | pH range over 24h (pipeline `$match → $group → $project`) | > 0.5 |

When the aerator fails, DO falls by ~0.1 mg/L/minute and ORP follows. An incident's `incidents.evidence` now also carries `last_orp_mv`, `nh3_mg_per_l` and `ph_swing_24h`, so technicians see the water quality context right in the Investigate step. The new metrics are in the last group of the Datadog dashboard.

**Technical highlights:**
- A MongoDB **Time Series Collection** with `expireAfterSeconds` (7-day TTL) is the single store for telemetry.
- A **`$setWindowFields` + `$derivative`** pipeline computes the oxygen drop rate (DO drop rate) over a 5-minute **time** window for all 6 ponds in **one** aggregate → the result is stored in `incidents.evidence`.
- **Two-tier data lifecycle:** raw data is kept for 7 days, hourly rollups (`$group` + `$merge`) for 1 year.
- A **4-step incident lifecycle** (Open → Investigating → Resolving → Recovered) keeps its state in MongoDB, and every change is written to the `events` collection.
- **Log ↔ Trace ↔ Metric correlation** in Datadog thanks to `ddtrace` + `python-json-logger` (every log line carries `dd.trace_id`).

---

## System Architecture

```mermaid
flowchart LR
    subgraph SIM["Simulator (6 ponds)"]
        S["app/simulator/<br/>generates metrics every 30s"]
    end
    subgraph API["FastAPI"]
        A["POST /ingest/batch<br/>POST /ingest/manual/{metric}<br/>GET /incidents · PATCH /incidents/{id}"]
    end
    subgraph W["Worker"]
        K["app/worker/<br/>$setWindowFields → DO drop rate<br/>opens/closes incidents, computes CO2"]
    end
    subgraph MONGO["MongoDB Atlas M0"]
        M1[("metrics<br/>Time Series + TTL 7d")]
        M2[("incidents")]
        M3[("events")]
        M4[("ponds")]
        M5[("metrics_hourly<br/>rollup + TTL 1 year")]
    end
    DDA["Datadog Agent<br/>DogStatsD :8125 · APM :8126 · Logs"]
    DD["Datadog Cloud<br/>Dashboard · Monitor · Logs · Traces"]

    S -->|batch of 42 records| A
    A --> M1
    A --> M2
    A --> M4
    K -->|aggregate| M1
    K --> M2
    K --> M3
    K -->|$merge every 5 minutes| M5
    A -.-> DDA
    K -.-> DDA
    S -.-> DDA
    DDA --> DD
```

**Container overview (`docker compose ps`)**

| Container | Responsibility |
|---|---|
| `shrimpwatch-api` | FastAPI — ingests data, manages incidents |
| `shrimpwatch-simulator` | Simulates 6 ponds, sends data every 30 seconds |
| `shrimpwatch-worker` | Analyzes data, detects incidents and opens/closes them |
| `shrimpwatch-mongodb` | Fallback local MongoDB (only used when `.env` does not point at Atlas) |
| `shrimpwatch-datadog` | Datadog Agent — collects metrics, logs, traces |

### Source Code Layout

The code is split into layers; each layer only calls the layer below it:

```
app/
  core/        configuration, JSON logs, Datadog (DogStatsD + span tags), UTC clock
  domain/      pure business logic, no DB/HTTP: models, CO₂/NH₃ formulas,
               incident detection rules, incident lifecycle (valid state transitions)
  db/          mongo.py (connection + collections), schema.py (collections/indexes/migrations),
               pipelines.py (aggregations), repositories/ (queries per collection)
  services/    the only place that writes data: ingest, derivations, incidents, ponds
  api/         FastAPI: main.py, schemas.py (request bodies), routers/ per resource
  worker/      loop.py (30s cycle) + pond_check.py (readings → rules → service)
  simulator/   pond_model.py (pond model), runner.py, api_client.py (only calls the HTTP API)
  cli/         init_db, seed_incident_history, demo_incident, datadog_push  →  python -m app.cli.<name>
```

```
api, worker ──► services ──► db.repositories ──► db.mongo
                    └──────► domain (pure)
simulator, cli.demo_incident ──HTTP──► api
```

- Adding a new metric: declare it in `domain/models.py`, put the formula in `domain/`, write it in `services/derivations.py`.
- Adding an alert rule: edit `domain/incident_rules.py` and add a test in `tests/unit/test_incident_rules.py` (no MongoDB needed).
- `PATCH /incidents/{id}` rejects invalid state transitions (for example `recovered → open`) with `409`.

---

## MongoDB Data Model

Database: **`shrimpwatch`** — 5 collections.

| Collection | Type | Contents |
|---|---|---|
| `metrics` | **Time Series** | `timeField: timestamp`, `metaField: metadata`, `granularity: seconds`, `expireAfterSeconds: 604800` (7 days) |
| `metrics_hourly` | Document | Hourly rollup: `min`/`max`/`avg`/`count` for each `(pond_id, metric, hour)`, 1-year TTL |
| `incidents` | Document | Incidents: `severity`, `state`, `evidence`, `actions[]`, `root_cause`, `fingerprint` (6-dimensional vector, Atlas Vector Search index `incident_fingerprint`), `similar_incidents[]` |
| `events` | Document | Event log: `incident_open`, `incident_escalated`, `incident_action`, `incident_recovered`, `incident_similar_matched`, `incident_imported`, `pump_state_change`, `scenario_start`, `scenario_clear`. Every incident event has `incident_id` at the top level |
| `ponds` | Document | Catalog of the 6 ponds, aerator status and the active simulation scenarios (`scenarios`) |

**Structure of one telemetry record (Time Series form):**

```json
{
  "timestamp": "2026-10-20T06:03:38Z",
  "metadata": {
    "farm_id": "farm-001",
    "pond_id": "pond-01",
    "device_id": "node-pond-01",
    "device_type": "water_node",
    "metric": "do",
    "source": "sensor",
    "unit": "mg/L"
  },
  "value": 7.41
}
```

### Access Patterns & Design Decisions

The system is **write-light, read-heavy**: every 30 seconds there is only 1 `insert_many` (42 records), but the worker runs ~60 analytical queries. Writes therefore stay append-only in batches, and optimization focuses on the read path:

| Decision | Reason |
|---|---|
| **No hand-rolled bucket pattern** | A Time Series Collection *is* the bucket pattern, managed by MongoDB (`system.buckets.metrics`, columnar compression). Bucketing by hand would duplicate it and lose the engine's optimizations. |
| **1 compound index `pond_metric_ts`** = `(metadata.pond_id, metadata.metric, timestamp)` | Follows the ESR rule: every worker query is pond (=) + metric (=) + time range. It replaces 3 old single-field indexes (the planner could only use 1). |
| **Time-based windows** (`range` + `unit`) instead of record-count windows | When a sensor drops packets, the window is still exactly 5 minutes and `$derivative` still gives the correct rate. |
| **1 aggregate for all 6 ponds** (`partitionBy: pond_id`) | 1 round-trip instead of 6. |
| **Hourly rollup** (`$group` → `$merge`, idempotent) | The 24h pH swing reads ~24 rollup rows instead of ~2,880 raw points per pond every 30 seconds; long-term history survives the 7-day TTL. |
| **Incident timeline** `GET /incidents/{id}/timeline` | 1 aggregate: `$lookup` of the incident's events + the pond's aerator events before/during the incident, sorted with `$sortArray` — serves the *Investigate* step. |

Run [scripts/step6_index_and_buckets.js](scripts/step6_index_and_buckets.js) in mongosh to see which index is used, how many buckets were read and the average number of records per bucket.

### Vector Search: Finding Similar Past Incidents

When a technician moves an incident to **Investigating**, the system finds the **recovered** incidents whose water readings evolved most similarly, and shows how they were handled last time and how long it took.

- **Fingerprint without an AI model:** each incident has a 6-dimensional vector normalized to `[0, 1]` from its `evidence`: DO drop rate, aerator power, minimum DO, ORP, NH₃, 24h pH swing ([incident_fingerprint.py](app/domain/incident_fingerprint.py)). No API key and no extra library needed.
- **Atlas Vector Search index** `incident_fingerprint`: `similarity: euclidean`, `filter` on `state` → `$vectorSearch` only searches `recovered` incidents. Score = `1 / (1 + d²)`.
- **Fallback:** a plain mongod (CI, local Docker Compose) has no `$vectorSearch` → the same score formula is computed with `$reduce` in an aggregation, limited to the 500 most recent incidents. The result states `mode: vector_search | aggregation_fallback`.
- **Recording:** the top 3 suggestions are stored in `incidents.similar_incidents` and in an `incident_similar_matched` event (shown in the timeline).
- **Datadog:** metric `shrimpwatch.incident.similarity_score` (tags `pond`, `mode`, `root_cause`), `shrimpwatch.incident.similarity_search`, span tags `similarity_mode` / `similarity_top_score`; the last widget group in the dashboard.

| Open incident: aerator failure on pond-05 | Top score per history group |
|---|---|
| Aerator failure (`pump_failure`) | **0.998** |
| Night-time power outage (`pump_failure`) | 0.972 |
| Algae crash / excess NH₃ / heavy rain | 0.45 – 0.49 |

```bash
python -m app.cli.seed_incident_history          # load 15 synthetic historical incidents (idempotent)
curl "localhost:8000/incidents/<id>/similar?k=3"   # top 3 similar incidents
```

> The index can only be created on MongoDB Atlas (M0 or higher) or the `mongodb/mongodb-atlas-local` image; `init_db` skips it on a plain mongod. A new index needs about 1 minute to become ready, and the API uses the fallback in the meantime.

---

## Installation & Startup

### Prerequisites
- **Docker Desktop** (latest version).
- **Git**.
- **RAM ≥ 8 GB** (5 containers).
- **A MongoDB Atlas account** (free) — if you want to store data in the cloud.

### Step 1: Prepare the Environment

```powershell
copy .env.example .env
```

Open `.env` and fill in the values:

| Variable | Meaning | Where to get it |
|---|---|---|
| `MONGODB_URI` | MongoDB connection string | Atlas → *Connect* → *Drivers* (see **Step 4**) |
| `DD_API_KEY` | Datadog API key | Datadog → *Organization Settings* → *API Keys* |
| `DD_APP_KEY` | Datadog application key | Datadog → *Organization Settings* → *Application Keys* |
| `DD_SITE` | Datadog site domain | `datadoghq.com` (US1 region) |

### Step 2: Start the System

```powershell
docker compose up -d --build
docker compose ps
```

*All 5 services must be **Up**.*

### Step 3: Initialize the Data

```powershell
docker compose exec -T api python -m app.cli.init_db
docker compose exec -T api python -m app.cli.seed_incident_history   # incident history for Vector Search
```

This creates the **Time Series Collection + TTL index** and seeds the 6 ponds. When the `simulator` container starts, it backfills the missing historical data (up to **6 hours**, counted from the latest record) so the dashboard and monitors have baseline data to analyze right away; a restart does not overwrite existing data.

### Step 4: Point the Data at MongoDB Atlas (Cloud)

1. **Create a cluster:** [MongoDB Atlas](https://cloud.mongodb.com) → *Create* → **M0 (Free)** tier → *Singapore* region → *Create Deployment*.
2. **Create a database user:** enter a *Username* / *Password* → *Create User* (**save these credentials**).
3. **Network Access:** *Add entries* → *Allow access from anywhere* (`0.0.0.0/0`) → *Finish and Close*.
   > Required — without this step the containers' IP is blocked, even though the API health check may still report `ok` because an old connection is still alive.
4. **Get the connection string:** *Connect* → *Drivers* → copy `mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority`.
5. **Put it in `.env`** and restart:

```powershell
docker compose up -d --build
```

> **Note:** a `mongodb+srv://` string needs the `dnspython` library — it is already in [requirements.txt](requirements.txt).

### Running on GitHub Codespaces (free, for demos)

Nothing to install locally: the repo ships a [.devcontainer/](.devcontainer/), and the Codespace builds and runs the whole stack by itself.

1. *(Optional)* In the repo's **Settings → Secrets and variables → Codespaces**: add `MONGODB_URI` (Atlas), `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE`. Without them → MongoDB runs in a container and the Datadog Agent does not run.
2. **Code → Codespaces → Create codespace on main**. The first start takes a few minutes (image build).
3. **Ports** tab → port `8000` → open `/docs` or `/control`. To share the link: right-click → *Port Visibility → Public* (the API has no authentication yet, so only make it Public during the demo).

> Datadog monitors filter by `pond`, not by `env`: if you set `DD_API_KEY`, do not run the local stack and the Codespace at the same time, otherwise data from the two simulators gets mixed on the dashboard and monitors.

> A Codespace stops by itself after at most 4 hours without activity in the editor/terminal; when reopened, the stack starts again automatically. Personal accounts get 120 free core-hours/month (~60 hours on a 2-core machine). Remember to **Stop** the codespace after the demo.

---

## Usage & Operations

### 5.1. API (Swagger UI)
Go to [http://localhost:8000/docs](http://localhost:8000/docs):

| Endpoint | Function |
|---|---|
| `GET /healthz` | Checks the API + MongoDB connection |
| `POST /ingest/batch` | Writes a batch of sensor data |
| `POST /ingest/manual/{metric}` | Manual test-kit entry: `alkalinity`, `tan`, `nitrite` |
| `GET /ponds` | List of the 6 ponds + aerator status |
| `POST /ponds/{pond_id}/pump/fail?failed=true` | Marks an aerator as failed / restores it |
| `GET /ponds/readings?window_min=10` | Latest sensor values of each pond within the last `window_min` minutes (default 10, max 360) |
| `POST /ponds/{pond_id}/scenarios` | Starts a metric-offset scenario: `{"scenario": "do\|temperature\|ph\|salinity", "offset": -3, "duration_min": 15}` |
| `DELETE /ponds/{pond_id}/scenarios[/{scenario}]` | Stops one or all scenarios of a pond |
| `GET /ponds/scenarios/catalog` | Offset limits of each scenario and the alert thresholds |
| `GET /control` | Simulator control panel (see section 5.2) |
| `GET /incidents?state=open` | Queries incidents by state |
| `PATCH /incidents/{id}?state=...&action=...` | Updates the incident lifecycle |

```powershell
curl http://localhost:8000/healthz
# {"status":"ok","mongo":true,"farm_id":"farm-001","env":"development", ...}
```

### 5.2. Simulator Control Panel

Open [http://localhost:8000/control](http://localhost:8000/control) to create abnormal behavior for each pond without calling the API by hand:

- **Aerator power**: turns an aerator on/off (uses `POST /ponds/{id}/pump/fail`) → `pump_power` goes to 0 W, DO and ORP drop, and the worker opens a `pump_failure` incident.
- **Environment scenarios**: offsets DO, temperature, pH or salinity from normal levels for 5–120 minutes. Quick buttons are available: *Low oxygen*, *Heat wave*, *pH spike*, *Heavy rain*.
- The page refreshes every 5 seconds: latest metrics (highlighted when over a threshold), running scenarios, open incidents.

The simulator applies changes on its next send (30 seconds). Scenarios expire by themselves; **Restore all ponds** turns the aerators back on and clears every scenario. The API has no authentication, so do not expose port 8000 to the Internet.

### 5.3. Browsing Raw Data with MongoDB Compass

- **Atlas (Cloud):** paste the `mongodb+srv://...` string into Compass → database `shrimpwatch`.
- **Local Docker:** `mongodb://shrimp_app:shrimp_pass@localhost:27017/shrimpwatch?authSource=shrimpwatch`

### 5.4. Monitoring in Datadog

| Screen | Path |
|---|---|
| Dashboard | Datadog → *Dashboards* → **ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng** (the dashboard name is in Vietnamese) |
| Monitor | Datadog → *Monitors* → *Manage Monitors* → filter by tag `shrimpwatch` |
| Logs | Datadog → *Logs* → query `service:shrimpwatch` |
| Traces | Datadog → *APM* → *Traces* → service `shrimpwatch` |

Monitors in [datadog/monitors/](datadog/monitors/) (same thresholds as `app/core/config.py` and the worker's incident rules):

| Monitor | Condition (5-minute average, by `pond`) | Matching worker incident |
|---|---|---|
| Aerator failure | `pump_power` < 50 W | `pump_failure` (critical) |
| Fast DO drop | DO down > 0.25 mg/L compared with 5 minutes earlier | `do_anomaly_drop` (warning) |
| Low DO | DO < 4 (warning), < 3 (critical) | `do_low`, `do_critical` |
| Temperature out of range | outside 26–32 °C | `temperature_out_of_range` (warning) |
| pH out of range | outside 7.5–8.5 | `ph_out_of_range` (warning) |
| Low salinity | < 10 ppt | `salinity_low` (warning) |

Monitors evaluate a **full 5-minute** average and Datadog receives data a few tens of seconds late, so when demoing an aerator failure keep the aerator off for **at least 8 minutes** before the `pump_power < 50 W` monitor switches to Alert (the worker opens the incident earlier, based on the MongoDB data).

The **Sự cố ĐANG MỞ** (open incidents) widget reads the gauge `shrimpwatch.incident.active` (0/1 per pond, sent by the worker every 30-second cycle), so the number drops as soon as an incident recovers. The counters `shrimpwatch.incident.open` / `incident.recovered` are still used to count how many times incidents opened / recovered.

A pond has at most one open incident. When a more severe cause appears, the incident is escalated and an `incident_escalated` event is written; incidents are never downgraded, so they keep the most severe cause until they recover.

`python -m app.cli.datadog_push` updates monitors/dashboards with the same name, and creates them if they do not exist yet.

---

## Incident Demo Scenario (Incident Lifecycle)

The core feature, showing MongoDB and Datadog working together.

| Step | State | Action |
|---|---|---|
| 1. **Detect** | `open` | The worker detects an aerator failure (`pump_power` 5-minute average < 50W) → opens a **CRITICAL** incident, `root_cause = pump_failure` |
| 2. **Investigate** | `investigating` | The technician checks the aerator's power supply and records an action note. **Vector Search** suggests the 3 most similar past incidents with how they were handled and how long recovery took |
| 3. **Resolve** | `resolving` | Replace the parts, restore aerator power. The worker keeps tracking the incident: if the fault persists, the worker updates this same incident (no duplicate incident is opened) |
| 4. **Recover** | `recovered` | The aerator runs again and DO is stable (5 minutes) → **the worker closes** the incident by itself from `open`/`investigating`/`resolving`. Every transition writes an event to `events` |

Run the automated demo (acts on pond 3):

```powershell
docker compose exec -T api python -m app.cli.demo_incident
```

Actual verified output (incident `inc-477e03104c`):

```text
06:07:59  Injecting pump failure on pond-03  (pump_power → 0 W)
06:08:05  inc-477e03104c  state=open          severity=critical  root_cause=pump_failure
          evidence: {do_drop_rate: 0.0108 mg/L/phút, avg_pump_power: 0.0 W, min_do: 6.338}
06:08:05  inc-477e03104c → investigating      action="KT điện quạt, báo bảo trì"
06:08:09  inc-477e03104c → resolving          action="Thay phụ tùng, khởi động lại quạt"
06:08:12  inc-477e03104c → recovered          action="AO đã ổn định DO > 5mg/L"
```

The action notes above are the Vietnamese strings that `demo_incident` sends: "Checked aerator power, notified maintenance", "Replaced parts, restarted the aerator", "Pond stable, DO > 5 mg/L".

---

## Verification Results (Real Data)

### 0. End-to-End Verification on 31/10/2026

> The full flow was re-checked on the live system (MongoDB Atlas + Datadog) at **11:59–12:12 UTC** (18:59–19:12 Vietnam time): the [control panel](#52-simulator-control-panel) was driven with Playwright, and Datadog was checked through its API.

| Time (UTC) | Event | Source |
|---|---|---|
| 11:59:08 | Turn off the pond 1 aerator, start the **Heavy rain** scenario on pond 3 (salinity −7 ppt) | Control panel |
| 12:01:27 | `warning / do_anomaly_drop` incident opened for pond 1 (DO falling 0.07 mg/L/minute) | Worker → `incidents`, `events` |
| 12:02:27 | `warning / salinity_low` incident opened for pond 3 (5-minute average salinity 7.78 ppt) | Worker |
| 12:03:24 | **Low salinity** monitor goes *Triggered* on `pond:pond-03` | Datadog |
| 12:03:56 | **The same** pond 1 incident is escalated to `critical / pump_failure` (5-minute average power 0 W), with an `incident_escalated` event — no duplicate incident opened | Worker |
| 12:04:59 | **Fast DO drop** monitor goes *Triggered* on `pond:pond-01` | Datadog |
| 12:05:10 | Moved to `investigating` → Atlas Vector Search suggests past incident `hist-pump-01` (score 0.70) with its handling "Replaced the aerator capacitor, restarted the aerator" and a recovery time of 30 minutes | API + `$vectorSearch` |
| 12:05:44 | Click **Restore all ponds** (aerators back on, scenarios cleared) | Control panel |
| 12:07:27 | Worker closes the pond 3 incident by itself (`recovered`, event `incident_recovered`) | Worker |
| 12:09:24 · 12:10:59 | Both monitors go *Recovered* | Datadog |

API checks: invalid state transitions (`recovered → open`, `investigating → open`) return **409**; `state`/`scenario`/`metric` values outside the enum and Mongo operators in the body (`{"$where": ...}`, `{"$ne": null}`) are blocked by Pydantic with **422**; nonexistent ponds/incidents return **404**.

This verification found and fixed 2 lifecycle bugs: incidents in `resolving` were not tracked by the worker (a duplicate incident was opened, and the original never closed by itself), and state transitions without a note did not write to `events`. Both now have tests.

> Verification time: **2026-10-20 06:00–06:09 UTC** (13:00–13:09 Vietnam time), with the system running live on **MongoDB Atlas Cloud**.

### 1. Cloud Connection & Data

| Item | Result |
|---|---|
| Server | `cluster0.evhkrg4.mongodb.net` (3-node replica set, M0 tier) |
| Database | `shrimpwatch` |
| Collections | `metrics`, `incidents`, `ponds`, `events` (+ `system.buckets.metrics`) |
| Record counts | `metrics` = **24,684** · `incidents` = 8 · `events` = 17 · `ponds` = 6 |
| Time Series configuration | `timeField=timestamp`, `metaField=metadata`, `granularity=seconds`, `bucketMaxSpanSeconds=3600` |
| TTL | `expireAfterSeconds = 604800` (7 days) |
| Measured write rate | **354 records / 5 minutes** (~1.18 records/second) |
| Metrics being written | `temperature`, `do`, `ph`, `pump_power`, `pump_status`, `alkalinity`, `co2` |
| API health check | `{"status":"ok","mongo":true}` |

### 2. `$setWindowFields` Pipeline Results (MongoDB)

Excerpt from the worker's real log (5-minute window, `do_drop_rate_mg_per_l_per_min`):

| Pond | DO at window start | DO at window end | DO drop | Drop rate (mg/L/minute) |
|---|---|---|---|---|
| pond-01 | 7.842 | 7.410 | 0.432 | **0.0866** |
| pond-02 | 7.897 | 7.583 | 0.314 | 0.0630 |
| pond-03 (aerator failed) | 6.404 | 6.392 | 0.012 | 0.0024 |
| pond-04 | 7.804 | 7.497 | 0.307 | 0.0616 |
| pond-05 | 7.878 | 7.596 | 0.282 | 0.0565 |
| pond-06 | 7.981 | 7.541 | 0.440 | **0.0882** |

This result is written directly to `incidents.evidence.do_drop_rate_mg_per_l_per_min`.

### 3. Datadog Status (verified via API, 31/10/2026)

The dashboard and monitors are pushed from the JSON in the repo with `python -m app.cli.datadog_push` (updated by name, no duplicates created).

Names are shown exactly as they appear in Datadog (in Vietnamese), so you can search for them.

| Type | ID | Name | Result on 31/10 |
|---|---|---|---|
| Dashboard | `896-5wb-wzs` | ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng | Every metric in the dashboard has data |
| Monitor | `339119277` | [ShrimpWatch] CRITICAL: Quạt nước hỏng (Pump failure) - OXY có nguy cơ sụt | `pump_power < 50 W` 5-minute average by `pond` |
| Monitor | `339209639` | [ShrimpWatch] WARNING: DO giảm nhanh (> 0.25 mg/L trong 5 phút) | **Triggered** 12:04:59 on `pond:pond-01`, **Recovered** 12:10:59 |
| Monitor | `339209647` | [ShrimpWatch] DO thấp (Warning dưới 4, Critical dưới 3 mg/L) | OK |
| Monitor | `339209657` | [ShrimpWatch] WARNING: pH ngoài ngưỡng 7.5-8.5 | OK |
| Monitor | `339209664` | [ShrimpWatch] WARNING: Độ mặn thấp (dưới 10 ppt) | **Triggered** 12:03:24 on `pond:pond-03`, **Recovered** 12:09:24 |
| Monitor | `339209670` | [ShrimpWatch] WARNING: Nhiệt độ nước ngoài ngưỡng 26-32 °C | OK |

Logs reach Datadog: the query `service:shrimpwatch` (last 30 minutes) returns records with `dd.trace_id` — confirming that **Log Management + Log↔Trace correlation** works.

### 4. Automated Tests

**106 tests PASSED** (unit + integration on a local MongoDB 7), coverage **78.8%** (CI floor 64%), `ruff` clean — 31/10/2026. The record of the first run (9 tests, 07/10) is kept in [screenshots_pytest.txt](screenshots_pytest.txt).

---

## Mapping to the Organizers' Technical Requirements

### 6.1. MongoDB — Operational Data Platform

#### Required (6/6 criteria)

| # | Organizers' requirement | How ShrimpWatch meets it | Evidence (source code / screenshots) |
|---|---|---|---|
| 1 | **PRIMARY platform** | All sensor data, incidents and operating history are stored on **MongoDB Atlas (Cloud)** | [13_mongodb_atlas_cluster.png](assets_proof/13_mongodb_atlas_cluster.png) · [14_mongodb_atlas_data.png](assets_proof/14_mongodb_atlas_data.png) |
| 2 | **Time Series Collections** | Telemetry (DO, pH, temperature, aerator) is stored in the Time Series Collection `metrics` (`timeField`, `metaField`, `granularity`) | [01_mongo_timeseries_ttl.txt](assets_proof/01_mongo_timeseries_ttl.txt) · [schema.py](app/db/schema.py#L38-L68) |
| 3 | **Aggregation & Window Functions** | `$setWindowFields` + `$derivative` compute the DO drop rate over a 5-minute time window; `$group` + `$merge` build the hourly rollup; `$lookup` + `$sortArray` build the incident timeline | [pipelines.py](app/db/pipelines.py) (`compute_do_drop_rate_pipeline`, `rollup_hourly_pipeline`, `incident_timeline_pipeline`) |
| 4 | **expireAfterSeconds (TTL)** | TTL automatically deletes raw telemetry after 7 days (`604800` seconds); the `metrics_hourly` rollup is kept for 1 year | [01_mongo_timeseries_ttl.txt](assets_proof/01_mongo_timeseries_ttl.txt) · [schema.py](app/db/schema.py) (`init_collections`) |
| 5 | **Efficient modeling & access** | Time Series (engine-managed buckets), ESR compound index `pond_metric_ts`, 1 aggregate for all 6 ponds, rollups instead of scanning raw data — see [Access Patterns & Design Decisions](#access-patterns--design-decisions) | [schema.py](app/db/schema.py) · [pipelines.py](app/db/pipelines.py) · [step6_index_and_buckets.js](scripts/step6_index_and_buckets.js) |
| 6 | **Tied closely to operations** | MongoDB drives the entire incident state machine (`open → investigating → resolving → recovered`) | [04_swagger_ui_incidents.png](assets_proof/04_swagger_ui_incidents.png) · [06_mongodb_document_incident.png](assets_proof/06_mongodb_document_incident.png) |

#### Optional

| Organizers' requirement | How ShrimpWatch meets it | Evidence |
|---|---|---|
| Event-driven processing | Every incident state change is written to the `events` collection (keyed by `incident_id`) and rebuilt into a timeline via `GET /incidents/{id}/timeline` | [05_mongodb_event_log.png](assets_proof/05_mongodb_event_log.png) |
| **Atlas Vector Search** | `$vectorSearch` over each incident's water quality fingerprint → suggests handling steps from past incidents in the Investigate step (with an aggregation fallback) | [incident_fingerprint.py](app/domain/incident_fingerprint.py) · [pipelines.py](app/db/pipelines.py) (`similar_incidents_pipeline`) |
| Change Streams / extensions | The data structure is ready for Change Streams integration for real-time notifications | [app/db/](app/db) |

### 6.2. Datadog — Observability & Alerting

#### Required (4/4 criteria)

| # | Organizers' requirement | How ShrimpWatch meets it | Evidence |
|---|---|---|---|
| 1 | **At least 1 dashboard** | Health overview dashboard for the 6 ponds: incident KPIs, temperature, DO, pH, alkalinity, CO₂, aerator power, heatmap | [07_datadog_dashboard.png](assets_proof/07_datadog_dashboard.png)   [07_datadog_dashboard_2.png](assets_proof/07_datadog_dashboard_2.png)   [07_datadog_dashboard_3.png](assets_proof/07_datadog_dashboard_3.png)   · [shrimpwatch_overview.json](datadog/dashboards/shrimpwatch_overview.json) |
| 2 | **At least 1 monitor/alert** | **CRITICAL** monitor when an aerator fails (`pump_power < 50W`) and **WARNING** when oxygen drops abnormally | [08_datadog_monitor_alert.png](assets_proof/08_datadog_monitor_alert.png) · [pump_failure_critical.json](datadog/monitors/pump_failure_critical.json) |
| 3 | **Additional capabilities** | Metrics (DogStatsD), Log Management, APM and Distributed Tracing integrated together | [09_datadog_correlation_log_trace_metric.png](assets_proof/09_datadog_correlation_log_trace_metric.png) |
| 4 | **Connected Metrics/Logs/Traces** | Every log carries `dd.trace_id`; spans are tagged with `pond`, `device_type` → investigate from the alert down to the line of code | [09_datadog_correlation_log_trace_metric.png](assets_proof/09_datadog_correlation_log_trace_metric.png) · [log.py](app/core/log.py) |

#### Advanced Optional

| Organizers' requirement | How ShrimpWatch meets it | Evidence |
|---|---|---|
| Agent Observability | Monitors Datadog Agent health via `agent status` and `http_check` | [03_agent_status.png](assets_proof/03_agent_status.png)    [03_agent_status_2.png](assets_proof/03_agent_status_2.png)|
| AI Observability | Uses Watchdog Insights to detect anomalies in logs | [09_datadog_correlation_log_trace_metric.png](assets_proof/09_datadog_correlation_log_trace_metric.png) |

---

## Evidence Catalog & Screenshot Guide

All screenshots are stored in the **`assets_proof/`** folder with exactly the file names below.

| # | File name | What it shows |
|---|---|---|
| 1 | `01_mongo_timeseries_ttl.txt` | Time Series + TTL configuration, indexes and the Vector Search index, read directly from Atlas (31/10/2026) |
| 2 | `02_pytest_9_xanh.png` | First test run (9/9 passed, 07/10); current result: 106 passed — see [Automated Tests](#automated-tests) |
| 3 | `03_agent_status.png` | Datadog Agent status |
| 4 | `04_swagger_ui_incidents.png` | Incident management UI (Incident Management) |
| 5 | `05_mongodb_event_log.png` | Event log (`events` collection) |
| 6 | `06_mongodb_document_incident.png` | Document structure of an incident (with `evidence`, `actions`) |
| 7 | `07_datadog_dashboard.png` | Dashboard — Part 1: KPIs & system health |
| 8 | `07_datadog_dashboard_2.png` | Dashboard — Part 2: Temperature, DO, pH |
| 9 | `07_datadog_dashboard_3.png` | Dashboard — Part 3: Alkalinity, CO₂, aerators, heatmap |
| 10 | `08_datadog_monitor_alert.png` | Red alert (Alert) when an aerator fails |
| 11 | `09_datadog_correlation_log_trace_metric.png` | Log ↔ Trace ↔ Metric tracing |
| 12 | Demo video (link in [Submission Information](#submission-information)) | Demo video of the complete incident lifecycle |
| 13 | `13_mongodb_atlas_cluster.png` | MongoDB Atlas cluster (Cloud, M0 tier) up and running |
| 14 | `14_mongodb_atlas_data.png` | Sensor data from the 6 ponds flowing live into Atlas |
| 15 | `15_control_panel_incident.png` | Control panel on 31/10: pond 1 with a critical incident (aerator at 0 W, being resolved), pond 3 with a salinity warning at 7.8 ppt |

### How to Capture Each Screenshot

**1. `01_mongo_timeseries_ttl.txt` — Time Series + TTL** (a copy read directly from Atlas already exists; you can take extra screenshots with the steps below)
1. Atlas → **Database** → cluster0 → **Browse Collections** → the **`>_` (Open MongoDB Shell)** button.
2. Paste the entire content of [scripts/step4_check_ts_ttl_ponds.js](scripts/step4_check_ts_ttl_ponds.js) and press Enter.
3. Capture the printed output: `timeseries.timeField`, `metaField`, `granularity` and `expireAfterSeconds TTL : 604800 (~ 7 ngày)` (7 days).

**2. `02_pytest_9_xanh.png` — Tests**
1. Run: `make test` (runs against the `shrimpwatch_test` database in the local MongoDB container, never touches Atlas)
2. Capture the `9 passed` line.
> **Warning:** the test suite calls `init_collections(force=True)`, so it **wipes every collection** in the connected database. `make test` already overrides `MONGODB_URI`/`MONGODB_DB` to point at the local MongoDB; do not run `pytest` directly while `.env` points at Atlas.

> **CI (GitHub Actions):** every PR and every push to `main` runs 3 jobs — `Lint (ruff)` (= `make lint`), `Tests (MongoDB 7)` with a minimum coverage of 64% ([.coveragerc](.coveragerc)), and `Compose smoke test` (brings up the real stack and checks that data flows into `metrics` / `metrics_hourly`). See [.github/workflows/ci.yml](.github/workflows/ci.yml).

**3. `03_agent_status.png` — Datadog Agent**
```powershell
docker compose exec -T datadog-agent agent status
```
Capture the top of the report (Agent version, Running Checks, `http_check`).

**4. `04_swagger_ui_incidents.png` — Incident Management**
1. Open [http://localhost:8000/docs](http://localhost:8000/docs).
2. Expand the `GET /incidents` endpoint → **Try it out** → **Execute**.
3. Capture the Response area showing the list of incidents.

**5. `05_mongodb_event_log.png` — Event Log**
1. Atlas → **Browse Collections** → `shrimpwatch` → `events`.
2. Sort by `timestamp` descending (Sort desc).
3. Capture a few `incident_open` / `incident_action` / `incident_recovered` documents.

**6. `06_mongodb_document_incident.png` — Incident document**
1. Atlas → `shrimpwatch` → `incidents`.
2. Filter `_id` = `inc-477e03104c` (or the latest incident with `severity: critical`).
3. Expand the `evidence` field (contains `do_drop_rate_mg_per_l_per_min`) and the `actions` array (4 steps), then capture.
> Run `docker compose exec -T api python -m app.cli.demo_incident` first so that `actions` has all 4 steps.

**7–9. `07_datadog_dashboard*.png` — Dashboard**
1. Datadog → **Dashboards** → **ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng**.
2. Set the time range to **Past 4 Hours**, variables `pond = *`, `device_type = *`.
3. Scroll and capture in turn:
   - **Part 1:** the *Tổng quan hệ thống* (system overview) group (4 KPI tiles: Open incidents, Recovered, Aerator failures, Records ingested/minute).
   - **Part 2:** the *Chỉ số theo dõi từng ao* (per-pond metrics) group (Temperature, DO, pH).
   - **Part 3:** the *Độ kiềm (nhập tay), CO2 (tính toán) và quạt nước* (alkalinity, CO2 and aerators) group (with the per-pond DO heatmap).

**10. `08_datadog_monitor_alert.png` — Red alert**
1. Datadog → **Monitors** → **Manage Monitors** → filter by tag `shrimpwatch`.
2. Open the monitor **[ShrimpWatch] CRITICAL: Quạt nước hỏng (Pump failure)** (ID `334608203`) and check its current status.
3. If the monitor is `OK`, trigger a real alert again with the command below, then wait **6–7 minutes** (5-minute alert window + 90 seconds of `evaluation_delay`):
   ```powershell
   docker compose exec -T api python -c "import httpx;print(httpx.post('http://localhost:8000/ponds/pond-05/pump/fail',params={'failed':'true'}).json())"
   ```
   The worker then also opens a new CRITICAL incident for pond 5 → more data for the `06_mongodb_document_incident.png` screenshot.
4. Capture the red status area + the `pump_power` value chart + the monitor's message.
5. When done, restore the aerator: `POST /ponds/pond-05/pump/fail?failed=false`.

**11. `09_datadog_correlation_log_trace_metric.png` — Log ↔ Trace ↔ Metric**
1. Datadog → **Logs** → query: `service:shrimpwatch`
2. Open a log whose `trace_id` is not `0` (for example an `ingested batch` or `DO window computed` line).
3. In the detail panel, click the **Trace** tab (or the *Trace* icon next to `dd.trace_id`) to jump to APM.
4. Capture both layers: the log list + the opened trace panel.
> To cross-check the metric: open **Metrics → Explorer**, query `shrimpwatch.metric.do` and capture it too.

**12. `ShrimpWatch_Lifecycle.mp4` — Video**
1. Start screen recording.
2. Run `docker compose exec -T api python -m app.cli.demo_incident` (the log shows all 4 steps).
3. Open the Datadog Monitor tab and Atlas → `incidents` side by side to see the state change in real time.

**13. `13_mongodb_atlas_cluster.png` — Atlas cluster**
1. Atlas → **Database** → cluster list.
2. Capture the **cluster0** card with the **M0 / Free** tier label, the region and the *Connect* button.

**14. `14_mongodb_atlas_data.png` — Data in the cloud**
1. Atlas → **Browse Collections** → `shrimpwatch` → `metrics`.
2. Capture the list of the latest documents (with a `timestamp` a few seconds old and `metadata.pond_id` for all 6 ponds).

---

## Automated Tests

```bash
make test                      # everything (unit + integration) on the test DB of the local MongoDB
pytest tests/unit              # unit tests only, no MongoDB needed
```

- `tests/unit/`: water chemistry formulas, incident detection rules, incident lifecycle, simulator model, pipeline structure.
- `tests/integration/`: run against a real MongoDB through the API and the worker.

The integration tests cover: health check, collection seeding, Time Series + TTL configuration, sensor data ingestion, manual alkalinity/TAN/nitrite entry, the `$setWindowFields` pipeline, the pH swing pipeline, the CO₂ and NH₃ derivation formulas, DO/ORP dropping when an aerator fails, opening an incident on aerator failure and the 4-state lifecycle (including a `resolving` incident being closed by the worker), Vector Search and the control panel — **106 tests PASSED**, coverage 78.8%.

> **Important:** the `setup_db` fixture calls `init_collections(force=True)` → it **deletes every collection** in the connected database. Do not run it while `.env` points at a MongoDB Atlas database that holds the reporting data.

---


## Declaration of Reused Components, Datasets & AI Tools

As required by the contest rules, the team declares the parts that existed before the build period, the libraries, the data and the AI tools used.

### Code Written Before the Build Period (17/10 – 31/10/2026)

| When | What already existed | Notes |
|---|---|---|
| 07/10 – 09/10/2026 | Prototype v1.0 (the first commits of the repo): FastAPI ingest API, 6-pond simulator, worker computing the DO drop rate with `$setWindowFields`, Time Series Collection + TTL, metrics/logs/traces pushed to Datadog, a basic 4-state incident lifecycle, 9 tests | Written as one file per part (`app/db.py`, `app/worker.py`, `app/simulator.py`, …). Full history in `git log` |

Done during the build period (20/10 – 31/10/2026): moved to MongoDB Atlas; split the architecture into layers (`core`/`domain`/`db`/`services`/`api`/`worker`); new metrics (salinity, ORP, TAN, nitrite, NH₃, 24h pH swing) and the hourly `$merge` rollup; incident state transition validation (409) and incident escalation without opening duplicates; **Atlas Vector Search** suggesting similar incidents; the **simulator control panel** and scenario API; 6 environment monitors + a dashboard pushed as upserts; CI (lint, MongoDB 7 tests, Docker Compose smoke test); the test suite grew from 9 to 106.

### Open-Source Libraries & Components

Used as-is under each project's license, with no changes to their source code: FastAPI, Uvicorn, Pydantic / pydantic-settings, PyMongo / Motor, dnspython, python-json-logger, datadog (DogStatsD), ddtrace, httpx, python-dotenv, pytest, ruff (see [requirements.txt](requirements.txt), [requirements-dev.txt](requirements-dev.txt)). Official Docker images: `mongo:7`, `gcr.io/datadoghq/agent:7.59.0`, `python:3.11-slim`. Swagger UI is served by FastAPI.

### Data

- **No real data or external datasets are used.** All sensor readings are generated by the simulator ([pond_model.py](app/simulator/pond_model.py)); the incident history used for Vector Search is synthetic data marked `notes.synthetic = true` ([seed_incident_history.py](app/cli/seed_incident_history.py)).
- The free NH₃ formula follows Emerson et al. (1975), *Aqueous ammonia equilibrium calculations*; CO₂ is derived from pH and alkalinity using the carbonate equilibrium. Operating thresholds (DO, pH, salinity, temperature) follow general recommendations for whiteleg shrimp farming.

### AI Tools

| Tool | Used for |
|---|---|
| **Claude Code** (Anthropic, Claude Opus model) | Help writing and refactoring code, writing tests, review and end-to-end testing (Playwright), updating documentation. Commits with AI assistance carry `Co-Authored-By: Claude` |
| **GitHub Copilot code review** | Pull request review following [.github/copilot-instructions.md](.github/copilot-instructions.md) |
| **DeepSeek** (GitHub Action) | Automated review on pull requests |

The application **does not call any AI model at runtime**: the Vector Search fingerprint is a 6-dimensional vector computed by a formula from `evidence`. The team has read, checked and understood all AI-suggested code.

---

## Technology Stack

- **Backend:** FastAPI 0.115, Pydantic v2, Uvicorn.
- **Database:** MongoDB Atlas M0 — Time Series Collections, Aggregation Pipeline (`$setWindowFields`, `$group`), TTL Index, Replica Set.
- **Observability:** Datadog Agent 7.59 — DogStatsD metrics, APM (`ddtrace`), Log Management, Distributed Tracing.
- **Infrastructure:** Docker, Docker Compose, Makefile.

---

**Wishing you a brilliantly successful Hackathon!**
