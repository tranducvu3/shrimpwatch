#!/usr/bin/env bash
# Khoi dong stack that (MongoDB local, API, simulator, worker) va kiem tra luong du lieu dau-cuoi.
set -euo pipefail

API=http://localhost:8000
export COMPOSE_FILE=docker-compose.yml:docker-compose.ci.yml

cp .env.example .env
# Khong co Datadog Agent tren CI
sed -i 's/^DD_TRACE_ENABLED=.*/DD_TRACE_ENABLED=false/' .env

docker compose config --quiet
docker compose up -d --build --wait mongodb api
docker compose up -d simulator worker

echo "Waiting for the worker's first DO window..."
for _ in $(seq 1 60); do
  docker compose logs worker 2>&1 | grep -q "DO window computed" && break
  sleep 3
done
docker compose logs worker 2>&1 | grep -q "DO window computed" || { echo "Worker never computed a DO window"; exit 1; }

curl -fsS "$API/healthz" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["mongo"] is True, d; print("healthz ok")'
curl -fsS "$API/ponds" | python3 -c 'import json,sys; n=len(json.load(sys.stdin)); assert n == 6, n; print("ponds:", n)'
code=$(curl -s -o /dev/null -w '%{http_code}' "$API/incidents/inc-missing/timeline")
[ "$code" = "404" ] || { echo "timeline for unknown incident returned $code"; exit 1; }

docker compose exec -T api python - <<'EOF'
from app.db import metrics_collection, metrics_hourly_collection

raw = metrics_collection().count_documents({})
hourly = metrics_hourly_collection().count_documents({})
indexes = sorted(metrics_collection().index_information())
print(f"metrics={raw} metrics_hourly={hourly} indexes={indexes}")
assert raw > 0 and hourly > 0, "no data flowed into metrics / metrics_hourly"
assert "pond_metric_ts" in indexes
EOF

if docker compose logs worker 2>&1 | grep -E "rollup failed|DO window failed|check_pond failed"; then
  echo "Worker reported errors"
  exit 1
fi
echo "Smoke test passed"
