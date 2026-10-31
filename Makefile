.PHONY: up down build logs api simulator worker reset demo test lint fmt init-mongo seed-history push-dashboards help

SHELL := /bin/bash

help:
	@echo "Available targets:"
	@echo "  up            - Start all services with Docker Compose"
	@echo "  down          - Stop all services"
	@echo "  build         - Rebuild all images"
	@echo "  logs          - Tail all service logs"
	@echo "  api           - Restart API service"
	@echo "  simulator     - Restart simulator service"
	@echo "  worker        - Restart worker service"
	@echo "  init-mongo    - Initialize MongoDB collections (Time Series + TTL)"
	@echo "  seed-history  - Load synthetic recovered incidents for similar-incident search"
	@echo "  demo          - Run demo incident (pump failure -> oxygen drop)"
	@echo "  push-dashboards - Upload Datadog dashboard & monitor"
	@echo "  test          - Run pytest"
	@echo "  lint          - Ruff lint + format check (same as CI)"
	@echo "  fmt           - Ruff auto-fix + format"
	@echo "  reset         - Wipe MongoDB data and re-init"

up:
	docker compose up -d
	@echo "Waiting for services..."
	@sleep 5
	@echo "Services running. Check logs with 'make logs'"

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f --tail=100

api:
	docker compose restart api

simulator:
	docker compose restart simulator

worker:
	docker compose restart worker

init-mongo:
	docker compose exec -T api python -m app.cli.init_db

seed-history:
	docker compose exec -T api python -m app.cli.seed_incident_history

demo:
	docker compose exec -T api python -m app.cli.demo_incident

push-dashboards:
	docker compose exec -T api python -m app.cli.datadog_push

# Test xoa sach collection -> luon chay tren DB test cua MongoDB local, khong dung .env (Atlas)
test:
	docker compose exec -T -e MONGODB_URI='mongodb://admin:admin_pass@mongodb:27017/?authSource=admin' \
		-e MONGODB_DB=shrimpwatch_test -e DD_TRACE_ENABLED=false api pytest -v tests/

RUFF := docker run --rm -u $(shell id -u):$(shell id -g) -v $(CURDIR):/src -w /src ghcr.io/astral-sh/ruff:0.7.4

lint:
	$(RUFF) check --no-cache app tests
	$(RUFF) format --no-cache --check app tests

fmt:
	$(RUFF) check --no-cache --fix app tests
	$(RUFF) format --no-cache app tests

reset:
	docker compose down -v
	docker compose up -d mongodb
	@sleep 5
	docker compose exec -T api python -m app.cli.init_db
	docker compose up -d
