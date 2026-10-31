# 🦐 ShrimpWatch — Hệ thống Giám sát Ao nuôi Tôm Thông minh

> Dự án tham gia **MUG Vietnam Mini Hackathon 2026**.
> **ShrimpWatch** là hệ thống giám sát IoT cho trang trại nuôi tôm thẻ chân trắng, kết hợp **MongoDB Atlas (Time Series)** để lưu trữ dữ liệu cảm biến và **Datadog** để quan sát, cảnh báo sự cố theo thời gian thực.

| Thành phần | Công nghệ | Vai trò |
|---|---|---|
| Cơ sở dữ liệu | **MongoDB Atlas M0** (Time Series, Aggregation, TTL) | Nền tảng dữ liệu vận hành chính |
| Observability | **Datadog** (Metrics, Logs, APM, Traces) | Dashboard, Monitor/Alert và điều tra sự cố |
| Backend | FastAPI + Pydantic v2 (Python 3.11) | Tiếp nhận dữ liệu, quản lý vòng đời sự cố |
| Hạ tầng | Docker Compose (5 container) | Đóng gói & chạy đồng nhất |

---

## 📖 Mục lục

1. [Giới thiệu dự án](#-giới-thiệu-dự-án)
2. [Kiến trúc hệ thống](#-kiến-trúc-hệ-thống)
3. [Mô hình dữ liệu MongoDB](#-mô-hình-dữ-liệu-mongodb)
4. [Cài đặt & Khởi chạy](#-cài-đặt--khởi-chạy)
5. [Hướng dẫn sử dụng & Vận hành](#-hướng-dẫn-sử-dụng--vận-hành)
6. [Kịch bản Demo sự cố (Incident Lifecycle)](#-kịch-bản-demo-sự-cố-incident-lifecycle)
7. [Kết quả kiểm chứng (số liệu thật)](#-kết-quả-kiểm-chứng-số-liệu-thật)
8. [Đối chiếu yêu cầu kỹ thuật của BTC](#-đối-chiếu-yêu-cầu-kỹ-thuật-của-btc)
9. [Danh mục bằng chứng & Hướng dẫn lấy ảnh](#-danh-mục-bằng-chứng--hướng-dẫn-lấy-ảnh)
11. [Xử lý sự cố thường gặp](#-xử-lý-sự-cố-thường-gặp)

---

## 🌟 Giới thiệu dự án

ShrimpWatch mô phỏng một trang trại gồm **6 ao nuôi**, dữ liệu môi trường được cập nhật **mỗi 30 giây** (42 bản ghi cảm biến / chu kỳ, tương đương ~1,4 bản ghi/giây).

Các nhóm dữ liệu được thu thập:

| Nhóm | Chỉ số | Nguồn (`metadata.source`) |
|---|---|---|
| Cảm biến IoT | Nhiệt độ, pH, Oxy hòa tan (DO), **Độ mặn (salinity)**, **ORP** | `sensor` |
| Thiết bị vận hành | Công suất quạt (pump_power), Trạng thái quạt (pump_status) | `sensor` |
| Nhập thủ công | Độ kiềm (alkalinity), **Tổng amoni (TAN)**, **Nitrite** từ test-kit, 4 lần/ngày | `manual` |
| Tính toán tự động | CO₂ hòa tan (pH + độ kiềm), **NH₃ độc** (TAN + pH + nhiệt độ), **Biên độ pH 24h** (aggregation `$group` max − min) | `derived` |

### 🆕 Chỉ số bổ sung

| Chỉ số | Đơn vị | Nguồn | Ý nghĩa vận hành | Ngưỡng cảnh báo |
|---|---|---|---|---|
| `salinity` | ppt | sensor | Độ mặn sụt sau mưa lớn → tôm sốc, lột xác lỗi | < 10 ppt |
| `orp` | mV | sensor | Đại diện cho đáy ao: ORP giảm cùng DO khi quạt hỏng → phân hủy kỵ khí, nguy cơ H₂S | < 100 mV |
| `tan` | mg/L | manual | Tổng amoni từ test-kit, đầu vào để tính NH₃ | — |
| `nitrite` | mg/L | manual | NO₂⁻ gây thiếu oxy trong máu tôm | > 1 mg/L |
| `nh3` | mg/L | derived | Phần amoni độc: `TAN × 1/(1+10^(pKa−pH))`, `pKa = 0.09018 + 2729.92/T(K)` (Emerson 1975) | > 0.1 mg/L |
| `ph_swing` | pH | derived | Biên độ pH trong 24h (pipeline `$match → $group → $project`) | > 0.5 |

Khi quạt hỏng, DO giảm ~0.1 mg/L/phút và ORP giảm theo. `incidents.evidence` của sự cố giờ mang thêm `last_orp_mv`, `nh3_mg_per_l`, `ph_swing_24h`, nên kỹ thuật viên thấy được bối cảnh chất lượng nước ngay trong bước Điều tra. Các chỉ số mới nằm ở nhóm cuối của Dashboard Datadog.

**Điểm nhấn kỹ thuật:**
- MongoDB **Time Series Collection** với `expireAfterSeconds` (TTL 7 ngày) làm nơi lưu trữ duy nhất cho telemetry.
- Pipeline **`$setWindowFields` + `$derivative`** tính tốc độ sụt giảm oxy (DO drop rate) theo cửa sổ **thời gian** 5 phút cho cả 6 ao trong **một** lần aggregate → kết quả được lưu vào `incidents.evidence`.
- **Vòng đời dữ liệu 2 tầng:** dữ liệu thô giữ 7 ngày, rollup theo giờ (`$group` + `$merge`) giữ 1 năm.
- **Vòng đời sự cố 4 bước** (Open → Investigating → Resolving → Recovered) lưu trạng thái trong MongoDB, mọi thay đổi đều ghi vào collection `events`.
- **Log ↔ Trace ↔ Metric correlation** trên Datadog nhờ `ddtrace` + `python-json-logger` (mọi dòng log đều mang `dd.trace_id`).

---

## 🏗 Kiến trúc hệ thống

```mermaid
flowchart LR
    subgraph SIM["Simulator (6 ao)"]
        S["app/simulator.py<br/>sinh chỉ số mỗi 30s"]
    end
    subgraph API["FastAPI"]
        A["POST /ingest/batch<br/>POST /ingest/manual/alkalinity<br/>GET /incidents · PATCH /incidents/{id}"]
    end
    subgraph W["Worker"]
        K["app/worker.py<br/>$setWindowFields → DO drop rate<br/>mở/đóng sự cố, tính CO2"]
    end
    subgraph MONGO["MongoDB Atlas M0"]
        M1[("metrics<br/>Time Series + TTL 7d")]
        M2[("incidents")]
        M3[("events")]
        M4[("ponds")]
        M5[("metrics_hourly<br/>rollup + TTL 1 năm")]
    end
    DDA["Datadog Agent<br/>DogStatsD :8125 · APM :8126 · Logs"]
    DD["Datadog Cloud<br/>Dashboard · Monitor · Logs · Traces"]

    S -->|batch 42 bản ghi| A
    A --> M1
    A --> M2
    A --> M4
    K -->|aggregate| M1
    K --> M2
    K --> M3
    K -->|$merge mỗi 5 phút| M5
    A -.-> DDA
    K -.-> DDA
    S -.-> DDA
    DDA --> DD
```

**Sơ đồ container (`docker compose ps`)**

| Container | Nhiệm vụ |
|---|---|
| `shrimpwatch-api` | FastAPI — tiếp nhận dữ liệu, quản lý sự cố |
| `shrimpwatch-simulator` | Mô phỏng 6 ao, gửi dữ liệu mỗi 30 giây |
| `shrimpwatch-worker` | Phân tích dữ liệu, phát hiện & mở/đóng sự cố |
| `shrimpwatch-mongodb` | MongoDB local dự phòng (chỉ dùng khi `.env` không trỏ lên Atlas) |
| `shrimpwatch-datadog` | Datadog Agent — thu metrics, logs, traces |

---

## 🗄 Mô hình dữ liệu MongoDB

Database: **`shrimpwatch`** — 5 collection.

| Collection | Loại | Nội dung |
|---|---|---|
| `metrics` | **Time Series** | `timeField: timestamp`, `metaField: metadata`, `granularity: seconds`, `expireAfterSeconds: 604800` (7 ngày) |
| `metrics_hourly` | Document | Rollup theo giờ: `min`/`max`/`avg`/`count` cho từng `(pond_id, metric, hour)`, TTL 1 năm |
| `incidents` | Document | Sự cố: `severity`, `state`, `evidence`, `actions[]`, `root_cause` |
| `events` | Document | Nhật ký sự kiện: `incident_open`, `incident_action`, `incident_recovered`, `pump_state_change`. Mọi event của sự cố đều có `incident_id` ở cấp cao nhất |
| `ponds` | Document | Danh mục 6 ao và trạng thái quạt nước |

**Cấu trúc một bản ghi telemetry (dạng Time Series):**

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

### Mẫu truy cập & quyết định thiết kế

Hệ thống **ghi ít, đọc nhiều**: mỗi 30 giây chỉ có 1 lần `insert_many` (42 bản ghi), nhưng worker chạy ~60 truy vấn phân tích. Vì vậy ghi được giữ ở dạng append-only theo batch, còn tối ưu tập trung vào đường đọc:

| Quyết định | Lý do |
|---|---|
| **Không tự cài bucket pattern** | Time Series Collection *chính là* bucket pattern do MongoDB quản lý (`system.buckets.metrics`, nén theo cột). Tự gom bucket bằng tay sẽ trùng lặp và mất tối ưu của engine. |
| **1 index ghép `pond_metric_ts`** = `(metadata.pond_id, metadata.metric, timestamp)` | Theo quy tắc ESR: mọi truy vấn của worker là pond (=) + metric (=) + khoảng thời gian. Thay 3 index đơn lẻ cũ (planner chỉ dùng được 1). |
| **Cửa sổ theo thời gian** (`range` + `unit`) thay vì theo số bản ghi | Khi cảm biến rớt gói, cửa sổ vẫn đúng 5 phút, `$derivative` vẫn cho tốc độ đúng. |
| **1 aggregate cho cả 6 ao** (`partitionBy: pond_id`) | 1 round-trip thay vì 6. |
| **Rollup theo giờ** (`$group` → `$merge`, idempotent) | Biên độ pH 24h đọc ~24 dòng rollup thay vì ~2.880 điểm thô mỗi ao mỗi 30 giây; lịch sử dài hạn sống sót sau TTL 7 ngày. |
| **Timeline sự cố** `GET /incidents/{id}/timeline` | 1 aggregate: `$lookup` event của sự cố + sự kiện quạt của ao trước/trong sự cố, sắp xếp bằng `$sortArray` — phục vụ bước *Điều tra*. |

Chạy [scripts/step6_index_and_buckets.js](scripts/step6_index_and_buckets.js) trong mongosh để xem index được dùng, số bucket đã đọc và số bản ghi trung bình mỗi bucket.

---

## 🚀 Cài đặt & Khởi chạy

### 🛠 Yêu cầu tiên quyết
- **Docker Desktop** (bản mới nhất).
- **Git**.
- **RAM ≥ 8 GB** (5 container).
- **Tài khoản MongoDB Atlas** (miễn phí) — nếu muốn lưu dữ liệu trên Cloud.

### Bước 1: Chuẩn bị môi trường

```powershell
copy .env.example .env
```

Mở `.env` và điền các giá trị:

| Biến | Ý nghĩa | Lấy ở đâu |
|---|---|---|
| `MONGODB_URI` | Chuỗi kết nối MongoDB | Atlas → *Connect* → *Drivers* (xem **Bước 4**) |
| `DD_API_KEY` | Khóa API Datadog | Datadog → *Organization Settings* → *API Keys* |
| `DD_APP_KEY` | Khóa ứng dụng Datadog | Datadog → *Organization Settings* → *Application Keys* |
| `DD_SITE` | Tên miền Datadog | `datadoghq.com` (vùng US1) |

### Bước 2: Khởi chạy hệ thống

```powershell
docker compose up -d --build
docker compose ps
```

*Cả 5 dịch vụ phải ở trạng thái **Up**.*

### Bước 3: Khởi tạo dữ liệu

```powershell
docker compose exec -T api python -m app.init_db
```

Lệnh này tạo **Time Series Collection + TTL index** và seed 6 ao. Khi container `simulator` khởi động, nó còn tự nạp lại **6 giờ dữ liệu lịch sử** để Dashboard/Monitor có dữ liệu nền phân tích ngay.

### Bước 4: Trỏ dữ liệu lên MongoDB Atlas (Cloud)

1. **Tạo Cluster:** [MongoDB Atlas](https://cloud.mongodb.com) → *Create* → gói **M0 (Free)** → khu vực *Singapore* → *Create Deployment*.
2. **Tạo user database:** nhập *Username* / *Password* → *Create User* (**lưu lại thông tin**).
3. **Network Access:** *Add entries* → *Allow access from anywhere* (`0.0.0.0/0`) → *Finish and Close*.
   > Bắt buộc — nếu thiếu bước này container sẽ bị chặn IP, dù health check của API vẫn có thể báo `ok` do kết nối cũ còn tồn tại.
4. **Lấy chuỗi kết nối:** *Connect* → *Drivers* → copy `mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority`.
5. **Điền vào `.env`** và khởi động lại:

```powershell
docker compose up -d --build
```

> **Ghi chú:** chuỗi `mongodb+srv://` cần thư viện `dnspython` — đã có sẵn trong [requirements.txt](requirements.txt).

---

## 🎮 Hướng dẫn sử dụng & Vận hành

### 5.1. API (Swagger UI)
Truy cập [http://localhost:8000/docs](http://localhost:8000/docs):

| Endpoint | Chức năng |
|---|---|
| `GET /healthz` | Kiểm tra API + kết nối MongoDB |
| `POST /ingest/batch` | Ghi lô dữ liệu cảm biến |
| `POST /ingest/manual/alkalinity` | Nhập độ kiềm thủ công |
| `POST /ingest/manual/{metric}` | Nhập test-kit thủ công: `alkalinity`, `tan`, `nitrite` |
| `GET /ponds` | Danh sách 6 ao + trạng thái quạt |
| `POST /ponds/{pond_id}/pump/fail?failed=true` | Đánh dấu/hồi phục quạt nước |
| `GET /incidents?state=open` | Truy vấn sự cố theo trạng thái |
| `PATCH /incidents/{id}?state=...&action=...` | Cập nhật vòng đời sự cố |

```powershell
curl http://localhost:8000/healthz
# {"status":"ok","mongo":true,"farm_id":"farm-001","env":"development", ...}
```

### 5.2. Xem dữ liệu thô bằng MongoDB Compass

- **Atlas (Cloud):** dán chuỗi `mongodb+srv://...` vào Compass → database `shrimpwatch`.
- **Docker local:** `mongodb://shrimp_app:shrimp_pass@localhost:27017/shrimpwatch?authSource=shrimpwatch`

### 5.3. Giám sát trên Datadog

| Màn hình | Đường dẫn |
|---|---|
| Dashboard | Datadog → *Dashboards* → **ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng** |
| Monitor | Datadog → *Monitors* → *Manage Monitors* → lọc tag `shrimpwatch` |
| Logs | Datadog → *Logs* → query `service:shrimpwatch` |
| Traces | Datadog → *APM* → *Traces* → service `shrimpwatch` |

---

## 🚨 Kịch bản Demo sự cố (Incident Lifecycle)

Tính năng cốt lõi thể hiện sự phối hợp giữa MongoDB và Datadog.

| Bước | Trạng thái | Hành động |
|---|---|---|
| 1. **Phát hiện** | `open` | Worker phát hiện quạt hỏng (`pump_power` 5 phút < 50W) → mở sự cố **CRITICAL**, `root_cause = pump_failure` |
| 2. **Điều tra** | `investigating` | Kỹ thuật viên kiểm tra điện quạt, ghi chú hành động |
| 3. **Xử lý** | `resolving` | Thay phụ tùng, khôi phục công suất quạt |
| 4. **Hồi phục** | `recovered` | DO ổn định trở lại (> 5 mg/L) → đóng sự cố (worker có thể tự động chuyển trạng thái) |

Chạy demo tự động (thao tác trên ao số 3):

```powershell
docker compose exec -T api python -m app.demo_incident
```

Kết quả thực tế đã kiểm chứng (sự cố `inc-477e03104c`):

```text
06:07:59  Injecting pump failure on pond-03  (pump_power → 0 W)
06:08:05  inc-477e03104c  state=open          severity=critical  root_cause=pump_failure
          evidence: {do_drop_rate: 0.0108 mg/L/phút, avg_pump_power: 0.0 W, min_do: 6.338}
06:08:05  inc-477e03104c → investigating      action="KT điện quạt, báo bảo trì"
06:08:09  inc-477e03104c → resolving          action="Thay phụ tùng, khởi động lại quạt"
06:08:12  inc-477e03104c → recovered          action="AO đã ổn định DO > 5mg/L"
```

---

## ✅ Kết quả kiểm chứng (số liệu thật)

> Thời điểm kiểm chứng: **2026-10-20 06:00–06:09 UTC** (13:00–13:09 giờ Việt Nam), hệ thống đang chạy trực tiếp trên **MongoDB Atlas Cloud**.

### 1. Kết nối & dữ liệu trên Cloud

| Hạng mục | Kết quả |
|---|---|
| Máy chủ | `cluster0.evhkrg4.mongodb.net` (replica set 3 node, gói M0) |
| Database | `shrimpwatch` |
| Collection | `metrics`, `incidents`, `ponds`, `events` (+ `system.buckets.metrics`) |
| Số bản ghi | `metrics` = **24.684** · `incidents` = 8 · `events` = 17 · `ponds` = 6 |
| Cấu hình Time Series | `timeField=timestamp`, `metaField=metadata`, `granularity=seconds`, `bucketMaxSpanSeconds=3600` |
| TTL | `expireAfterSeconds = 604800` (7 ngày) |
| Tốc độ ghi thực đo | **354 bản ghi / 5 phút** (~1,18 bản ghi/giây) |
| Chỉ số đang ghi | `temperature`, `do`, `ph`, `pump_power`, `pump_status`, `alkalinity`, `co2` |
| Health check API | `{"status":"ok","mongo":true}` |

### 2. Kết quả pipeline `$setWindowFields` (MongoDB)

Trích log thật của worker (cửa sổ 5 phút, `do_drop_rate_mg_per_l_per_min`):

| Ao | DO đầu cửa sổ | DO cuối cửa sổ | DO drop | Tốc độ sụt (mg/L/phút) |
|---|---|---|---|---|
| pond-01 | 7.842 | 7.410 | 0.432 | **0.0866** |
| pond-02 | 7.897 | 7.583 | 0.314 | 0.0630 |
| pond-03 (quạt hỏng) | 6.404 | 6.392 | 0.012 | 0.0024 |
| pond-04 | 7.804 | 7.497 | 0.307 | 0.0616 |
| pond-05 | 7.878 | 7.596 | 0.282 | 0.0565 |
| pond-06 | 7.981 | 7.541 | 0.440 | **0.0882** |

Kết quả này được ghi trực tiếp vào `incidents.evidence.do_drop_rate_mg_per_l_per_min`.

### 3. Trạng thái Datadog (kiểm chứng qua API)

| Loại | ID | Tên | Trạng thái |
|---|---|---|---|
| Dashboard | `sid-7q3-hug` | ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng | Đang hoạt động |
| Monitor | `334608203` | [ShrimpWatch] CRITICAL: Quạt nước hỏng (Pump failure) | **Alert** — đã kích hoạt thật lúc 06:03 UTC, tự hồi phục về `OK` sau khi quạt được khôi phục (đúng cơ chế recovery) |
| Monitor | `334608560` | [ShrimpWatch] WARNING: DO giảm nhanh bất thường | No Data (thuật toán `anomalies` cần thêm dữ liệu nền) |
| Monitor | `334609031` | Watchdog Anomaly for `service` | OK |
| Monitor | `334609512` | High Error Rate on `service.name` (APM) | OK |

Log đã về Datadog: truy vấn `service:shrimpwatch` (30 phút gần nhất) trả về bản ghi có `dd.trace_id` — xác nhận **Log Management + Log↔Trace correlation** hoạt động.

### 4. Kiểm thử tự động

**9/9 test PASSED** (chi tiết tại [screenshots_pytest.txt](screenshots_pytest.txt)).

---

## 📑 Đối chiếu yêu cầu kỹ thuật của BTC

### 6.1. MongoDB — Nền tảng dữ liệu vận hành

#### 🔴 Bắt buộc (6/6 tiêu chí)

| # | Yêu cầu BTC | ShrimpWatch đáp ứng | Bằng chứng (mã nguồn / ảnh) |
|---|---|---|---|
| 1 | **Nền tảng CHÍNH** | Toàn bộ dữ liệu sensor, sự cố, lịch sử vận hành lưu trên **MongoDB Atlas (Cloud)** | [13_mongodb_atlas_cluster.png](assets_proof/13_mongodb_atlas_cluster.png) · [14_mongodb_atlas_data.png](assets_proof/14_mongodb_atlas_data.png) |
| 2 | **Time Series Collections** | Telemetry (DO, pH, nhiệt độ, quạt) lưu trong Time Series Collection `metrics` (`timeField`, `metaField`, `granularity`) | [01_mongo_timeseries_ttl.png](assets_proof/01_mongo_timeseries_ttl.png) · [db.py](app/db.py#L58-L82) |
| 3 | **Aggregation & Window Functions** | `$setWindowFields` + `$derivative` tính tốc độ sụt DO theo cửa sổ thời gian 5 phút; `$group` + `$merge` rollup theo giờ; `$lookup` + `$sortArray` dựng timeline sự cố | [db.py](app/db.py) (`compute_do_drop_rate_pipeline`, `rollup_hourly_pipeline`, `incident_timeline_pipeline`) |
| 4 | **expireAfterSeconds (TTL)** | TTL tự động xoá telemetry thô sau 7 ngày (`604800` giây); rollup `metrics_hourly` giữ 1 năm | [01_mongo_timeseries_ttl.png](assets_proof/01_mongo_timeseries_ttl.png) · [db.py](app/db.py) (`init_collections`) |
| 5 | **Mô hình & Truy cập hiệu quả** | Time Series (bucket do engine quản lý), index ghép ESR `pond_metric_ts`, 1 aggregate cho cả 6 ao, rollup thay cho quét dữ liệu thô — xem [Mẫu truy cập & quyết định thiết kế](#mẫu-truy-cập--quyết-định-thiết-kế) | [db.py](app/db.py) · [step6_index_and_buckets.js](scripts/step6_index_and_buckets.js) |
| 6 | **Gắn chặt với vận hành** | MongoDB điều khiển toàn bộ state machine của sự cố (`open → investigating → resolving → recovered`) | [04_swagger_ui_incidents.png](assets_proof/04_swagger_ui_incidents.png) · [06_mongodb_document_incident.png](assets_proof/06_mongodb_document_incident.png) |

#### 🟡 Tùy chọn

| Yêu cầu BTC | ShrimpWatch đáp ứng | Bằng chứng |
|---|---|---|
| Xử lý hướng sự kiện | Mọi thay đổi trạng thái sự cố ghi vào collection `events` (khoá theo `incident_id`), dựng lại thành timeline qua `GET /incidents/{id}/timeline` | [05_mongodb_event_log.png](assets_proof/05_mongodb_event_log.png) |
| Change Streams / mở rộng | Cấu trúc dữ liệu sẵn sàng tích hợp Change Streams cho thông báo thời gian thực | [db.py](app/db.py) |

### 6.2. Datadog — Quan sát & Cảnh báo

#### 🔴 Bắt buộc (4/4 tiêu chí)

| # | Yêu cầu BTC | ShrimpWatch đáp ứng | Bằng chứng |
|---|---|---|---|
| 1 | **Ít nhất 1 Dashboard** | Dashboard tổng quan sức khỏe 6 ao: KPI sự cố, nhiệt độ, DO, pH, độ kiềm, CO₂, công suất quạt, heatmap | [07_datadog_dashboard.png](assets_proof/07_datadog_dashboard.png)   [07_datadog_dashboard_2.png](assets_proof/07_datadog_dashboard_2.png)   [07_datadog_dashboard_3.png](assets_proof/07_datadog_dashboard_3.png)   · [shrimpwatch_overview.json](datadog/dashboards/shrimpwatch_overview.json) |
| 2 | **Ít nhất 1 Monitor/Alert** | Monitor **CRITICAL** khi quạt hỏng (`pump_power < 50W`) và **WARNING** khi oxy sụt bất thường | [08_datadog_monitor_alert.png](assets_proof/08_datadog_monitor_alert.png) · [pump_failure_critical.json](datadog/monitors/pump_failure_critical.json) |
| 3 | **Năng lực bổ sung** | Tích hợp đồng thời Metrics (DogStatsD), Log Management, APM và Distributed Tracing | [09_datadog_correlation_log_trace_metric.png](assets_proof/09_datadog_correlation_log_trace_metric.png) |
| 4 | **Kết nối Metrics/Logs/Traces** | Mỗi log mang `dd.trace_id`; span được gắn tag `pond`, `device_type` → điều tra từ cảnh báo tới dòng code | [09_datadog_correlation_log_trace_metric.png](assets_proof/09_datadog_correlation_log_trace_metric.png) · [logging_setup.py](app/logging_setup.py) |

#### 🟡 Tùy chọn nâng cao

| Yêu cầu BTC | ShrimpWatch đáp ứng | Bằng chứng |
|---|---|---|
| Agent Observability | Theo dõi sức khỏe Datadog Agent qua `agent status` và `http_check` | [03_agent_status.png](assets_proof/03_agent_status.png)    [03_agent_status_2.png](assets_proof/03_agent_status_2.png)|
| AI Observability | Dùng Watchdog Insights để phát hiện bất thường trong log | [09_datadog_correlation_log_trace_metric.png](assets_proof/09_datadog_correlation_log_trace_metric.png) |

---

## 📂 Danh mục bằng chứng & Hướng dẫn lấy ảnh

Tất cả ảnh được lưu trong thư mục **`assets_proof/`** với đúng tên tệp dưới đây.

| # | Tên tệp | Nội dung minh chứng |
|---|---|---|
| 1 | `01_mongo_timeseries_ttl.png` | Cấu hình Time Series + TTL trong MongoDB |
| 2 | `02_pytest_9_xanh.png` | Kết quả chạy kiểm thử tự động (9/9 passed) |
| 3 | `03_agent_status.png` | Trạng thái Datadog Agent |
| 4 | `04_swagger_ui_incidents.png` | Giao diện quản lý sự cố (Incident Management) |
| 5 | `05_mongodb_event_log.png` | Nhật ký sự kiện (collection `events`) |
| 6 | `06_mongodb_document_incident.png` | Cấu trúc Document của một sự cố (có `evidence`, `actions`) |
| 7 | `07_datadog_dashboard.png` | Dashboard — Phần 1: KPI & sức khỏe hệ thống |
| 8 | `07_datadog_dashboard_2.png` | Dashboard — Phần 2: Nhiệt độ, DO, pH |
| 9 | `07_datadog_dashboard_3.png` | Dashboard — Phần 3: Độ kiềm, CO₂, quạt nước, heatmap |
| 10 | `08_datadog_monitor_alert.png` | Cảnh báo đỏ (Alert) khi quạt nước hỏng |
| 11 | `09_datadog_correlation_log_trace_metric.png` | Truy vết Log ↔ Trace ↔ Metric |
| 12 | `ShrimpWatch_Lifecycle.mp4` | Video demo trọn vẹn vòng đời sự cố |
| 13 | `13_mongodb_atlas_cluster.png` | Cụm MongoDB Atlas (Cloud, gói M0) đang hoạt động |
| 14 | `14_mongodb_atlas_data.png` | Dữ liệu cảm biến 6 ao chảy trực tiếp lên Atlas |

### 📸 Cách lấy từng ảnh

**1. `01_mongo_timeseries_ttl.png` — Time Series + TTL**
1. Atlas → **Database** → cluster0 → **Browse Collections** → nút **`>_` (Open MongoDB Shell)**.
2. Dán toàn bộ nội dung [scripts/step4_check_ts_ttl_ponds.js](scripts/step4_check_ts_ttl_ponds.js) rồi Enter.
3. Chụp phần kết quả in ra: `timeseries.timeField`, `metaField`, `granularity` và `expireAfterSeconds TTL : 604800 (~ 7 ngày)`.

**2. `02_pytest_9_xanh.png` — Kiểm thử**
1. Chạy: `make test` (chạy trên database `shrimpwatch_test` của container MongoDB local, không đụng tới Atlas)
2. Chụp dòng `9 passed`.
> ⚠️ **Cảnh báo:** bộ test gọi `init_collections(force=True)` nên sẽ **xoá sạch collection** trong database đang kết nối. `make test` đã ghi đè `MONGODB_URI`/`MONGODB_DB` về MongoDB local; đừng chạy `pytest` trực tiếp khi `.env` trỏ lên Atlas.

**3. `03_agent_status.png` — Datadog Agent**
```powershell
docker compose exec -T datadog-agent agent status
```
Chụp phần đầu báo cáo (Agent version, Running Checks, `http_check`).

**4. `04_swagger_ui_incidents.png` — Incident Management**
1. Mở [http://localhost:8000/docs](http://localhost:8000/docs).
2. Mở rộng endpoint `GET /incidents` → **Try it out** → **Execute**.
3. Chụp vùng Response hiển thị danh sách sự cố.

**5. `05_mongodb_event_log.png` — Event Log**
1. Atlas → **Browse Collections** → `shrimpwatch` → `events`.
2. Sắp xếp theo `timestamp` giảm dần (Sort desc).
3. Chụp vài document `incident_open` / `incident_action` / `incident_recovered`.

**6. `06_mongodb_document_incident.png` — Document sự cố**
1. Atlas → `shrimpwatch` → `incidents`.
2. Lọc `_id` = `inc-477e03104c` (hoặc sự cố mới nhất có `severity: critical`).
3. Mở rộng trường `evidence` (chứa `do_drop_rate_mg_per_l_per_min`) và mảng `actions` (4 bước) rồi chụp.
> Cần chạy `docker compose exec -T api python -m app.demo_incident` trước để có đủ 4 bước trong `actions`.

**7–9. `07_datadog_dashboard*.png` — Dashboard**
1. Datadog → **Dashboards** → **ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng**.
2. Đặt khoảng thời gian **Past 4 Hours**, biến `pond = *`, `device_type = *`.
3. Cuộn và chụp lần lượt:
   - **Phần 1:** nhóm *Tổng quan hệ thống* (4 ô KPI: Sự cố đang mở, Đã hồi phục, Số lần quạt hỏng, Records ingested/phút).
   - **Phần 2:** nhóm *Chỉ số theo dõi theo Ao* (Nhiệt độ, DO, pH).
   - **Phần 3:** nhóm *Độ kiềm & CO₂ + Quạt nước* (kèm heatmap DO theo ao).

**10. `08_datadog_monitor_alert.png` — Cảnh báo đỏ**
1. Datadog → **Monitors** → **Manage Monitors** → lọc tag `shrimpwatch`.
2. Mở monitor **[ShrimpWatch] CRITICAL: Quạt nước hỏng** (ID `334608203`) và xem trạng thái hiện tại.
3. Nếu monitor đang `OK`, hãy kích hoạt lại cảnh báo thật bằng lệnh sau rồi chờ **6–7 phút** (5 phút cửa sổ cảnh báo + 90 giây `evaluation_delay`):
   ```powershell
   docker compose exec -T api python -c "import httpx;print(httpx.post('http://localhost:8000/ponds/pond-05/pump/fail',params={'failed':'true'}).json())"
   ```
   Lúc này worker cũng mở một sự cố CRITICAL mới cho ao 5 → có thêm dữ liệu để chụp ảnh `06_mongodb_document_incident.png`.
4. Chụp vùng trạng thái đỏ + biểu đồ giá trị `pump_power` + phần message của monitor.
5. Chụp xong, khôi phục quạt: `POST /ponds/pond-05/pump/fail?failed=false`.

**11. `09_datadog_correlation_log_trace_metric.png` — Log ↔ Trace ↔ Metric**
1. Datadog → **Logs** → query: `service:shrimpwatch`
2. Mở một log có `trace_id` khác `0` (ví dụ dòng `ingested batch` hoặc `DO window computed`).
3. Trong panel chi tiết, bấm tab **Trace** (hoặc biểu tượng *Trace* cạnh `dd.trace_id`) để nhảy sang APM.
4. Chụp cả 2 tầng: danh sách log + panel trace mở ra.
> Muốn đối chiếu Metric: mở **Metrics → Explorer**, query `shrimpwatch.metric.do` và chụp kèm.

**12. `ShrimpWatch_Lifecycle.mp4` — Video**
1. Bật quay màn hình.
2. Chạy `docker compose exec -T api python -m app.demo_incident` (log hiện đủ 4 bước).
3. Mở song song tab Datadog Monitor và Atlas → `incidents` để thấy trạng thái đổi theo thời gian thực.

**13. `13_mongodb_atlas_cluster.png` — Cluster Atlas**
1. Atlas → **Database** → danh sách cluster.
2. Chụp thẻ **cluster0** kèm nhãn gói **M0 / Free**, region và nút *Connect*.

**14. `14_mongodb_atlas_data.png` — Dữ liệu trên Cloud**
1. Atlas → **Browse Collections** → `shrimpwatch` → `metrics`.
2. Chụp danh sách document mới nhất (có `timestamp` vài giây trước, `metadata.pond_id` của 6 ao).

---

## 🧪 Kiểm thử tự động

```powershell
docker compose exec -T api pytest -v tests/
```

Bộ test bao phủ: health check, seed collection, cấu hình Time Series + TTL, ghi dữ liệu cảm biến, nhập độ kiềm/TAN/nitrite thủ công, pipeline `$setWindowFields`, pipeline biên độ pH, công thức suy ra CO₂ và NH₃, DO/ORP giảm khi quạt hỏng, mở sự cố khi quạt hỏng và vòng đời 4 trạng thái — **14/14 PASSED**.

> ⚠️ **Lưu ý quan trọng:** fixture `setup_db` gọi `init_collections(force=True)` → **xoá toàn bộ collection** của database đang kết nối. Không chạy lệnh này khi `.env` đang trỏ lên MongoDB Atlas đang chứa dữ liệu báo cáo.

---


## 💻 Công nghệ sử dụng

- **Backend:** FastAPI 0.115, Pydantic v2, Uvicorn.
- **Database:** MongoDB Atlas M0 — Time Series Collections, Aggregation Pipeline (`$setWindowFields`, `$group`), TTL Index, Replica Set.
- **Observability:** Datadog Agent 7.59 — DogStatsD metrics, APM (`ddtrace`), Log Management, Distributed Tracing.
- **Hạ tầng:** Docker, Docker Compose, Makefile.

---

**Chúc bạn một kỳ Hackathon thành công rực rỡ! 🦐🚀**
