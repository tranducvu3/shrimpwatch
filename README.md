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

## 📦 Thông tin nộp bài

| Hạng mục | Nội dung |
|---|---|
| Tên dự án | **ShrimpWatch** — giám sát chất lượng nước ao nuôi tôm thẻ chân trắng |
| Bài toán | Quạt nước hỏng ban đêm hoặc mưa lớn làm oxy hòa tan (DO) / độ mặn sụt trong vài chục phút; phát hiện muộn có thể làm chết cả ao. ShrimpWatch phát hiện sớm, hỗ trợ điều tra bằng dữ liệu, ghi lại cách xử lý và chứng minh ao đã hồi phục |
| Đội | Trần Đức Vũ ([tranducvu3](https://github.com/tranducvu3)) · Hồ Ngọc Dũng ([HoNgocDung1503](https://github.com/HoNgocDung1503)) · Quang Anh |
| Đầu mối liên hệ | Trần Đức Vũ — vu.tran@techcoop.vn |
| Repository | https://github.com/tranducvu3/shrimpwatch (public) |
| Video demo | ⏳ _Cập nhật link trước 23:59 31/10/2026_ |
| Slide thuyết trình | ⏳ _Cập nhật link trước 23:59 31/10/2026_ |
| Sự cố được trình diễn | Quạt nước ao 1 hỏng → DO sụt → mở sự cố **CRITICAL `pump_failure`** → Điều tra (Vector Search gợi ý sự cố cũ) → Xử lý → worker tự đóng khi số liệu ổn định. Kịch bản phụ: mưa lớn làm độ mặn ao 3 < 10 ppt. Chi tiết: [Kịch bản Demo](#-kịch-bản-demo-sự-cố-incident-lifecycle), [kiểm chứng 31/10](#0-kiểm-chứng-end-to-end-ngày-31102026) |
| Khai báo tái sử dụng & AI | [Mục 11](#-khai-báo-thành-phần-tái-sử-dụng-dataset--công-cụ-ai) |

---

## 📖 Mục lục

0. [Thông tin nộp bài](#-thông-tin-nộp-bài)
1. [Giới thiệu dự án](#-giới-thiệu-dự-án)
2. [Kiến trúc hệ thống](#-kiến-trúc-hệ-thống)
3. [Mô hình dữ liệu MongoDB](#-mô-hình-dữ-liệu-mongodb)
4. [Cài đặt & Khởi chạy](#-cài-đặt--khởi-chạy)
5. [Hướng dẫn sử dụng & Vận hành](#-hướng-dẫn-sử-dụng--vận-hành)
6. [Kịch bản Demo sự cố (Incident Lifecycle)](#-kịch-bản-demo-sự-cố-incident-lifecycle)
7. [Kết quả kiểm chứng (số liệu thật)](#-kết-quả-kiểm-chứng-số-liệu-thật)
8. [Đối chiếu yêu cầu kỹ thuật của BTC](#-đối-chiếu-yêu-cầu-kỹ-thuật-của-btc)
9. [Danh mục bằng chứng & Hướng dẫn lấy ảnh](#-danh-mục-bằng-chứng--hướng-dẫn-lấy-ảnh)
10. [Kiểm thử tự động](#-kiểm-thử-tự-động)
11. [Khai báo thành phần tái sử dụng, dataset & công cụ AI](#-khai-báo-thành-phần-tái-sử-dụng-dataset--công-cụ-ai)

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
        S["app/simulator/<br/>sinh chỉ số mỗi 30s"]
    end
    subgraph API["FastAPI"]
        A["POST /ingest/batch<br/>POST /ingest/manual/{metric}<br/>GET /incidents · PATCH /incidents/{id}"]
    end
    subgraph W["Worker"]
        K["app/worker/<br/>$setWindowFields → DO drop rate<br/>mở/đóng sự cố, tính CO2"]
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

### Cấu trúc mã nguồn

Mã được chia theo tầng; mỗi tầng chỉ gọi tầng bên dưới:

```
app/
  core/        cấu hình, log JSON, Datadog (DogStatsD + span tag), đồng hồ UTC
  domain/      nghiệp vụ thuần, không đụng DB/HTTP: model, công thức CO₂/NH₃,
               quy tắc phát hiện sự cố, vòng đời sự cố (chuyển trạng thái hợp lệ)
  db/          mongo.py (kết nối + collection), schema.py (collection/index/migrate),
               pipelines.py (aggregation), repositories/ (truy vấn theo collection)
  services/    nơi duy nhất ghi dữ liệu: ingest, derivations, incidents, ponds
  api/         FastAPI: main.py, schemas.py (body request), routers/ theo tài nguyên
  worker/      loop.py (chu kỳ 30s) + pond_check.py (số liệu → quy tắc → service)
  simulator/   pond_model.py (mô hình ao), runner.py, api_client.py (chỉ gọi HTTP API)
  cli/         init_db, seed_incident_history, demo_incident, datadog_push  →  python -m app.cli.<tên>
```

```
api, worker ──► services ──► db.repositories ──► db.mongo
                    └──────► domain (thuần)
simulator, cli.demo_incident ──HTTP──► api
```

- Thêm chỉ số mới: khai báo ở `domain/models.py`, công thức ở `domain/`, ghi ở `services/derivations.py`.
- Thêm quy tắc cảnh báo: sửa `domain/incident_rules.py` và thêm test ở `tests/unit/test_incident_rules.py` (không cần MongoDB).
- `PATCH /incidents/{id}` từ chối chuyển trạng thái không hợp lệ (ví dụ `recovered → open`) bằng `409`.

---

## 🗄 Mô hình dữ liệu MongoDB

Database: **`shrimpwatch`** — 5 collection.

| Collection | Loại | Nội dung |
|---|---|---|
| `metrics` | **Time Series** | `timeField: timestamp`, `metaField: metadata`, `granularity: seconds`, `expireAfterSeconds: 604800` (7 ngày) |
| `metrics_hourly` | Document | Rollup theo giờ: `min`/`max`/`avg`/`count` cho từng `(pond_id, metric, hour)`, TTL 1 năm |
| `incidents` | Document | Sự cố: `severity`, `state`, `evidence`, `actions[]`, `root_cause`, `fingerprint` (vector 6 chiều, Atlas Vector Search index `incident_fingerprint`), `similar_incidents[]` |
| `events` | Document | Nhật ký sự kiện: `incident_open`, `incident_escalated`, `incident_action`, `incident_recovered`, `incident_similar_matched`, `incident_imported`, `pump_state_change`, `scenario_start`, `scenario_clear`. Mọi event của sự cố đều có `incident_id` ở cấp cao nhất |
| `ponds` | Document | Danh mục 6 ao, trạng thái quạt nước và kịch bản mô phỏng đang chạy (`scenarios`) |

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

### 🔎 Vector Search: tìm sự cố tương tự trong lịch sử

Khi kỹ thuật viên chuyển sự cố sang **Điều tra**, hệ thống tìm những sự cố **đã hồi phục** có diễn biến nước giống nhất và cho biết lần trước đã xử lý thế nào, mất bao lâu.

- **Fingerprint không cần mô hình AI:** mỗi sự cố có vector 6 chiều chuẩn hoá về `[0, 1]` từ `evidence`: tốc độ sụt DO, công suất quạt, DO thấp nhất, ORP, NH₃, biên độ pH 24h ([incident_fingerprint.py](app/domain/incident_fingerprint.py)). Không cần API key, không thêm thư viện.
- **Atlas Vector Search index** `incident_fingerprint`: `similarity: euclidean`, `filter` theo `state` → `$vectorSearch` chỉ tìm trong sự cố `recovered`. Điểm = `1 / (1 + d²)`.
- **Fallback:** mongod thường (CI, Docker Compose cục bộ) không có `$vectorSearch` → cùng công thức điểm tính bằng `$reduce` trong aggregation, giới hạn 500 sự cố gần nhất. Kết quả trả về ghi rõ `mode: vector_search | aggregation_fallback`.
- **Ghi lại:** top 3 gợi ý lưu vào `incidents.similar_incidents` và event `incident_similar_matched` (xuất hiện trong timeline).
- **Datadog:** metric `shrimpwatch.incident.similarity_score` (tag `pond`, `mode`, `root_cause`), `shrimpwatch.incident.similarity_search`, tag span `similarity_mode` / `similarity_top_score`; nhóm widget cuối trong dashboard.

| Sự cố đang mở: quạt hỏng pond-05 | Điểm cao nhất theo nhóm lịch sử |
|---|---|
| Quạt hỏng (`pump_failure`) | **0.998** |
| Mất điện ban đêm (`pump_failure`) | 0.972 |
| Tảo tàn / dư NH₃ / mưa lớn | 0.45 – 0.49 |

```bash
python -m app.cli.seed_incident_history          # nạp 15 sự cố lịch sử tổng hợp (idempotent)
curl "localhost:8000/incidents/<id>/similar?k=3"   # top 3 sự cố tương tự
```

> Index chỉ tạo được trên MongoDB Atlas (M0 trở lên) hoặc image `mongodb/mongodb-atlas-local`; `init_db` tự bỏ qua trên mongod thường. Index mới cần khoảng 1 phút để sẵn sàng, trong lúc đó API dùng fallback.

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
docker compose exec -T api python -m app.cli.init_db
docker compose exec -T api python -m app.cli.seed_incident_history   # lịch sử sự cố cho Vector Search
```

Lệnh này tạo **Time Series Collection + TTL index** và seed 6 ao. Khi container `simulator` khởi động, nó tự nạp phần dữ liệu lịch sử còn thiếu (tối đa **6 giờ**, tính từ bản ghi mới nhất) để Dashboard/Monitor có dữ liệu nền phân tích ngay; khởi động lại không ghi chồng dữ liệu đã có.

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
| `POST /ingest/manual/{metric}` | Nhập test-kit thủ công: `alkalinity`, `tan`, `nitrite` |
| `GET /ponds` | Danh sách 6 ao + trạng thái quạt |
| `POST /ponds/{pond_id}/pump/fail?failed=true` | Đánh dấu/hồi phục quạt nước |
| `GET /ponds/readings?window_min=10` | Giá trị cảm biến mới nhất của từng ao trong `window_min` phút gần đây (mặc định 10, tối đa 360) |
| `POST /ponds/{pond_id}/scenarios` | Bật kịch bản lệch chỉ số: `{"scenario": "do\|temperature\|ph\|salinity", "offset": -3, "duration_min": 15}` |
| `DELETE /ponds/{pond_id}/scenarios[/{scenario}]` | Tắt một hoặc tất cả kịch bản của ao |
| `GET /ponds/scenarios/catalog` | Giới hạn độ lệch của từng kịch bản và ngưỡng cảnh báo |
| `GET /control` | Bảng điều khiển simulator (xem mục 5.2) |
| `GET /incidents?state=open` | Truy vấn sự cố theo trạng thái |
| `PATCH /incidents/{id}?state=...&action=...` | Cập nhật vòng đời sự cố |

```powershell
curl http://localhost:8000/healthz
# {"status":"ok","mongo":true,"farm_id":"farm-001","env":"development", ...}
```

### 5.2. Bảng điều khiển simulator

Mở [http://localhost:8000/control](http://localhost:8000/control) để tạo hành vi bất thường cho từng ao mà không cần gọi API bằng tay:

- **Nguồn quạt nước**: bật/tắt quạt nước (dùng `POST /ponds/{id}/pump/fail`) → `pump_power` về 0 W, DO và ORP sụt, worker mở sự cố `pump_failure`.
- **Kịch bản môi trường**: lệch DO, nhiệt độ, pH hoặc độ mặn so với mức bình thường trong 5–120 phút. Có sẵn các nút nhanh: *Giảm oxy*, *Nắng nóng*, *pH tăng vọt*, *Mưa lớn*.
- Trang tự cập nhật mỗi 5 giây: chỉ số mới nhất (tô màu khi vượt ngưỡng), kịch bản đang chạy, sự cố đang mở.

Simulator áp dụng thay đổi ở lần gửi kế tiếp (30 giây). Kịch bản tự hết hạn; **Khôi phục tất cả ao** bật lại quạt và xoá mọi kịch bản. API không có xác thực, nên không mở cổng 8000 ra Internet.

### 5.3. Xem dữ liệu thô bằng MongoDB Compass

- **Atlas (Cloud):** dán chuỗi `mongodb+srv://...` vào Compass → database `shrimpwatch`.
- **Docker local:** `mongodb://shrimp_app:shrimp_pass@localhost:27017/shrimpwatch?authSource=shrimpwatch`

### 5.4. Giám sát trên Datadog

| Màn hình | Đường dẫn |
|---|---|
| Dashboard | Datadog → *Dashboards* → **ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng** |
| Monitor | Datadog → *Monitors* → *Manage Monitors* → lọc tag `shrimpwatch` |
| Logs | Datadog → *Logs* → query `service:shrimpwatch` |
| Traces | Datadog → *APM* → *Traces* → service `shrimpwatch` |

Monitor trong [datadog/monitors/](datadog/monitors/) (cùng ngưỡng với `app/core/config.py` và quy tắc sự cố của worker):

| Monitor | Điều kiện (trung bình 5 phút, theo `pond`) | Sự cố worker tương ứng |
|---|---|---|
| Quạt nước hỏng | `pump_power` < 50 W | `pump_failure` (critical) |
| DO giảm nhanh | DO giảm > 0.25 mg/L so với 5 phút trước | `do_anomaly_drop` (warning) |
| DO thấp | DO < 4 (warning), < 3 (critical) | `do_low`, `do_critical` |
| Nhiệt độ ngoài ngưỡng | ngoài 26–32 °C | `temperature_out_of_range` (warning) |
| pH ngoài ngưỡng | ngoài 7.5–8.5 | `ph_out_of_range` (warning) |
| Độ mặn thấp | < 10 ppt | `salinity_low` (warning) |

Monitor xét trung bình **5 phút đầy đủ** và Datadog nhận số liệu chậm vài chục giây, nên khi demo quạt hỏng hãy để quạt tắt **ít nhất 8 phút** thì monitor `pump_power < 50 W` mới chuyển Alert (worker mở sự cố sớm hơn, theo dữ liệu MongoDB).

Widget **Sự cố ĐANG MỞ** đọc gauge `shrimpwatch.incident.active` (0/1 mỗi ao, worker gửi mỗi chu kỳ 30 giây), nên con số giảm ngay khi sự cố hồi phục. Counter `shrimpwatch.incident.open` / `incident.recovered` vẫn dùng để đếm số lần mở / hồi phục.

Một ao chỉ có một sự cố đang mở. Khi có nguyên nhân nghiêm trọng hơn, sự cố được nâng mức và ghi event `incident_escalated`; sự cố không bị hạ mức nên vẫn giữ nguyên nhân nghiêm trọng nhất đến khi hồi phục.

`python -m app.cli.datadog_push` cập nhật monitor/dashboard trùng tên, chưa có thì tạo mới.

---

## 🚨 Kịch bản Demo sự cố (Incident Lifecycle)

Tính năng cốt lõi thể hiện sự phối hợp giữa MongoDB và Datadog.

| Bước | Trạng thái | Hành động |
|---|---|---|
| 1. **Phát hiện** | `open` | Worker phát hiện quạt hỏng (`pump_power` 5 phút < 50W) → mở sự cố **CRITICAL**, `root_cause = pump_failure` |
| 2. **Điều tra** | `investigating` | Kỹ thuật viên kiểm tra điện quạt, ghi chú hành động. **Vector Search** gợi ý 3 sự cố cũ giống nhất kèm cách xử lý và thời gian hồi phục |
| 3. **Xử lý** | `resolving` | Thay phụ tùng, khôi phục công suất quạt. Sự cố vẫn được worker theo dõi: nếu lỗi còn, worker cập nhật chính sự cố này (không mở sự cố trùng) |
| 4. **Hồi phục** | `recovered` | Quạt chạy lại và DO ổn định (5 phút) → **worker tự đóng** sự cố đang ở `open`/`investigating`/`resolving`. Mọi bước chuyển đều ghi một event vào `events` |

Chạy demo tự động (thao tác trên ao số 3):

```powershell
docker compose exec -T api python -m app.cli.demo_incident
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

### 0. Kiểm chứng end-to-end ngày 31/10/2026

> Kiểm tra lại toàn bộ luồng trên hệ thống thật (MongoDB Atlas + Datadog) lúc **11:59–12:12 UTC** (18:59–19:12 giờ Việt Nam): thao tác trên [bảng điều khiển](#52-bảng-điều-khiển-simulator) bằng Playwright, đối chiếu Datadog qua API.

| Giờ (UTC) | Sự kiện | Nguồn |
|---|---|---|
| 11:59:08 | Tắt quạt ao 1, bật kịch bản **Mưa lớn** ở ao 3 (độ mặn −7 ppt) | Bảng điều khiển |
| 12:01:27 | Mở sự cố `warning / do_anomaly_drop` ao 1 (DO giảm 0,07 mg/L/phút) | Worker → `incidents`, `events` |
| 12:02:27 | Mở sự cố `warning / salinity_low` ao 3 (độ mặn TB 5 phút 7,78 ppt) | Worker |
| 12:03:24 | Monitor **Độ mặn thấp** chuyển *Triggered* trên `pond:pond-03` | Datadog |
| 12:03:56 | **Cùng sự cố** ao 1 được nâng lên `critical / pump_failure` (công suất TB 5 phút 0 W), có event `incident_escalated` — không mở sự cố trùng | Worker |
| 12:04:59 | Monitor **DO giảm nhanh** chuyển *Triggered* trên `pond:pond-01` | Datadog |
| 12:05:10 | Chuyển sang `investigating` → Atlas Vector Search gợi ý sự cố cũ `hist-pump-01` (score 0,70) kèm cách xử lý "Thay tụ quạt, khởi động lại quạt" và thời gian hồi phục 30 phút | API + `$vectorSearch` |
| 12:05:44 | Bấm **Khôi phục tất cả ao** (quạt chạy lại, xoá kịch bản) | Bảng điều khiển |
| 12:07:27 | Worker tự đóng sự cố ao 3 (`recovered`, event `incident_recovered`) | Worker |
| 12:09:24 · 12:10:59 | Hai monitor chuyển *Recovered* | Datadog |

Kiểm tra API: chuyển trạng thái sai (`recovered → open`, `investigating → open`) trả **409**; `state`/`scenario`/`metric` ngoài enum, toán tử Mongo trong body (`{"$where": ...}`, `{"$ne": null}`) bị Pydantic chặn với **422**; ao/sự cố không tồn tại trả **404**.

Lần kiểm chứng này phát hiện và sửa 2 lỗi vòng đời: sự cố ở `resolving` không được worker theo dõi (mở sự cố trùng, không bao giờ tự đóng) và chuyển trạng thái không kèm ghi chú không ghi `events`. Cả hai đã có test.

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

### 3. Trạng thái Datadog (kiểm chứng qua API, 31/10/2026)

Dashboard và monitor được đẩy từ JSON trong repo bằng `python -m app.cli.datadog_push` (cập nhật theo tên, không tạo trùng).

| Loại | ID | Tên | Kết quả 31/10 |
|---|---|---|---|
| Dashboard | `896-5wb-wzs` | ShrimpWatch - Giám sát chất lượng nước Ao nuôi tôm thẻ chân trắng | Mọi metric trong dashboard đều có dữ liệu |
| Monitor | `339119277` | [ShrimpWatch] CRITICAL: Quạt nước hỏng (Pump failure) | `pump_power < 50 W` trung bình 5 phút theo `pond` |
| Monitor | `339209639` | [ShrimpWatch] WARNING: DO giảm nhanh (> 0.25 mg/L trong 5 phút) | **Triggered** 12:04:59 trên `pond:pond-01`, **Recovered** 12:10:59 |
| Monitor | `339209647` | [ShrimpWatch] DO thấp (Warning dưới 4, Critical dưới 3 mg/L) | OK |
| Monitor | `339209657` | [ShrimpWatch] WARNING: pH ngoài ngưỡng 7.5-8.5 | OK |
| Monitor | `339209664` | [ShrimpWatch] WARNING: Độ mặn thấp (dưới 10 ppt) | **Triggered** 12:03:24 trên `pond:pond-03`, **Recovered** 12:09:24 |
| Monitor | `339209670` | [ShrimpWatch] WARNING: Nhiệt độ nước ngoài ngưỡng 26-32 °C | OK |

Log đã về Datadog: truy vấn `service:shrimpwatch` (30 phút gần nhất) trả về bản ghi có `dd.trace_id` — xác nhận **Log Management + Log↔Trace correlation** hoạt động.

### 4. Kiểm thử tự động

**106 test PASSED** (unit + integration trên MongoDB 7 local), coverage **78,8%** (ngưỡng CI 64%), `ruff` sạch — 31/10/2026. Bản ghi lần chạy đầu tiên (9 test, 07/10) vẫn giữ tại [screenshots_pytest.txt](screenshots_pytest.txt).

---

## 📑 Đối chiếu yêu cầu kỹ thuật của BTC

### 6.1. MongoDB — Nền tảng dữ liệu vận hành

#### 🔴 Bắt buộc (6/6 tiêu chí)

| # | Yêu cầu BTC | ShrimpWatch đáp ứng | Bằng chứng (mã nguồn / ảnh) |
|---|---|---|---|
| 1 | **Nền tảng CHÍNH** | Toàn bộ dữ liệu sensor, sự cố, lịch sử vận hành lưu trên **MongoDB Atlas (Cloud)** | [13_mongodb_atlas_cluster.png](assets_proof/13_mongodb_atlas_cluster.png) · [14_mongodb_atlas_data.png](assets_proof/14_mongodb_atlas_data.png) |
| 2 | **Time Series Collections** | Telemetry (DO, pH, nhiệt độ, quạt) lưu trong Time Series Collection `metrics` (`timeField`, `metaField`, `granularity`) | [01_mongo_timeseries_ttl.txt](assets_proof/01_mongo_timeseries_ttl.txt) · [schema.py](app/db/schema.py#L38-L68) |
| 3 | **Aggregation & Window Functions** | `$setWindowFields` + `$derivative` tính tốc độ sụt DO theo cửa sổ thời gian 5 phút; `$group` + `$merge` rollup theo giờ; `$lookup` + `$sortArray` dựng timeline sự cố | [pipelines.py](app/db/pipelines.py) (`compute_do_drop_rate_pipeline`, `rollup_hourly_pipeline`, `incident_timeline_pipeline`) |
| 4 | **expireAfterSeconds (TTL)** | TTL tự động xoá telemetry thô sau 7 ngày (`604800` giây); rollup `metrics_hourly` giữ 1 năm | [01_mongo_timeseries_ttl.txt](assets_proof/01_mongo_timeseries_ttl.txt) · [schema.py](app/db/schema.py) (`init_collections`) |
| 5 | **Mô hình & Truy cập hiệu quả** | Time Series (bucket do engine quản lý), index ghép ESR `pond_metric_ts`, 1 aggregate cho cả 6 ao, rollup thay cho quét dữ liệu thô — xem [Mẫu truy cập & quyết định thiết kế](#mẫu-truy-cập--quyết-định-thiết-kế) | [schema.py](app/db/schema.py) · [pipelines.py](app/db/pipelines.py) · [step6_index_and_buckets.js](scripts/step6_index_and_buckets.js) |
| 6 | **Gắn chặt với vận hành** | MongoDB điều khiển toàn bộ state machine của sự cố (`open → investigating → resolving → recovered`) | [04_swagger_ui_incidents.png](assets_proof/04_swagger_ui_incidents.png) · [06_mongodb_document_incident.png](assets_proof/06_mongodb_document_incident.png) |

#### 🟡 Tùy chọn

| Yêu cầu BTC | ShrimpWatch đáp ứng | Bằng chứng |
|---|---|---|
| Xử lý hướng sự kiện | Mọi thay đổi trạng thái sự cố ghi vào collection `events` (khoá theo `incident_id`), dựng lại thành timeline qua `GET /incidents/{id}/timeline` | [05_mongodb_event_log.png](assets_proof/05_mongodb_event_log.png) |
| **Atlas Vector Search** | `$vectorSearch` trên fingerprint chất lượng nước của sự cố → gợi ý cách xử lý từ sự cố cũ ở bước Điều tra (có fallback aggregation) | [incident_fingerprint.py](app/domain/incident_fingerprint.py) · [pipelines.py](app/db/pipelines.py) (`similar_incidents_pipeline`) |
| Change Streams / mở rộng | Cấu trúc dữ liệu sẵn sàng tích hợp Change Streams cho thông báo thời gian thực | [app/db/](app/db) |

### 6.2. Datadog — Quan sát & Cảnh báo

#### 🔴 Bắt buộc (4/4 tiêu chí)

| # | Yêu cầu BTC | ShrimpWatch đáp ứng | Bằng chứng |
|---|---|---|---|
| 1 | **Ít nhất 1 Dashboard** | Dashboard tổng quan sức khỏe 6 ao: KPI sự cố, nhiệt độ, DO, pH, độ kiềm, CO₂, công suất quạt, heatmap | [07_datadog_dashboard.png](assets_proof/07_datadog_dashboard.png)   [07_datadog_dashboard_2.png](assets_proof/07_datadog_dashboard_2.png)   [07_datadog_dashboard_3.png](assets_proof/07_datadog_dashboard_3.png)   · [shrimpwatch_overview.json](datadog/dashboards/shrimpwatch_overview.json) |
| 2 | **Ít nhất 1 Monitor/Alert** | Monitor **CRITICAL** khi quạt hỏng (`pump_power < 50W`) và **WARNING** khi oxy sụt bất thường | [08_datadog_monitor_alert.png](assets_proof/08_datadog_monitor_alert.png) · [pump_failure_critical.json](datadog/monitors/pump_failure_critical.json) |
| 3 | **Năng lực bổ sung** | Tích hợp đồng thời Metrics (DogStatsD), Log Management, APM và Distributed Tracing | [09_datadog_correlation_log_trace_metric.png](assets_proof/09_datadog_correlation_log_trace_metric.png) |
| 4 | **Kết nối Metrics/Logs/Traces** | Mỗi log mang `dd.trace_id`; span được gắn tag `pond`, `device_type` → điều tra từ cảnh báo tới dòng code | [09_datadog_correlation_log_trace_metric.png](assets_proof/09_datadog_correlation_log_trace_metric.png) · [log.py](app/core/log.py) |

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
| 1 | `01_mongo_timeseries_ttl.txt` | Cấu hình Time Series + TTL, index và Vector Search index đọc trực tiếp từ Atlas (31/10/2026) |
| 2 | `02_pytest_9_xanh.png` | Kết quả kiểm thử lần đầu (9/9 passed, 07/10); kết quả hiện tại: 106 passed — xem [Kiểm thử tự động](#-kiểm-thử-tự-động) |
| 3 | `03_agent_status.png` | Trạng thái Datadog Agent |
| 4 | `04_swagger_ui_incidents.png` | Giao diện quản lý sự cố (Incident Management) |
| 5 | `05_mongodb_event_log.png` | Nhật ký sự kiện (collection `events`) |
| 6 | `06_mongodb_document_incident.png` | Cấu trúc Document của một sự cố (có `evidence`, `actions`) |
| 7 | `07_datadog_dashboard.png` | Dashboard — Phần 1: KPI & sức khỏe hệ thống |
| 8 | `07_datadog_dashboard_2.png` | Dashboard — Phần 2: Nhiệt độ, DO, pH |
| 9 | `07_datadog_dashboard_3.png` | Dashboard — Phần 3: Độ kiềm, CO₂, quạt nước, heatmap |
| 10 | `08_datadog_monitor_alert.png` | Cảnh báo đỏ (Alert) khi quạt nước hỏng |
| 11 | `09_datadog_correlation_log_trace_metric.png` | Truy vết Log ↔ Trace ↔ Metric |
| 12 | Video demo (link ở [Thông tin nộp bài](#-thông-tin-nộp-bài)) | Video demo trọn vẹn vòng đời sự cố |
| 13 | `13_mongodb_atlas_cluster.png` | Cụm MongoDB Atlas (Cloud, gói M0) đang hoạt động |
| 14 | `14_mongodb_atlas_data.png` | Dữ liệu cảm biến 6 ao chảy trực tiếp lên Atlas |
| 15 | `15_control_panel_incident.png` | Bảng điều khiển 31/10: ao 1 sự cố nghiêm trọng (quạt 0 W, đang xử lý), ao 3 cảnh báo độ mặn 7,8 ppt |

### 📸 Cách lấy từng ảnh

**1. `01_mongo_timeseries_ttl.txt` — Time Series + TTL** (đã có bản đọc trực tiếp từ Atlas; có thể chụp thêm ảnh theo các bước dưới)
1. Atlas → **Database** → cluster0 → **Browse Collections** → nút **`>_` (Open MongoDB Shell)**.
2. Dán toàn bộ nội dung [scripts/step4_check_ts_ttl_ponds.js](scripts/step4_check_ts_ttl_ponds.js) rồi Enter.
3. Chụp phần kết quả in ra: `timeseries.timeField`, `metaField`, `granularity` và `expireAfterSeconds TTL : 604800 (~ 7 ngày)`.

**2. `02_pytest_9_xanh.png` — Kiểm thử**
1. Chạy: `make test` (chạy trên database `shrimpwatch_test` của container MongoDB local, không đụng tới Atlas)
2. Chụp dòng `9 passed`.
> ⚠️ **Cảnh báo:** bộ test gọi `init_collections(force=True)` nên sẽ **xoá sạch collection** trong database đang kết nối. `make test` đã ghi đè `MONGODB_URI`/`MONGODB_DB` về MongoDB local; đừng chạy `pytest` trực tiếp khi `.env` trỏ lên Atlas.

> 🔁 **CI (GitHub Actions):** mỗi PR và mỗi lần push lên `main` chạy 3 job — `Lint (ruff)` (= `make lint`), `Tests (MongoDB 7)` với coverage tối thiểu 64% ([.coveragerc](.coveragerc)), và `Compose smoke test` (dựng stack thật, kiểm tra dữ liệu chảy vào `metrics` / `metrics_hourly`). Xem [.github/workflows/ci.yml](.github/workflows/ci.yml).

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
> Cần chạy `docker compose exec -T api python -m app.cli.demo_incident` trước để có đủ 4 bước trong `actions`.

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
2. Chạy `docker compose exec -T api python -m app.cli.demo_incident` (log hiện đủ 4 bước).
3. Mở song song tab Datadog Monitor và Atlas → `incidents` để thấy trạng thái đổi theo thời gian thực.

**13. `13_mongodb_atlas_cluster.png` — Cluster Atlas**
1. Atlas → **Database** → danh sách cluster.
2. Chụp thẻ **cluster0** kèm nhãn gói **M0 / Free**, region và nút *Connect*.

**14. `14_mongodb_atlas_data.png` — Dữ liệu trên Cloud**
1. Atlas → **Browse Collections** → `shrimpwatch` → `metrics`.
2. Chụp danh sách document mới nhất (có `timestamp` vài giây trước, `metadata.pond_id` của 6 ao).

---

## 🧪 Kiểm thử tự động

```bash
make test                      # toàn bộ (unit + integration) trên DB test của MongoDB local
pytest tests/unit              # chỉ unit test, không cần MongoDB
```

- `tests/unit/`: công thức hoá học nước, quy tắc phát hiện sự cố, vòng đời sự cố, mô hình simulator, cấu trúc pipeline.
- `tests/integration/`: chạy với MongoDB thật qua API và worker.

Bộ test integration bao phủ: health check, seed collection, cấu hình Time Series + TTL, ghi dữ liệu cảm biến, nhập độ kiềm/TAN/nitrite thủ công, pipeline `$setWindowFields`, pipeline biên độ pH, công thức suy ra CO₂ và NH₃, DO/ORP giảm khi quạt hỏng, mở sự cố khi quạt hỏng vòng đời 4 trạng thái (kể cả sự cố ở `resolving` được worker tự đóng), Vector Search và bảng điều khiển — **106 test PASSED**, coverage 78,8%.

> ⚠️ **Lưu ý quan trọng:** fixture `setup_db` gọi `init_collections(force=True)` → **xoá toàn bộ collection** của database đang kết nối. Không chạy lệnh này khi `.env` đang trỏ lên MongoDB Atlas đang chứa dữ liệu báo cáo.

---


## 🧾 Khai báo thành phần tái sử dụng, dataset & công cụ AI

Theo thể lệ, đội khai báo các phần có sẵn trước thời gian build, thư viện, dữ liệu và công cụ AI đã dùng.

### Mã có trước thời gian build (17/10 – 31/10/2026)

| Thời điểm | Phần đã có | Ghi chú |
|---|---|---|
| 07/10 – 09/10/2026 | Bản nguyên mẫu v1.0 (các commit đầu tiên của repo): API FastAPI nhận dữ liệu, simulator 6 ao, worker tính DO drop rate bằng `$setWindowFields`, Time Series Collection + TTL, đẩy metric/log/trace lên Datadog, vòng đời sự cố 4 trạng thái cơ bản, 9 test | Viết dạng một file mỗi phần (`app/db.py`, `app/worker.py`, `app/simulator.py`, …). Lịch sử đầy đủ trong `git log` |

Làm trong thời gian build (20/10 – 31/10/2026): chuyển sang MongoDB Atlas; tách kiến trúc theo tầng (`core`/`domain`/`db`/`services`/`api`/`worker`); chỉ số mới (độ mặn, ORP, TAN, nitrite, NH₃, biên độ pH 24h) và rollup theo giờ `$merge`; kiểm tra chuyển trạng thái sự cố (409) và nâng mức sự cố không mở trùng; **Atlas Vector Search** gợi ý sự cố tương tự; **bảng điều khiển simulator** và API kịch bản; 6 monitor môi trường + dashboard đẩy theo kiểu upsert; CI (lint, test MongoDB 7, smoke test Docker Compose); bộ test từ 9 lên 106.

### Thư viện & thành phần mã nguồn mở

Dùng nguyên trạng theo giấy phép của từng dự án, không sửa mã nguồn: FastAPI, Uvicorn, Pydantic / pydantic-settings, PyMongo / Motor, dnspython, python-json-logger, datadog (DogStatsD), ddtrace, httpx, python-dotenv, pytest, ruff (xem [requirements.txt](requirements.txt), [requirements-dev.txt](requirements-dev.txt)). Docker image chính thức: `mongo:7`, `gcr.io/datadoghq/agent:7.59.0`, `python:3.11-slim`. Swagger UI do FastAPI phục vụ.

### Dữ liệu

- **Không dùng dữ liệu thật hay dataset bên ngoài.** Toàn bộ số liệu cảm biến do simulator sinh ra ([pond_model.py](app/simulator/pond_model.py)); lịch sử sự cố dùng cho Vector Search là dữ liệu tổng hợp đánh dấu `notes.synthetic = true` ([seed_incident_history.py](app/cli/seed_incident_history.py)).
- Công thức NH₃ tự do theo Emerson et al. (1975), *Aqueous ammonia equilibrium calculations*; CO₂ từ pH và độ kiềm theo cân bằng carbonate. Ngưỡng vận hành (DO, pH, độ mặn, nhiệt độ) theo khuyến nghị chung cho nuôi tôm thẻ chân trắng.

### Công cụ AI

| Công cụ | Dùng để |
|---|---|
| **Claude Code** (Anthropic, model Claude Opus) | Hỗ trợ viết và refactor code, viết test, rà soát và kiểm thử end-to-end (Playwright), cập nhật tài liệu. Các commit có AI hỗ trợ ghi `Co-Authored-By: Claude` |
| **GitHub Copilot code review** | Review pull request theo [.github/copilot-instructions.md](.github/copilot-instructions.md) |
| **DeepSeek** (GitHub Action) | Review tự động trên pull request |

Ứng dụng **không gọi mô hình AI khi chạy**: fingerprint cho Vector Search là vector 6 chiều tính bằng công thức từ `evidence`. Đội đã đọc, kiểm tra và hiểu toàn bộ mã được AI gợi ý.

---

## 💻 Công nghệ sử dụng

- **Backend:** FastAPI 0.115, Pydantic v2, Uvicorn.
- **Database:** MongoDB Atlas M0 — Time Series Collections, Aggregation Pipeline (`$setWindowFields`, `$group`), TTL Index, Replica Set.
- **Observability:** Datadog Agent 7.59 — DogStatsD metrics, APM (`ddtrace`), Log Management, Distributed Tracing.
- **Hạ tầng:** Docker, Docker Compose, Makefile.

---

**Chúc bạn một kỳ Hackathon thành công rực rỡ! 🦐🚀**
