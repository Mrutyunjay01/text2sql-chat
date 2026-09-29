# Synthio

Ask questions about the [Chinook](chinook-database/) music store in plain English and get
plain-English answers, grounded in data from PostgreSQL.

- **Read-only:** requests to change data are refused. Every query is checked and runs
  as a SELECT-only role in a read-only transaction.
- **Grounded:** answers come only from query results, with the rows attached as evidence.
  If the data can't answer the question, the reply is `INFO not available`.
- **No schema leaks:** table names, column names, SQL and database errors never reach the user.
- **Follow-ups:** in-memory chat sessions, so questions like "and in 2023?" work.

## How it works

```
question ─▶ decision model ─▶ SQL agent ─▶ SQL guard ─▶ Postgres (read-only) ─▶ answer writer ─▶ answer + evidence
               │                 │
               │                 └─ tools: describe_tables · execute_query · report_not_available
               └─ out of scope / write request ─▶ polite refusal
```

1. **Decision model** (`gpt-6-luna`): decides whether the question is about the store's data
   and read-only, and rewrites follow-ups as complete standalone questions.
2. **SQL agent** (`gpt-6.1-sol`): looks up the tables it needs, writes one SELECT and runs
   it. When a query fails it retries, up to 3 queries.
3. **Answer writer** (`gpt-6-luna`): turns the result rows into a short answer, without the schema.

Diagrams and design details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Stack

| Service | Tech                       | Port                  |
|---------|----------------------------|-----------------------|
| `ui`    | Next.js                    | `3000` (only exposed port) |
| `api`   | Python, FastAPI, OpenAI    | `8000` (internal)     |
| `db`    | PostgreSQL 16 + Chinook    | `5432` (internal)     |

## Quick start

```bash
cp .env.example .env    # set OPENAI_API_KEY, POSTGRES_PASSWORD, CHINOOK_RO_PASSWORD
docker compose up -d --build
open http://localhost:3000
```

The database seeds on the first start. To reseed, run `docker compose down -v`.

## API

```bash
curl -s localhost:3000/api/chat -H 'Content-Type: application/json' \
  -d '{"query": "Who are the top 3 artists by revenue?"}'
```

```json
{
  "session_id": "3f0c…",
  "status": "ok",
  "results": "Iron Maiden leads with $138.60, followed by U2 ($105.93) and Metallica ($90.09).",
  "evidence": {"row_count": 3, "truncated": false,
               "rows": [{"Artist": "Iron Maiden", "Revenue": 138.6}, "…"]},
  "request_id": "6f5daf546c78"
}
```

To continue the conversation, send the returned `session_id` with the next question.
`status` is one of `ok`, `not_available`, `not_in_scope`, `not_allowed` or `error`.

## Configuration

All settings are in `.env` (see [.env.example](.env.example)):

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | — | Required |
| `GATE_MODEL` / `PLANNER_MODEL` / `ANSWER_MODEL` | `gpt-6-luna` / `gpt-6.1-sol` / `gpt-6-luna` | Model per step |
| `GATE_EFFORT` / `PLANNER_EFFORT` / `ANSWER_EFFORT` | `low` / `medium` / `low` | Reasoning effort per step |
| `EXPOSE_SQL` | `false` | Include the executed SQL in evidence (reveals the schema) |
| `POSTGRES_PASSWORD`, `CHINOOK_RO_PASSWORD` | — | DB superuser (init only) and the api's read-only role |

## Tests

```bash
docker compose run --rm api python -m pytest -q tests
```

These cover the SQL guard: allowed reads, and rejected writes, DDL, multiple statements,
system functions and system catalogs.

## Project layout

```
api/            FastAPI backend (pipeline, SQL guard, read-only executor, sessions)
ui/             Next.js chat page + /api/chat proxy
db/init/        Read-only role setup
chinook-database/  Upstream Chinook repo (seed SQL)
docs/           Architecture and diagrams
```

## Limitations

- Sessions are held in memory: they're lost on restart and not shared across api replicas.
- The data covers 2021–2025, so questions about other years return `INFO not available`.
