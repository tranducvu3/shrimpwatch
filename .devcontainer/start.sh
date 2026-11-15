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
for key in MONGODB_URI DD_API_KEY DD_APP_KEY DD_SITE DD_DASHBOARD_ID FARMER_ALERT_EMAILS AUTOMATION_TOKEN \
  DD_RUM_APPLICATION_ID DD_RUM_CLIENT_TOKEN; do
  set_env "$key" "${!key:-}"
done
set_env DD_ENV codespaces
# The forwarded address of port 8000: alert links and the Datadog workflow call the API there.
# Datadog's cloud and the farmer's phone only reach it once the port is Public.
if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  set_env PUBLIC_BASE_URL "https://${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
fi

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
