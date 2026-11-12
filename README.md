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
| Demo video | https://youtu.be/_Oug2B-jZic |
| Presentation slides | https://docs.google.com/presentation/d/1MfH-SyYHznrgjQy6uKjYE9oANiLmH-QW/edit?usp=sharing |
| Demonstrated incident | Pond 1 aerator fails → DO drops → a **CRITICAL `pump_failure`** incident opens → Investigate → Resolve → the worker closes the incident by itself once readings are stable. Secondary scenario: heavy rain pushes pond 3 salinity below 10 ppt. Details: [Demo scenario](#incident-demo-scenario-incident-lifecycle), [31/10 verification](#0-end-to-end-verification-on-31102026) |
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
9. [Automated Tests](#automated-tests)
10. [Declaration of Reused Components, Datasets & AI Tools](#declaration-of-reused-components-datasets--ai-tools)

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
| `tan` | mg/L | manual | Total ammonia from the test kit, input for the NH₃ calculation. Warning at 1 mg/L: below the ≈1.45 mg/L juvenile safe level reported for *L. vannamei*, above the stricter limits for larvae/postlarvae (0.4–0.8 mg/L); NH₃ stays the toxicity check | > 1 mg/L |
| `nitrite` | mg/L | manual | NO₂⁻ causes oxygen deficiency in shrimp blood | > 1 mg/L |
| `nh3` | mg/L | derived | Toxic fraction of ammonia: `TAN × 1/(1+10^(pKa−pH))`, `pKa = 0.09018 + 2729.92/T(K)` (Emerson 1975) | > 0.1 mg/L |
| `ph_swing` | pH | derived | pH range over 24h (pipeline `$match → $group → $project`) | > 0.5 |
| `do.minutes_to_critical` | minutes | worker gauge | **DO forecast**: minutes until DO reaches 3 mg/L if it keeps falling at the `$derivative` rate of the last 5 minutes (`(last DO − 3) / drop rate`). Added to the incident description ("Dự báo ≈ 18 phút nữa DO < 3 mg/L") and to `incidents.evidence.minutes_to_do_critical` | ≤ 30 min |

When the aerator fails, DO falls by ~0.1 mg/L/minute and ORP follows. An incident's `incidents.evidence` now also carries `last_orp_mv`, `nh3_mg_per_l` and `ph_swing_24h`, so technicians see the water quality context right in the Investigate step. On the Datadog dashboard, ORP sits next to DO and aerator power in *Oxy và quạt nước*, and NH₃, TAN, nitrite and the pH swing are in the water and waste sections.

### Metric Rules

The worker checks every pond every 30 seconds. A value outside the safe range opens an incident (or updates the pond's open one). Thresholds live in [`app/core/config.py`](app/core/config.py).

| Metric | Unit | Safe range | Incident when | Severity | Value checked |
|---|---|---|---|---|---|
| Aerator power | W | ≥ 50 | < 50 | critical (`pump_failure`) | 5-min average |
| DO | mg/L | ≥ 4 | < 4 · < 3 | warning · critical | 5-min minimum |
| DO drop rate | mg/L/min | < 0.05 | ≥ 0.05 (aerator running) | warning | 5-min window |
| Temperature | °C | 26 – 32 | outside the range | warning | 5-min average |
| pH | — | 7.5 – 8.5 | outside the range | warning | 5-min average |
| Salinity | ppt | ≥ 10 | < 10 | warning | 5-min average |
| ORP | mV | ≥ 100 | < 100 | warning | latest, last 5 min |
| NH₃ (toxic ammonia) | mg/L | ≤ 0.1 | > 0.1 | warning | derived, see below |
| CO₂ | mg/L | ≤ 10 | > 10 | warning | derived, see below |
| TAN | mg/L | ≤ 1 | > 1 | warning | latest test kit, last 24 h |
| Nitrite (NO₂) | mg/L | ≤ 1 | > 1 | warning | latest test kit, last 24 h |
| Alkalinity | mg CaCO₃/L | ≥ 120 | < 120 | warning | latest test kit, last 24 h |
| pH swing 24 h | pH | ≤ 0.5 | dashboard color only, no incident | — | derived, see below |
| Probe feed (`sensor_offline`) | — | readings keep arriving | silent > 120 s, or < 50 % of the expected readings over 5 min | warning | `$densify` + `$fill`, see below |

- **Priority** when several rules fire: aerator → DO → temperature, pH, salinity → NH₃, nitrite, TAN, CO₂, ORP, alkalinity → probe feed. A pond has one open incident; it escalates to a more severe cause but never downgrades.
- **Recovery**: the worker closes the incident when the 5-min minimum DO is above 4.5 mg/L (threshold + 0.5) and the aerator draws more than 500 W (or sends no power data).

**Derived metrics** (written back to `metrics` with `source: derived`):

| Metric | Formula | Inputs |
|---|---|---|
| CO₂ (mg/L) | `44000 × (alkalinity / 50000) × 10^(6.35 − pH)` | alkalinity: latest test kit (24 h); pH: latest sensor (30 min) |
| NH₃ (mg/L) | `TAN / (1 + 10^(pKa − pH))`, with `pKa = 0.09018 + 2729.92 / (T + 273.15)` (Emerson 1975) | TAN: latest test kit (24 h); pH, temperature T (°C): latest sensor (30 min) |
| DO drop rate (mg/L/min) | `−d(DO)/dt` over the last 5 minutes (`$setWindowFields` + `$derivative`); positive when DO falls | DO sensor, last 5 min |
| pH swing (pH) | `max(pH) − min(pH)` over the last 24 hours | hourly rollups in `metrics_hourly` |

**Probe feed per pond.** A dead water-quality probe makes a pond go quiet: no DO, pH or temperature data means no rule can fire, which looks exactly like a healthy pond. Each worker cycle one aggregate ([`sensor_coverage_pipeline`](app/db/pipelines.py)) groups the last 5 minutes of DO readings into 30-second slots, **`$densify`** adds every slot that got no reading, and **`$fill`** (`locf`) carries the last known DO into them. Counting real against added slots gives the share of expected readings that arrived (`shrimpwatch.sensor.completeness`), and the latest reading gives the seconds of silence (`shrimpwatch.sensor.silent_seconds`). A pond silent for more than 2 minutes, or below 50 %, gets a `sensor_offline` warning with the *Sửa đầu dò* remedy. When no pond sends anything, the whole feed is down rather than one probe, and the *No sensor data received* monitor reports it once instead of six incidents. Demo: turn on *Đầu dò mất kết nối* for a pond in the control panel; the simulator stops sending its water-quality readings, the aerator keeps reporting.

Example: alkalinity 148 mg/L and pH 8.10 give CO₂ = 44000 × 0.00296 × 10^(−1.75) ≈ 2.3 mg/L.

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
  cli/         init_db, demo_incident, datadog_push  →  python -m app.cli.<name>
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
| `incidents` | Document | Incidents: `severity`, `state`, `evidence`, `actions[]`, `root_cause` |
| `events` | Document | Event log: `incident_open`, `incident_escalated`, `incident_action`, `incident_recovered`, `pump_state_change`, `scenario_start`, `scenario_clear`. Every incident event has `incident_id` at the top level |
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
| **Multi-document transactions** for every incident change | The incident and its `events` entry are written in one transaction (`run_in_transaction` in [mongo.py](app/db/mongo.py): snapshot reads, majority writes, retried on a write conflict). The timeline can never miss a change, or show one that was rolled back. The state check of `PATCH /incidents/{id}` reads from the same snapshot it writes over, so two operators racing on one incident cannot both pass it: the loser gets `409`. Datadog metrics and events are sent only after the commit |
| **`$jsonSchema` validators** on `incidents` and `events` | MongoDB itself rejects an incident with an unknown `state`/`severity`, a missing `pond_id` or a non-date timestamp, whatever code writes it. `validationLevel: moderate`, so documents written before the validator are not rejected on update; applied with `collMod` by `init_db` ([schema.py](app/db/schema.py), `INCIDENT_VALIDATOR`, `EVENT_VALIDATOR`) |

Run [scripts/step6_index_and_buckets.js](scripts/step6_index_and_buckets.js) in mongosh to see which index is used, how many buckets were read and the average number of records per bucket.

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

### Optional: Datadog Database Monitoring and RUM

- **Database Monitoring (DBM) for MongoDB.** The agent's [mongo.d/conf.yaml](datadog/conf.d/mongo.d/conf.yaml) turns on DBM for the Compose MongoDB: query samples with explain plans, slow operations, collection and index stats, under *APM → Database Monitoring → MongoDB* (cluster `shrimpwatch-local`). It logs in as the read-only `datadog` user that [mongo-init.js](mongo-init.js) creates on a fresh volume. On an existing volume create it once, then restart the agent:

  ```bash
  docker compose exec mongodb mongosh -u admin -p admin_pass --authenticationDatabase admin /docker-entrypoint-initdb.d/mongo-init.js
  docker compose restart datadog-agent
  ```

  Checked with `agent check mongo` (agent 7.59, MongoDB 7.0): the check runs `[OK]` and emits DBM metadata and activity samples. Atlas M0 has no DBM; when `.env` points at Atlas, DBM watches the local container only (Atlas needs a dedicated M10+ cluster for DBM).
- **RUM for the control panel.** Create a RUM application (*Digital Experience → RUM Applications*, type JavaScript) and put its `DD_RUM_APPLICATION_ID` and `DD_RUM_CLIENT_TOKEN` in `.env`. The API then adds the Browser SDK to `/control` (service `shrimpwatch-control`, session replay with input masking). The panel's own API calls carry trace headers (`allowedTracingUrls`), so a click on *Sửa quạt nước* in a RUM session opens the backend trace of that request, down to the MongoDB transaction. Both empty, or not well formed: no RUM script at all.

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
| `GET /ponds/manual-readings` | Latest test-kit reading per pond and metric within the last 24 hours (control panel) |
| `POST /ponds/{pond_id}/scenarios` | Starts a metric-offset scenario: `{"scenario": "do\|temperature\|ph\|salinity", "offset": -3, "duration_min": 15}` |
| `DELETE /ponds/{pond_id}/scenarios[/{scenario}]` | Stops one or all scenarios of a pond |
| `POST /ponds/{pond_id}/conditions/{condition}` | Turns on a condition until it is stopped: `heavy_rain`, `hot_sun`, `overfeeding`, `algae_bloom`, `aerator_failure`, `cloudy`, `high_density`, `water_exchange`, `algae_crash`, `dirty_bottom`, `salinity_rise`, `liming`, `sensor_offline` (several can run at once). `?severity=light\|medium\|severe` (default `medium`) scales the offsets by 0.5 / 1 / 1.5. The effect builds up over the condition's ramp (5 to 30 min, aerator failure at once); liming and water exchange are one-off actions that fade out and end by themselves |
| `POST /ponds/{pond_id}/remedies/{remedy}` | A farmer's fix that ends the conditions it solves: `repair_aerator`, `clean_bottom`, `cut_feed`, `thin_stock`, `lime` |
| `DELETE /ponds/{pond_id}/conditions[/{condition}]` | Stops one or all conditions of a pond |
| `GET /ponds/scenarios/catalog` | Conditions (with ramp and duration) and the metrics each one changes, severities, remedies, scenario offset limits, alert thresholds |
| `GET /control` | Simulator control panel (see section 5.2) |
| `GET /stream/incidents` | Server-sent events from a MongoDB Change Stream on `incidents` and `events` (503 when MongoDB is not a replica set) |
| `GET /incidents?state=open` | Queries incidents by state |
| `PATCH /incidents/{id}?state=...&action=...` | Updates the incident lifecycle |
| `POST /incidents/{id}/remedies/{remedy}?note=...` | Applies a remedy to an incident (`repair_aerator`, `clean_bottom`, `cut_feed`, `thin_stock`, `lime`): recorded as an action with an `incident_action` event, the incident moves to `resolving`, and on the simulated farm the cause ends (for `repair_aerator` also the aerator flag set by `/pump/fail`). Recovery is left to the worker, from new readings |
| `GET /incidents/{id}/timeline` | The incident, its events and the pond's aerator, condition, scenario and remedy events around it, plus `suggested_remedies` for its root cause |

```powershell
curl http://localhost:8000/healthz
# {"status":"ok","mongo":true,"farm_id":"farm-001","env":"development", ...}
```

### 5.2. Simulator Control Panel

Open [http://localhost:8000/control](http://localhost:8000/control) to create abnormal behavior for each pond without calling the API by hand:

- **Conditions**: each pond has a dropdown of real-world situations; tick any number. Each option lists the metrics it raises (*Tăng*) and lowers (*Giảm*); when several run together their effects add up. The affected readings get an outline and a summary under the dropdown shows the combined change, with amounts. A condition stays on until you untick it (or press *Khôi phục ao* to clear them all).

  | Condition | Changes directly | Knock-on |
  |---|---|---|
  | Heavy rain (*Mưa lớn*) | salinity −6 ppt, temperature −2 °C, pH −0.4, alkalinity −30 | NH₃ ↓, CO₂ ↑ |
  | Hot sun (*Nắng nóng*) | temperature +4 °C, DO −1, pH +0.3 | ORP ↓, NH₃ ↑, CO₂ ↓ |
  | Overfeeding (*Cho ăn dư*) | DO −1.5, pH −0.2, TAN +1.2, nitrite +0.6 | ORP ↓, NH₃ ↑, CO₂ ↑ |
  | Algae bloom (*Tảo phát triển*) | pH +0.6, DO +1 | ORP ↑, NH₃ ↑, CO₂ ↓ |
  | Aerator failure (*Quạt nước hỏng*) | aerator stops: power 0 W, DO falls steadily | ORP ↓ |
  | Cloudy weather (*Trời âm u*) | DO −1.5, pH −0.3, temperature −1 °C | ORP ↓, NH₃ ↓, CO₂ ↑ |
  | High stocking density (*Mật độ nuôi cao*) | DO −1.5, pH −0.15, TAN +0.6, nitrite +0.2 | ORP ↓, NH₃ ↑, CO₂ ↑ |
  | Water exchange (*Thay nước*) | DO +0.5, temperature −1 °C, TAN −0.3, nitrite −0.15, alkalinity −15 | ORP ↑, NH₃ ↓, CO₂ ↓ |
  | Probe offline (*Đầu dò mất kết nối*) | the probe stops sending DO, temperature, pH, salinity, ORP; the water does not change | `sensor_offline` incident; *Sửa đầu dò* ends it |

  DO and ORP move gradually (about 20 minutes); alkalinity, TAN and nitrite are re-tested on the simulator's next send whenever a condition starts or stops, so their change shows right away. Offsets live in `app/domain/conditions.py`.
- **Incidents**: a pond with an open incident shows it as a line on its card. Click it to handle the incident in one place (the list of open and recently recovered incidents is on the Datadog dashboard):
  - **Timings**: detected N seconds after the fault (green within the 2-minute SLO), acknowledged after, recovered after.
  - **Evidence**: the DO forecast (*Dự báo ≈ N phút nữa DO < 3 mg/L*), lowest DO, drop rate, aerator power, ORP, NH₃ and the other readings stored with the incident.
  - **Handle**: the remedies suggested for the root cause (one click: recorded, incident → *Đang xử lý*), a note field, and the allowed next states. The worker closes the incident when the data shows the pond is safe.
  - **Investigate in Datadog**: the dashboard filtered to the pond (set `DD_DASHBOARD_ID`), its logs, traces and incident events, for the incident's time window.
  - **Timeline**: what broke (aerator, condition, scenario), what the operator did and when the data confirmed recovery.
- **System faults** (*Sự cố hệ thống*, above the ponds): break ShrimpWatch itself to show Datadog catching it in the **Sức khoẻ hệ thống** dashboard group. Each fault switches itself off after 10 minutes (or press *Tắt*); starting and stopping one is written to `events` and sent as a Datadog Event, drawn on the system health charts.

  | Fault | What breaks | What Datadog shows |
  |---|---|---|
  | *Worker chạy chậm* | every worker cycle stalls 30 s more (span `worker.injected_delay`): incidents open and close late | worker cycle time above 25 s, *Worker slow or stopped* monitor |
  | *API lỗi 50%* | half the API requests answer 503, sensor batches included; the panel page, `/system-faults`, `/healthz` and the live stream are spared so the fault can be switched off | API error rate above 5 %, *API error rate* monitor, error traces in APM |
  | *Mất dữ liệu cảm biến* | the simulator stops sending readings for every pond | every pond's probe share falls to 0 %, *No sensor data received* monitor; no per-pond probe incidents, since all ponds are silent at once |

  API: `GET /system-faults`, `POST /system-faults/{slow_worker|api_errors|feed_stopped}`, `DELETE /system-faults[/{fault}]`.
- **Pond filter** (header): show only the ponds you pick; each button carries the pond's status dot (green normal, amber warning, red incident). The selection is kept in the URL (`/control?ao=pond-01,pond-03`), so it survives a reload and can be shared. **Restore all ponds** still applies to every pond.
- The page refreshes every 5 seconds: latest sensor and derived metrics from the last 10 minutes, the latest test-kit readings (alkalinity, TAN, nitrite) from the last 24 hours (hover for when they were entered), highlighted when over a threshold, plus running conditions and open incidents.

The simulator applies changes on its next send (30 seconds). **Restore all ponds** clears every condition and scenario and turns the aerators back on. The per-metric scenario and aerator endpoints below are still available for scripts (`make demo` uses them). The API has no authentication, so do not expose port 8000 to the Internet.

### 5.3. Browsing Raw Data with MongoDB Compass

- **Atlas (Cloud):** paste the `mongodb+srv://...` string into Compass → database `shrimpwatch`.
- **Local Docker:** `mongodb://shrimp_app:shrimp_pass@localhost:27017/shrimpwatch?authSource=shrimpwatch&directConnection=true` (the container is a single-node replica set whose member is named `mongodb:27017`, so connect directly from the host)

### 5.4. Monitoring in Datadog

| Screen | Path |
|---|---|
| Dashboard | Datadog → *Dashboards* → **ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng** (the dashboard name is in Vietnamese). Sections, top to bottom: **Tổng quan** (ponds with an open incident, data feed per minute, which pond had an incident in the last 24 hours); **Sự cố: đang mở và đã hồi phục trong 24 giờ** (one row per open incident with its opening time and the minutes since it opened in the column of its state, one row per incident recovered in the last 24 hours with its opening and recovery times and the minutes it took to recover; a search box filters each table by pond, title, time or incident id; clicking a row opens the pond in the control panel, the incident's events, the pond's logs or traces); **Tình trạng từng ao** (one row per pond with the open incident flag and the latest value of all 12 metrics, test-kit metrics from the last 24 hours, colored green/amber/red by the thresholds in `app/core/config.py`, plus the `shrimpwatch` monitors currently in Alert or Warn); **Oxy và quạt nước** (DO, aerator power, DO change per 5 minutes as the "DO giảm nhanh" monitor sees it, ORP); **Môi trường nước** (temperature, pH, salinity, 24h pH swing); **Khí độc và chất thải** (NH₃, CO₂, TAN, nitrite, alkalinity); **Sức khoẻ hệ thống** (detection SLO, worker cycle time, API error rate, share of expected probe readings per pond). Each pond keeps the same color in every chart, threshold lines match the monitor severity, sensor charts follow the time picker and test-kit charts show 24 hours |
| Monitor | Datadog → *Monitors* → *Manage Monitors* → filter by tag `shrimpwatch` |
| Logs | Datadog → *Logs* → query `service:shrimpwatch` |
| Traces | Datadog → *APM* → *Traces* → service `shrimpwatch` |

Monitors in [datadog/monitors/](datadog/monitors/) (same thresholds as `app/core/config.py` and the worker's incident rules):

| Monitor | Condition (5-minute average, by `pond`) | Matching worker incident |
|---|---|---|
| Aerator failure | `pump_power` < 50 W | `pump_failure` (critical) |
| Fast DO drop | DO down > 0.25 mg/L compared with 5 minutes earlier | `do_out_of_range` (warning) |
| Low DO | DO < 4 (warning), < 3 (critical) | `do_out_of_range` (warning or critical) |
| Temperature out of range | outside 26–32 °C | `temperature_out_of_range` (warning) |
| pH out of range | outside 7.5–8.5 | `ph_out_of_range` (warning) |
| Low salinity | < 10 ppt | `salinity_out_of_range` (warning) |
| DO unusually low for the hour | Datadog `anomalies()` (agile, daily seasonality) of DO, below its expected band for 15 minutes, by `pond` ([do_anomaly.json](datadog/monitors/do_anomaly.json)) | The fixed rules only fire under 4 mg/L; this catches a pond still above 4 but falling away from what is normal for that hour. The *Oxy và quạt nước* group shows the same band |
| DO forecast | Datadog seasonal `forecast()` of DO (daily seasonality, so it learns the day/night cycle) reaches ≤ 4 mg/L (warning) or ≤ 3 mg/L (critical) within the next 12 hours, the shortest horizon Datadog allows; it needs a few days of history before it is meaningful | Early warning for the night-time dip; the worker's minute-level forecast opens `do_out_of_range` when ≤ 30 min remain |
| Toxic NH₃ | `nh3` > 0.1 mg/L | `nh3_out_of_range` (warning) |
| Low ORP | `orp` < 100 mV | `orp_out_of_range` (warning) |
| High CO₂ | `co2` > 10 mg/L | `co2_out_of_range` (warning) |

The worker also opens a warning incident, with no Datadog monitor, when the test-kit readings cross their threshold (they arrive a few times a day, too rarely for a 5-minute monitor): `tan_out_of_range`, `nitrite_out_of_range`, `alkalinity_out_of_range`.

**System health.** These watch ShrimpWatch itself, so a silent pond never means a broken pipeline:

| Monitor / SLO | Condition | Why |
|---|---|---|
| Sensor data stopped | no `shrimpwatch.ingest.records` for 5 minutes (`default_zero`, so silence counts as 0; no-data alert as a backup) | Without data no incident can open |
| API error rate | APM `trace.fastapi.request.errors / hits` > 5% over 10 minutes (warning > 2%) | Lost sensor batches or operator actions |
| Auto-remediation: aerator stopped | event monitor on `root_cause:pump_failure` incident events, by `pond` ([auto_remediate_pump.json](datadog/monitors/auto_remediate_pump.json)) | Starts the self-healing workflow that switches on the backup aerator |
| Pond probe silent | `shrimpwatch.sensor.silent_seconds` > 120 s for 2 minutes, by `pond` ([sensor_offline.json](datadog/monitors/sensor_offline.json)) | One pond is unwatched while the others still report: the probe, not the pipeline |
| Worker slow or stopped | `shrimpwatch.worker.cycle_seconds` (sent every cycle) > 25 s, or no data for 5 minutes | Incidents would open and close late, or not at all |
| SLO: critical incidents detected within 2 minutes | `shrimpwatch.incident.detected{severity:critical,within_slo:true}` / all critical, 99% over 7 days ([datadog/slos/](datadog/slos/)) | The promise behind fast aerator detection |

The **Sức khoẻ hệ thống** dashboard group shows the SLO ratio, the worker cycle time, the API error rate and the share of expected probe readings per pond, with the control panel's system faults drawn on the charts as events. The panel's *Sự cố hệ thống* section breaks each of them on purpose (see [5.2](#52-simulator-control-panel)). The SLO only counts critical incidents with a known fault start, so it shows *No data* until an aerator failure is triggered from the panel. Every metric has one cause named `<metric>_out_of_range`; the severity says how bad it is.

Monitors evaluate a **full 5-minute** average and Datadog receives data a few tens of seconds late, so when demoing an aerator failure keep the aerator off for **at least 8 minutes** before the `pump_power < 50 W` monitor switches to Alert (the worker opens the incident earlier, based on the MongoDB data).

The **Số sự cố đang xảy ra** (active incidents) card and the **Sự cố** column of the per-pond table read the gauge `shrimpwatch.incident.active` (0/1 per pond, sent by the worker every 30-second cycle), so the number drops as soon as an incident recovers. Both take the last 1-minute bucket (`.rollup(max, 60)`, aggregator `last`): the table spans 1 day for the manual readings, and a rollup without an interval would turn the column into "any incident in the last 24 hours". The counters `shrimpwatch.incident.open` / `incident.recovered` are still used to count how many times incidents opened / recovered.

The **Sự cố: đang mở và đã hồi phục trong 24 giờ** tables read two gauges the worker sends every cycle, one series per incident, tagged `pond`, `severity`, `state`, `title`, `opened_at` (and `recovered_at`) and `incident_id`:

| Metric | Value | Sent for |
|---|---|---|
| `shrimpwatch.incident.open_minutes` | minutes since the incident opened | every open, investigating or resolving incident |
| `shrimpwatch.incident.recovered_minutes` | minutes from opened to recovered | every incident recovered in the last 24 hours |

`opened_at` / `recovered_at` are in the farm's local time (`FARM_TIMEZONE`, default `Asia/Ho_Chi_Minh`) as `dd/mm_hh:mm`. `title` is the incident title as a tag (`Cảnh báo: NH₃ độc cao` → `cảnh_báo_nh3_độc_cao`), so a title starting with `sự_cố` is critical and `cảnh_báo` a warning. The tables span the last 2 minutes and a series stops when its incident leaves the list, so a closed incident (or the old state of one that just moved on) can stay visible for up to 2 minutes. The queries end with `.fill(null)`: by default Datadog fills a gap in a series for up to 5 minutes, which would keep an ended series on the table that much longer. The row links are written with `$DD_APP_URL` and `$PUBLIC_BASE_URL` in the JSON; `datadog_push` fills them in and drops the control panel link when `PUBLIC_BASE_URL` is not set.

A pond has at most one open incident, enforced by MongoDB with a unique partial index (`one_active_incident_per_pond`: unique `pond_id` where `state` is open, investigating or resolving), because both the ingest check and the worker can open one. When a more severe cause appears, the incident is escalated and an `incident_escalated` event is written; incidents are never downgraded, so they keep the most severe cause until they recover.

**Incident timings and Datadog Events.** Every incident change also goes to Datadog, so Datadog and MongoDB show the same lifecycle:

| Signal | Type | Meaning |
|---|---|---|
| `shrimpwatch.incident.detection_delay` | distribution (s) | Fault start → incident opened. The fault start is the latest aerator failure, condition or scenario the simulator injected into the pond since its previous incident closed; real sensors have no ground truth, so nothing is sent for them |
| `shrimpwatch.incident.detected` | counter, `within_slo:true\|false` | One per incident with a known fault; `true` when detected within `DETECTION_SLO_SEC` (120 s) |
| `shrimpwatch.incident.time_to_acknowledge` | distribution (s) | Incident opened → the operator first moves it out of `open` |
| `shrimpwatch.incident.time_to_recover` | distribution (s) | Incident opened → recovered (by the worker or the operator) |
| Datadog Event per change | event, `aggregation_key` = incident id | `incident_open`, `incident_escalated`, `incident_investigating`, `incident_resolving`, `incident_recovered` (tag `incident_event`), sent through the agent, so the app needs no API key |

The incident document stores the same timings: `fault_started_at`, `detection_delay_sec`, `acknowledged_at`, `recovered_at`. The events are overlaid on the DO and aerator power charts, and each row of the incident tables links to its events.

**Farmer alert by email.** When an incident opens or escalates, the farmer gets an email, routed through Datadog so Datadog stays the one place that decides who is told. Email works on any phone without installing an app, which most farmers do not have for chat bots:

1. The incident service sends a Datadog Event whose text is written for the farmer: the severity (or *cảnh báo → nghiêm trọng* on an escalation), what is wrong (with the DO forecast when DO is falling), the suggested remedy and, when `PUBLIC_BASE_URL` is set to an address the farmer's phone can reach, a link to the pond in the control panel. Datadog email takes no HTML or CSS, so the text is laid out in Markdown (bold labels, a colour dot per severity, remedies as a list); the monitor message is only a heading, so the details are not shown twice.
2. Two event monitors, *Farmer alert: incident opened* and *…escalated* ([farmer_alert_open.json](datadog/monitors/farmer_alert_open.json), [farmer_alert_escalated.json](datadog/monitors/farmer_alert_escalated.json)), fire per pond; they are separate so an escalation a few minutes after the opening still notifies.
3. Datadog emails that text to every address in `FARMER_ALERT_EMAILS`.

Set it up: put the addresses in `.env` as `FARMER_ALERT_EMAILS=farmer@example.com,manager@example.com`, then run `python -m app.cli.datadog_push`. It adds an `@address` handle per address to monitors tagged `notify:email`, so the addresses stay in `.env` and in Datadog, never in this public repo. Invalid entries are skipped with a warning; remove an address and push again to stop its emails. No mail server is needed: Datadog sends the email. On a trial Datadog organization the email only reaches members of that organization (checked live: an org member received the alert, an outside Gmail address did not), so invite each recipient to the organization first.

`python -m app.cli.datadog_push` updates dashboards, monitors and SLOs with the same name, and creates them if they do not exist yet. It logs the dashboard id to put in `DD_DASHBOARD_ID`.

Each monitor has an English · Vietnamese name and an English message followed by the Vietnamese one. `datadog_push` finds a renamed monitor by its query, so renaming updates it instead of creating a copy.

---

## Incident Demo Scenario (Incident Lifecycle)

The core feature, showing MongoDB and Datadog working together.

| Step | State | Action |
|---|---|---|
| 1. **Detect** | `open` | The aerator reports *off* (`pump_status = 0`) in two readings in a row → the ingest API opens a **CRITICAL** incident at once, `root_cause = pump_failure`: **within 60 seconds** of the failure at one reading per 30 s. The worker's 5-minute power average (< 50 W) is the second line of defence |
| 2. **Investigate** | `investigating` | The technician checks the aerator's power supply and records an action note |
| 3. **Resolve** | `resolving` | Apply the suggested remedy (`pump_failure` → *repair aerator*; waste causes → *cut feed*, *clean bottom*; see `app/domain/remedies.py`) with one call, or replace the parts and restore aerator power by hand. The worker keeps tracking the incident: if the fault persists, the worker updates this same incident (no duplicate incident is opened) |
| 4. **Recover** | `recovered` | The aerator runs again and DO is stable (5 minutes) → **the worker closes** the incident by itself from `open`/`investigating`/`resolving`. Every transition writes an event to `events` |

### Self-healing: Datadog switches on the backup aerator

A stopped aerator kills a pond within the hour, often at night when nobody is watching. So for this one cause the system responds by itself, and Datadog is where that decision lives:

```mermaid
sequenceDiagram
    participant API as ShrimpWatch API
    participant DD as Datadog
    participant WF as Datadog Workflow
    participant W as Worker
    API->>API: aerator off twice → pump_failure incident (MongoDB transaction: incident + event)
    API->>DD: Datadog Event incident_open, root_cause:pump_failure
    DD->>WF: event monitor "Auto-remediation: aerator stopped" mentions @workflow-shrimpwatch-auto-remediate(pond_id=…)
    WF->>API: POST /automation/remediate {pond_id, run_id} + X-Automation-Token
    API->>API: backup aerator on, incident → resolving, action by datadog_workflow
    W->>W: new readings: DO back above 4.5 mg/L → recovered
```

1. **Detect**: the ingest API opens the `pump_failure` incident within 60 s and sends a Datadog Event tagged `root_cause:pump_failure`.
2. **Decide (Datadog)**: the event monitor [auto_remediate_pump.json](datadog/monitors/auto_remediate_pump.json) fires per pond and mentions the workflow's handle, passing the pond as an input.
3. **Act (Datadog Workflow Automation)**: [auto_remediate.json](datadog/workflows/auto_remediate.json) makes one HTTP request to `POST /automation/remediate`. The API checks the shared `AUTOMATION_TOKEN`, applies the pond's **automatic remedy** (only `pump_failure` → *Bật quạt dự phòng*, a remotely switched backup aerator; feeding, liming or siphoning still need a person, see `automatic_remedy` in [remedies.py](app/domain/remedies.py)) and moves the incident to *Resolving*. The call is idempotent: a workflow retry or a second alert answers `skipped: already_applied`.
4. **Verify**: the worker closes the incident only when new readings prove DO is safe, as for a farmer's remedy. The incident history in the panel labels the step *🤖 Datadog tự xử lý*, the `incident_action` event carries `by: datadog_workflow` and the workflow run id, and Datadog gets `shrimpwatch.remediation.auto` and `shrimpwatch.incident.time_to_auto_remediate`. The farmer still gets the alert email, to repair the main aerator.

Set it up: put `AUTOMATION_TOKEN` (any long random string) and `PUBLIC_BASE_URL` (an address Datadog's cloud can reach, such as the Codespace's forwarded port) in `.env`, restart the API, then run `python -m app.cli.datadog_push`, which creates or updates the workflow with the URL and token filled in, then the monitor. Without them the endpoint answers `503` and the workflow is not pushed. The token sits in the workflow's request header, visible to members of the Datadog organization; for a real farm use an HTTP connection with token auth instead.

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
| 12:01:27 | `warning / do_out_of_range` incident opened for pond 1 (DO falling 0.07 mg/L/minute) | Worker → `incidents`, `events` |
| 12:02:27 | `warning / salinity_out_of_range` incident opened for pond 3 (5-minute average salinity 7.78 ppt) | Worker |
| 12:03:24 | **Low salinity** monitor goes *Triggered* on `pond:pond-03` | Datadog |
| 12:03:56 | **The same** pond 1 incident is escalated to `critical / pump_failure` (5-minute average power 0 W), with an `incident_escalated` event — no duplicate incident opened | Worker |
| 12:04:59 | **Fast DO drop** monitor goes *Triggered* on `pond:pond-01` | Datadog |
| 12:05:10 | Moved to `investigating` → Atlas Vector Search suggests past incident `hist-pump-01` (score 0.70) with its handling "Replaced the aerator capacitor, restarted the aerator" and a recovery time of 30 minutes (this suggestion was removed later, see [Declaration](#declaration-of-reused-components-datasets--ai-tools)) | API + `$vectorSearch` |
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

**106 tests PASSED** (unit + integration on a local MongoDB 7), coverage **78.8%** (CI floor 64%), `ruff` clean — 31/10/2026.

---

## Mapping to the Organizers' Technical Requirements

### 6.1. MongoDB — Operational Data Platform

#### Required (6/6 criteria)

| # | Organizers' requirement | How ShrimpWatch meets it | Evidence (source code) |
|---|---|---|---|
| 1 | **PRIMARY platform** | All sensor data, incidents and operating history are stored on **MongoDB Atlas (Cloud)** | [mongo.py](app/db/mongo.py) · [.env.example](.env.example) (Atlas connection option) |
| 2 | **Time Series Collections** | Telemetry (DO, pH, temperature, aerator) is stored in the Time Series Collection `metrics` (`timeField`, `metaField`, `granularity`) | [schema.py](app/db/schema.py#L38-L68) |
| 3 | **Aggregation & Window Functions** | `$setWindowFields` + `$derivative` compute the DO drop rate over a 5-minute time window; `$densify` + `$fill` find the missing readings of each pond's probe; `$group` + `$merge` build the hourly rollup; `$lookup` + `$sortArray` build the incident timeline | [pipelines.py](app/db/pipelines.py) (`compute_do_drop_rate_pipeline`, `sensor_coverage_pipeline`, `rollup_hourly_pipeline`, `incident_timeline_pipeline`) |
| 4 | **expireAfterSeconds (TTL)** | TTL automatically deletes raw telemetry after 7 days (`604800` seconds); the `metrics_hourly` rollup is kept for 1 year | [schema.py](app/db/schema.py) (`init_collections`) |
| 5 | **Efficient modeling & access** | Time Series (engine-managed buckets), ESR compound index `pond_metric_ts`, 1 aggregate for all 6 ponds, rollups instead of scanning raw data — see [Access Patterns & Design Decisions](#access-patterns--design-decisions) | [schema.py](app/db/schema.py) · [pipelines.py](app/db/pipelines.py) · [step6_index_and_buckets.js](scripts/step6_index_and_buckets.js) |
| 6 | **Tied closely to operations** | MongoDB drives the entire incident state machine (`open → investigating → resolving → recovered`); each change and its `events` entry are committed in one **multi-document transaction**, and `$jsonSchema` validators guard both collections | [incident_lifecycle.py](app/domain/incident_lifecycle.py) · [incidents.py](app/services/incidents.py) |

#### Optional

| Organizers' requirement | How ShrimpWatch meets it | Evidence |
|---|---|---|
| Event-driven processing | Every incident state change is written to the `events` collection (keyed by `incident_id`) and rebuilt into a timeline via `GET /incidents/{id}/timeline` | [events.py](app/db/repositories/events.py) · [pipelines.py](app/db/pipelines.py) (`incident_timeline_pipeline`) |
| Change Streams | `GET /stream/incidents` watches `incidents` and `events` with a MongoDB Change Stream and pushes each change to the control panel as a server-sent event, so a new incident or state change shows at once (*Trực tiếp* in the header). Local Compose and CI run MongoDB as a single-node replica set; Atlas already is one. Time series collections do not support change streams, so readings are still polled | [changes.py](app/db/repositories/changes.py) · [incident_stream.py](app/services/incident_stream.py) |

### 6.2. Datadog — Observability & Alerting

#### Required (4/4 criteria)

| # | Organizers' requirement | How ShrimpWatch meets it | Evidence |
|---|---|---|---|
| 1 | **At least 1 dashboard** | Health overview dashboard for the 6 ponds: incident KPIs, a per-pond status table with the firing monitors, temperature, DO, pH, alkalinity, CO₂, aerator power, heatmap | [shrimpwatch_overview.json](datadog/dashboards/shrimpwatch_overview.json) |
| 2 | **At least 1 monitor/alert** | **CRITICAL** monitor when an aerator fails (`pump_power < 50W`) and **WARNING** when oxygen drops abnormally | [pump_failure_critical.json](datadog/monitors/pump_failure_critical.json) |
| 3 | **Additional capabilities** | Metrics (DogStatsD), Log Management, APM and Distributed Tracing integrated together; **Database Monitoring** for MongoDB, **RUM** on the control panel (linked to backend traces), **Anomaly Detection** and **Workflow Automation** (below) | [telemetry.py](app/core/telemetry.py) · [log.py](app/core/log.py) · [mongo.d](datadog/conf.d/mongo.d/conf.yaml) · [control.py](app/api/routers/control.py) |
| 4 | **Connected Metrics/Logs/Traces** | Every log carries `dd.trace_id`; spans are tagged with `pond`, `device_type` → investigate from the alert down to the line of code | [log.py](app/core/log.py) |

#### Advanced Optional

| Organizers' requirement | How ShrimpWatch meets it | Evidence |
|---|---|---|
| Agent Observability (AI agents) | Not used: ShrimpWatch runs no AI agent. (The Datadog Agent's own health is watched with `http_check` on the API, see [http_check conf.yaml](datadog/conf.d/http_check.d/conf.yaml)) | — |
| Anomaly Detection / ML-based detection | Three views of the night-time DO dip: an `anomalies()` monitor flags DO that is unusually low **for that hour** while still above the fixed 4 mg/L rule; a seasonal `forecast()` monitor looks 12 hours ahead; the worker forecasts *minutes until DO < 3 mg/L* from the MongoDB `$derivative` rate. Watchdog also scans the same metrics, logs and traces | [do_anomaly.json](datadog/monitors/do_anomaly.json) · [do_forecast.json](datadog/monitors/do_forecast.json) · [pipelines.py](app/db/pipelines.py) (`compute_do_drop_rate_pipeline`) |
| Database Monitoring | DBM for MongoDB: explain plans of the worker's `$setWindowFields` / `$densify` aggregates, slow operations, index usage of `pond_metric_ts` | [mongo.d/conf.yaml](datadog/conf.d/mongo.d/conf.yaml) |
| RUM | Browser SDK on the control panel, with trace headers on its API calls: a farmer's click links to the backend trace and its MongoDB spans | [control.py](app/api/routers/control.py) (`rum_snippet`) |
| Workflow Automation (self-healing) | A monitor on `pump_failure` incident events starts a Datadog Workflow that calls `POST /automation/remediate` to switch on the backup aerator; the worker confirms recovery from MongoDB data | [auto_remediate.json](datadog/workflows/auto_remediate.json) · [automation.py](app/api/routers/automation.py) |
| AI Observability (LLM Observability) | Not used: ShrimpWatch does not call an AI model at runtime, so there is no LLM to observe | — |

---

---

## Automated Tests

```bash
make test                      # everything (unit + integration) on the test DB of the local MongoDB
pytest tests/unit              # unit tests only, no MongoDB needed
```

- `tests/unit/`: water chemistry formulas, incident detection rules, incident lifecycle, simulator model, pipeline structure.
- `tests/integration/`: run against a real MongoDB through the API and the worker.

The integration tests cover: health check, collection seeding, Time Series + TTL configuration, sensor data ingestion, manual alkalinity/TAN/nitrite entry, the `$setWindowFields` pipeline, the pH swing pipeline, the CO₂ and NH₃ derivation formulas, DO/ORP dropping when an aerator fails, opening an incident on aerator failure and the 4-state lifecycle (including a `resolving` incident being closed by the worker) and the control panel — **106 tests PASSED**, coverage 78.8%.

> **Important:** the `setup_db` fixture calls `init_collections(force=True)` → it **deletes every collection** in the connected database. Do not run it while `.env` points at a MongoDB Atlas database that holds the reporting data.

---


## Declaration of Reused Components, Datasets & AI Tools

As required by the contest rules, the team declares the parts that existed before the build period, the libraries, the data and the AI tools used.

### Code Written Before the Build Period (from 17/10/2026)

| When | What already existed | Notes |
|---|---|---|
| 07/10 – 09/10/2026 | Prototype v1.0 (the first commits of the repo): FastAPI ingest API, 6-pond simulator, worker computing the DO drop rate with `$setWindowFields`, Time Series Collection + TTL, metrics/logs/traces pushed to Datadog, a basic 4-state incident lifecycle, 9 tests | Written as one file per part (`app/db.py`, `app/worker.py`, `app/simulator.py`, …). Full history in `git log` |

Done during the build period (20/10 – 31/10/2026): moved to MongoDB Atlas; split the architecture into layers (`core`/`domain`/`db`/`services`/`api`/`worker`); new metrics (salinity, ORP, TAN, nitrite, NH₃, 24h pH swing) and the hourly `$merge` rollup; incident state transition validation (409) and incident escalation without opening duplicates; **Atlas Vector Search** suggesting similar incidents (removed on 08/11, replaced by suggested remedies per condition); the **simulator control panel** and scenario API; 6 environment monitors + a dashboard pushed as upserts; CI (lint, MongoDB 7 tests, Docker Compose smoke test); the test suite grew from 9 to 106.

Done during the extended build period (01/11 – 15/11/2026): control panel conditions with severity, ramp-up and suggested remedies; aerator failure detected at ingest within 60 seconds; incident timings, a detection SLO and Datadog Events; the DO forecast (minutes until DO < 3 mg/L); the incident modal with remedy picker and history; **Change Streams** pushing incident changes to the panel; system-health monitors, derived-metric monitors and the farmer alert email; incident tables on the dashboard; **multi-document transactions** and `$jsonSchema` validators for incidents and events; **`$densify` + `$fill`** to detect a silent or flaky probe; self-healing through **Datadog Workflow Automation**; **Database Monitoring** for MongoDB, **RUM** on the control panel and **anomaly / forecast** monitors.

### Open-Source Libraries & Components

Used as-is under each project's license, with no changes to their source code: FastAPI, Uvicorn, Pydantic / pydantic-settings, PyMongo / Motor, dnspython, python-json-logger, datadog (DogStatsD), ddtrace, httpx, python-dotenv, pytest, ruff, vulture (see [requirements.txt](requirements.txt), [requirements-dev.txt](requirements-dev.txt)). Official Docker images: `mongo:7`, `gcr.io/datadoghq/agent:7.59.0`, `python:3.11-slim`. Swagger UI is served by FastAPI.

### Data

- **No real data or external datasets are used.** All sensor readings are generated by the simulator ([pond_model.py](app/simulator/pond_model.py)).
- The free NH₃ formula follows Emerson et al. (1975), *Aqueous ammonia equilibrium calculations*; CO₂ is derived from pH and alkalinity using the carbonate equilibrium. Operating thresholds (DO, pH, salinity, temperature) follow general recommendations for whiteleg shrimp farming.

### AI Tools

| Tool | Used for |
|---|---|
| **Claude Code** (Anthropic, Claude Opus model) | Help writing and refactoring code, writing tests, review and end-to-end testing (Playwright), updating documentation. Commits with AI assistance carry `Co-Authored-By: Claude` |
| **GitHub Copilot code review** | Pull request review following [.github/copilot-instructions.md](.github/copilot-instructions.md) |
| **DeepSeek** (GitHub Action) | Automated review on pull requests |

The application **does not call any AI model at runtime**. The team has read, checked and understood all AI-suggested code.

---

## Technology Stack

- **Backend:** FastAPI 0.115, Pydantic v2, Uvicorn.
- **Database:** MongoDB Atlas M0 — Time Series Collections, Aggregation Pipeline (`$setWindowFields`, `$group`), TTL Index, Replica Set.
- **Observability:** Datadog Agent 7.59 — DogStatsD metrics, APM (`ddtrace`), Log Management, Distributed Tracing.
- **Infrastructure:** Docker, Docker Compose, Makefile.

---

**Wishing you a brilliantly successful Hackathon!**
