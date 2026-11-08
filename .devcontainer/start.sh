#!/usr/bin/env bash
# Start the stack in a Codespace. Safe to re-run (postStartCommand runs every time the codespace starts).
set -euo pipefail
cd "$(dirname "$0")/.."

# .env is git-ignored: recreate it from .env.example on every start, then overwrite with the current Codespaces secrets,
# so a deleted or renamed secret does not leave a stale value in .env
cp .env.example .env
set_env() {
  local key=$1 value=$2
  [ -n "$value" ] || return 0
  grep -v "^${key}=" .env > .env.tmp || true
  printf '%s=%s\n' "$key" "$value" >> .env.tmp
  mv .env.tmp .env
}
set_env MONGODB_URI "${MONGODB_URI:-}"
set_env DD_API_KEY "${DD_API_KEY:-}"
set_env DD_APP_KEY "${DD_APP_KEY:-}"
set_env DD_SITE "${DD_SITE:-}"
set_env DD_ENV codespaces

# Docker-in-docker may not be ready right after the codespace starts
for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done

if [ -n "${DD_API_KEY:-}" ]; then
  set_env DD_TRACE_ENABLED true
  services=""   # everything, including the Datadog Agent
else
  # No Agent: disable tracing and send metrics to localhost (like CI) to avoid waiting on DNS
  echo "DD_API_KEY not set: starting without the Datadog Agent"
  export COMPOSE_FILE=docker-compose.yml:docker-compose.ci.yml
  set_env DD_TRACE_ENABLED false
  services="mongodb api simulator worker"
fi

chmod 600 .env

# shellcheck disable=SC2086
docker compose up -d --build $services

echo
echo "ShrimpWatch is starting. API: port 8000 (Ports tab) -> /docs, /control, /healthz"
echo "Logs: docker compose logs -f api worker simulator"
