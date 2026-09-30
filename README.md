# Synthio

Ask questions about the [Chinook](https://github.com/lerocha/chinook-database) music store in
plain English and get plain-English answers, backed by rows from PostgreSQL.

> **You:** Who are the top 3 artists by revenue?
> **Synthio:** Iron Maiden leads with $138.60, followed by U2 ($105.93) and Metallica ($90.09).
> ▸ *Evidence · 3 records*

- **Read-only.** Requests to change data are refused. Every generated query passes a SQL guard
  and runs as a SELECT-only role in a read-only transaction.
- **Grounded.** Answers come only from query results, and the rows are returned as evidence.
  If the data can't answer the question, the reply is `INFO not available`.
- **No schema leaks.** Table names, column names, SQL and database errors never reach the user.
- **Follow-ups.** Chat sessions keep recent turns, so questions like "and in 2023?" work.

## Contents

- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Getting started](#getting-started)
- [Using Synthio](#using-synthio)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Operations](#operations)
- [Security model](#security-model)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)

## How it works

```
question ─▶ decision model ─▶ SQL agent ─▶ SQL guard ─▶ Postgres (read-only) ─▶ answer writer ─▶ answer + evidence
               │                 │
               │                 └─ tools: describe_tables · execute_query · report_not_available
               └─ out of scope / write request ─▶ polite refusal
```

1. **Decision model** (`GATE_MODEL`, default `gpt-6-luna`) checks whether the question is about
   the store's data and read-only. It also rewrites follow-ups as complete standalone questions.
2. **SQL agent** (`PLANNER_MODEL`, default `gpt-6.1-sol`) looks up the tables it needs, then
   writes and runs one SELECT. If a query fails it tries again, up to 3 queries per question.
3. **Answer writer** (`ANSWER_MODEL`, default `gpt-6-luna`) turns the result rows into a short
   answer without mentioning the schema.

| Service | Tech                          | Port | Exposed to host |
|---------|-------------------------------|------|-----------------|
| `ui`    | Next.js (chat page + proxy)   | 3000 | Yes             |
| `api`   | Python 3.12, FastAPI, OpenAI  | 8000 | No              |
| `db`    | PostgreSQL 16 + Chinook seed  | 5432 | No              |

The browser only talks to `ui`. Its `/api/chat` route forwards requests to `api` over the
internal Docker network. Diagrams and design notes are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Prerequisites

- Docker with Compose v2 (`docker compose version`)
- An OpenAI API key with access to the configured models
- Git, and free port `3000` on the host

## Getting started

### 1. Clone the repo and the Chinook seed data

The database is seeded from the `chinook-database` repo. It is committed as a pinned
submodule pointer without a `.gitmodules` file, so fetch it yourself:

```bash
git clone https://github.com/Mrutyunjay01/text2sql-chat.git synthio
cd synthio
git clone https://github.com/Mrutyunjay01/chinook-database.git chinook-database
git -C chinook-database checkout "$(git ls-tree HEAD chinook-database | awk '{print $3}')"
```

Check that the seed file exists:

```bash
ls chinook-database/ChinookDatabase/DataSources/Chinook_PostgreSql.sql
```

### 2. Configure

```bash
cp .env.example .env
```

Set at least these three values in `.env`:

```dotenv
OPENAI_API_KEY=sk-...
POSTGRES_PASSWORD=<strong password>      # superuser, used only to seed the db
CHINOOK_RO_PASSWORD=<another password>   # read-only role the api connects as
```

`docker compose` refuses to start until all three are set. See [Configuration](#configuration)
for the other settings.

### 3. Start

```bash
docker compose up -d --build
```

The services start in order, each waiting for the previous one to be healthy:

1. `db` seeds Chinook and creates the read-only role. This happens only on the first start
   and takes up to a minute.
2. `api` loads the schema and serves `/api/health`.
3. `ui` starts on port 3000.

Watch progress with:

```bash
docker compose ps          # all three should reach "running" / "healthy"
docker compose logs -f api # look for "schema loaded: 11 tables"
```

### 4. Open the app

Go to <http://localhost:3000>, pick one of the suggested questions or type your own.

Smoke-test from the command line:

```bash
curl -s localhost:3000/api/chat -H 'Content-Type: application/json' \
  -d '{"query": "Which genre has the most tracks?"}'
```

## Using Synthio

### In the browser

- Ask one question per message. Answers arrive in a few seconds, depending on the model.
- Expand **Evidence** under an answer to see the rows it was based on. Only the first 50 rows
  are shown, and the note says when there are more.
- Follow-ups use the previous turns: "Top 5 artists by sales" → "only for 2024".
- **New chat** starts a fresh session with no history.

### What you can ask

The data covers a digital music store from 2021 to 2025: artists, albums, tracks, genres,
media types, playlists, customers, invoices and employees.

| Works well | Response |
|------------|----------|
| "How much revenue did we make in 2023?" | Answer + evidence |
| "Which employee supports the most customers?" | Answer + evidence |
| "Which countries have more than 5 customers?" | Answer + evidence |
| "What were sales in 2019?" | `INFO not available` (no data for that year) |
| "What's the weather in Paris?" | `Not in scope: …` |
| "Delete all invoices from 2021" | `Not allowed: …` |

## API reference

The UI's proxy is the only public endpoint. `api` also serves `GET /api/health`, but it is
reachable only inside the Docker network.

### `POST /api/chat`

Request:

```json
{
  "query": "Who are the top 3 artists by revenue?",
  "session_id": "3f0c…"
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `query` | string, 1–1000 chars | Yes | `request` is accepted as an alias |
| `session_id` | string | No | From a previous response. If it is missing, unknown or expired, a new session starts |

Response (`200 OK`):

```json
{
  "session_id": "3f0c…",
  "status": "ok",
  "results": "Iron Maiden leads with $138.60, followed by U2 ($105.93) and Metallica ($90.09).",
  "evidence": {
    "row_count": 3,
    "truncated": false,
    "rows": [{"Artist": "Iron Maiden", "Revenue": 138.6}, "…"]
  },
  "request_id": "6f5daf546c78"
}
```

| `status` | Meaning | `results` | `evidence` |
|----------|---------|-----------|------------|
| `ok` | The query ran and returned rows | Natural-language answer | Rows |
| `not_available` | No matching data, 0 rows, or retries used up | `INFO not available` | `null` |
| `not_in_scope` | The question isn't about the store's data | `Not in scope: …` | `null` |
| `not_allowed` | The question asks to change data | `Not allowed: …` | `null` |
| `error` | An upstream failure, e.g. an OpenAI or database error | Generic message | `null` |

Notes on the response:

- `evidence.rows` holds at most `EVIDENCE_ROWS` rows. `row_count` is the number of rows the
  query returned, which is capped at `MAX_ROWS`. `truncated: true` means the query hit that cap.
- `evidence.sql` is included only when `EXPOSE_SQL=true`.
- `request_id` shows up in the `api` logs. Include it when reporting a problem.
- Validation errors return `422`. If `ui` can't reach `api` it returns `502 {"detail": "Backend unavailable"}`.
  The proxy waits up to 120 s.

Multi-turn example:

```bash
SID=$(curl -s localhost:3000/api/chat -H 'Content-Type: application/json' \
  -d '{"query": "Top 3 artists by revenue"}' | jq -r .session_id)

curl -s localhost:3000/api/chat -H 'Content-Type: application/json' \
  -d "{\"query\": \"only in 2024\", \"session_id\": \"$SID\"}" | jq .results
```

## Configuration

All settings are environment variables read from `.env`. The first table lists the ones
wired through `docker-compose.yml`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | — | **Required.** OpenAI API key |
| `POSTGRES_PASSWORD` | — | **Required.** Superuser password, used only while seeding |
| `CHINOOK_RO_PASSWORD` | — | **Required.** Password for the api's read-only role |
| `POSTGRES_USER` | `chinook` | Superuser name |
| `CHINOOK_RO_USER` | `chinook_ro` | Read-only role name |
| `GATE_MODEL` | `gpt-6-luna` | Decision model |
| `PLANNER_MODEL` | `gpt-6.1-sol` | SQL agent |
| `ANSWER_MODEL` | `gpt-6-luna` | Answer writer |
| `GATE_EFFORT` / `PLANNER_EFFORT` / `ANSWER_EFFORT` | `low` / `medium` / `low` | Reasoning effort per step: `none` (luna only), `low`, `medium`, `high`, `xhigh`, `max` |
| `EXPOSE_SQL` | `false` | Add the executed SQL to evidence. This reveals the schema |

The `api` also reads the settings below. To change one, add it to the `api.environment`
block in `docker-compose.yml`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MAX_ROWS` | `200` | Row cap per query |
| `EVIDENCE_ROWS` | `50` | Rows returned as evidence |
| `STATEMENT_TIMEOUT_MS` | `5000` | Per-query timeout |
| `MAX_AGENT_TURNS` | `8` | Tool-call turns for the SQL agent |
| `MAX_QUERY_ATTEMPTS` | `3` | Queries the agent may run per question |
| `SESSION_TTL_SECONDS` | `1800` | Idle time before a session expires |
| `SESSION_MAX_TURNS` | `10` | Turns of history kept per session |
| `MAX_SESSIONS` | `1000` | Sessions held in memory. The least recently used are evicted first |

Changes to `.env` take effect after `docker compose up -d`, which recreates the changed
containers. Passwords are applied only when the database is first seeded (see
[Reset the database](#reset-the-database)).

## Operations

### Common commands

```bash
docker compose ps                   # status and health
docker compose logs -f api          # pipeline logs, one line per step, tagged with request_id
docker compose restart api          # restart; clears in-memory sessions
docker compose up -d --build        # rebuild after code changes
docker compose down                 # stop; keeps the database volume
```

### Reset the database

The seed and the read-only role are created only when the `pgdata` volume is empty. To
reseed, for example after changing a DB password:

```bash
docker compose down -v
docker compose up -d --build
```

### Deploying beyond localhost

This stack is set up for a single host. Before putting it on a network:

- Put `ui` behind a TLS-terminating reverse proxy, and add authentication and rate limiting.
  The app has none of these built in, and every request costs OpenAI tokens.
- Use strong, unique database passwords, and keep `.env` out of version control
  (it is already in `.gitignore`).
- Run a single `api` replica, or replace the in-memory session store first
  (see [Limitations](#limitations)).
- Keep `EXPOSE_SQL=false` for untrusted users.

## Security model

Read-only access is enforced in three independent layers, so no single one has to be perfect:

1. **SQL guard** ([api/app/sql_guard.py](api/app/sql_guard.py)) parses every query with
   `sqlglot`. It accepts only a single SELECT over known tables and rejects writes, DDL,
   multiple statements, system catalogs and system functions (`pg_*`, `set_config`, …).
2. **Read-only transaction.** Each query runs in a `READ ONLY` transaction with a statement timeout.
3. **Database role** ([db/init/02_readonly_role.sh](db/init/02_readonly_role.sh)). `chinook_ro`
   has only `CONNECT`, `USAGE` and `SELECT`, has `default_transaction_read_only = on`, and has
   5 s statement and 10 s idle-in-transaction timeouts.

Schema details stay private because database errors are logged but never returned, and the
answer writer is told not to mention tables or columns.

## Development

### Tests

```bash
docker compose run --rm api python -m pytest -q tests
```

These cover the SQL guard: allowed reads, and rejected writes, DDL, multiple statements,
system functions and system catalogs.

### Running a service outside Docker

For faster iteration, run the api or ui on the host against the containerized database.
Publish the db port first with a `docker-compose.override.yml`:

```yaml
services:
  db:
    ports: ["5432:5432"]
```

API (Python 3.12):

```bash
cd api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
set -a && source ../.env && set +a
uvicorn app.main:app --reload --port 8000
```

UI (Node 24):

```bash
cd ui
npm ci
API_URL=http://localhost:8000 npm run dev
```

### Project layout

```
api/
  app/main.py        FastAPI app: POST /api/chat, GET /api/health
  app/pipeline.py    decision model → SQL agent → answer writer
  app/sql_guard.py   deterministic read-only check
  app/db.py          read-only connection pool and executor
  app/schema.py      table catalog, describe_tables tool
  app/sessions.py    in-memory sessions
  tests/             SQL guard tests
ui/
  app/page.tsx       chat page
  app/api/chat/      server-side proxy to the api
db/init/             read-only role setup
chinook-database/    upstream Chinook repo (seed SQL)
docs/                architecture notes and diagrams
```

## Troubleshooting

| Symptom | Likely cause and fix |
|---------|----------------------|
| `required variable ... is missing a value` on `up` | A required value is missing from `.env`. See [Configure](#2-configure) |
| `db` is never healthy; its logs show `database "chinook" does not exist` | The seed data wasn't fetched. Repeat [step 1](#1-clone-the-repo-and-the-chinook-seed-data), then `docker compose down -v && docker compose up -d` |
| `api` exits with `password authentication failed` | `CHINOOK_RO_PASSWORD` changed after the first seed. [Reset the database](#reset-the-database) |
| Every answer has `status: "error"` | Check `docker compose logs api` for OpenAI errors: invalid key, no model access or rate limits |
| UI shows **Backend unavailable** | `api` is down or still starting. Run `docker compose ps` and check the `api` logs |
| `Bind for 0.0.0.0:3000 failed` | Port 3000 is in use. Change the `ui` port mapping, e.g. `"8080:3000"` |
| A follow-up loses context | The session expired (30 min idle) or `api` restarted |

## Limitations

- Sessions are held in memory. They are lost on restart and not shared across `api` replicas.
- The data covers 2021–2025, so questions about other years return `INFO not available`.
- Each question produces one SQL query. Questions that need several independent lookups
  may be answered only partly.
- There is no built-in authentication or rate limiting (see
  [Deploying beyond localhost](#deploying-beyond-localhost)).
