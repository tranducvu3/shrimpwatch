# Security

ShrimpWatch is a hackathon project (MUG Vietnam Mini Hackathon 2026). It runs on simulated data and is not
hardened for production. This file describes what is in scope, what is a known gap, and what code review
should catch.

## Reporting a vulnerability

Do not open a public issue for a security problem. Use **Security → Report a vulnerability** on the GitHub
repository, or contact the code owners listed in `.github/CODEOWNERS` directly.

## Secrets

- Real values live only in `.env` (git-ignored) or in GitHub Actions / Codex secrets. `.env.example` holds
  placeholders only.
- Secrets in this project: `DD_API_KEY`, `DD_APP_KEY`, and any `MONGODB_URI` that contains a password
  (especially MongoDB Atlas `mongodb+srv://` URIs).
- If a secret is committed, rotate it first (Datadog → Organization Settings → API/Application Keys; Atlas →
  Database Access), then remove it from the code. Deleting the commit does not make it safe.

## Known gaps (accepted for the demo)

Reviews should not re-report these unless a change makes them worse:

- The API has no authentication, and CORS allows all origins (`app/api/__init__.py`). Anyone who can reach
  port 8000 can ingest data, fail a pump, or change incident state.
- Local Docker credentials are hard-coded: `admin` / `admin_pass` in `docker-compose.yml`, `shrimp_app` /
  `shrimp_pass` in `mongo-init.js`. They are for the local container only and must never be used for Atlas.
- `shrimp_app` has `dbOwner` on `shrimpwatch` plus `readAnyDatabase`, which is more than the app needs.
- MongoDB and the Datadog agent ports are published on the host by Docker Compose.

Anything that deploys this stack somewhere reachable from the internet must fix the first two first.

## Security review guidelines

Flag as high priority:

1. **Committed secrets**: API keys, app keys, tokens, or Mongo URIs with real passwords in any file,
   including tests, Datadog JSON, scripts, screenshots and docs.
2. **Secrets in logs or responses**: logging settings objects, request headers (`DD-API-KEY`,
   `DD-APPLICATION-KEY`), full Mongo URIs, or exception messages that contain them; returning them from an
   endpoint such as `/healthz`.
3. **NoSQL injection**: request data used as a query or update document without Pydantic validation; query
   operators (`$where`, `$function`, `$accumulator`, `$expr`, `$regex`) built from user input; path or query
   parameters passed through as dicts.
4. **Destructive operations** reachable over HTTP or run outside the test DB: `drop`, `delete_many`,
   `init_collections(force=True)`, `docker compose down -v`.
5. **Unbounded input**: removing or raising the 1000-record limit on `/ingest/batch`, list endpoints without
   a `limit`, or aggregations that can scan the full `metrics` collection per request.
6. **Widened exposure**: new endpoints that change state, new published ports, broader Mongo roles, or
   relaxed CI permissions (`permissions:` in workflows, `pull_request_target`, untrusted input in `run:`).
7. **Dependencies**: new packages without a pinned version, or downgrades of pinned ones in `requirements*.txt`.
8. **Containers**: running as root where it is easy to avoid, `--reload` or debug settings outside local
   development, mounting more of the host (the Datadog agent already mounts the Docker socket).
