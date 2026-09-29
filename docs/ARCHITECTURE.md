# Synthio — Natural-Language Chat over Chinook

Users ask questions in plain English through a chat UI and get plain-English answers,
grounded in rows returned from the Chinook database (PostgreSQL). Only read-only queries
are supported. When the data can't answer a question, the reply is `INFO not available`.
See also `synthio-concept-diagram.png` for the original concept sketch.

## Decisions

| Area         | Choice                                                                 |
|--------------|------------------------------------------------------------------------|
| Database     | `postgres:16-alpine`, seeded from `chinook-database/ChinookDatabase/DataSources/Chinook_PostgreSql.sql` |
| Backend      | Python + FastAPI, one endpoint: `POST /api/chat`                        |
| LLM          | OpenAI Responses API: `gpt-6-luna` (decision model, answer writer), `gpt-6.1-sol` (SQL agent); all configurable in `.env` |
| UI           | Next.js (App Router), standalone server                                 |
| Sessions     | In memory in the api process (30 min TTL, last 10 turns kept)           |
| Evidence     | Result rows with human-readable column aliases; SQL optional (`EXPOSE_SQL`, off) |
| Hosting      | Docker Compose: `db`, `api`, `ui` containers                            |

## 1. Deployment

```
                      host :3000
                          │
┌─────────────────────────┼──────────────────── docker network: chinook_net ─┐
│                         ▼                                                  │
│   ┌──────────────────────────────┐                                         │
│   │  ui   (Next.js server)       │   /          → chat page                │
│   │  :3000                       │   /api/chat  → route handler proxies    │
│   └──────────────┬───────────────┘                to api:8000            │
│                  │ POST /api/chat                                          │
│                  ▼                                                         │
│   ┌──────────────────────────────┐        ┌──────────────────────┐         │
│   │  api  (FastAPI, Python)      │──────▶│  OpenAI API (HTTPS)   │ external │
│   │  :8000 (not exposed)         │        └──────────────────────┘         │
│   │  in-memory session store     │                                         │
│   └──────────────┬───────────────┘                                         │
│                  │ role: chinook_ro (SELECT only, read-only txn)           │
│                  ▼                                                         │
│   ┌──────────────────────────────┐                                         │
│   │  db  (postgres:16-alpine)    │  initdb.d/                              │
│   │  :5432 (not exposed)         │   01_chinook.sql                        │
│   │  volume: pgdata              │   02_readonly_role.sh                   │
│   └──────────────────────────────┘                                         │
└────────────────────────────────────────────────────────────────────────────┘
```

Seeding notes:

- Only `Chinook_PostgreSql.sql` is mounted. The `_AutoIncrementPKs` and `_SerialPKs`
  variants each run `DROP DATABASE chinook; CREATE DATABASE chinook` and would overwrite
  one another.
- The script creates and switches to its own database (`\c chinook`). `POSTGRES_DB` is set to
  `postgres`, because the script can't drop the database it is connected to.
- Data: 11 tables (`artist, album, track, genre, media_type, playlist, playlist_track,
  invoice, invoice_line, customer, employee`); invoices run from 2021-01-01 to 2025-12-22.

## 2. Request pipeline

```
 {"query": "and in 2023?", "session_id": "…"}
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ Session store: last turns of this session_id (in memory)    │
 └─────────────────────────────────────────────────────────────┘
        │
        ▼
 ┌─────────────────────────────────────────────────────────────┐
 │ 1. Decision model (gpt-6-luna, JSON schema output)          │
 │    input: table names + descriptions, history, question     │
 │    output: answerable | out_of_scope | write_request        │
 │            + standalone question ("Top artists in 2023?")   │
 └───────┬──────────────────┬──────────────────┬───────────────┘
   out_of_scope        write_request        answerable
         ▼                  ▼                  ▼
   "Not in scope"     "Not allowed"   ┌───────────────────────────────────────┐
                                      │ 2. SQL agent (gpt-6.1-sol, tool loop) │
                                      │ tools (tool registry):                │
                                      │  • describe_tables(tables)  ← table   │
                                      │    views: columns, PK, FK on demand   │
                                      │  • execute_query(sql)                 │
                                      │  • report_not_available(reason)       │
                                      └──────┬──────────────────┬─────────────┘
                                  report_not_available     execute_query(sql)
                                             │                  ▼
                                             │   ┌───────────────────────────────┐
                                             │   │ SQL guard (sqlglot, no LLM)   │
                                             │   │ • exactly 1 statement         │
                                             │   │ • SELECT / WITH / UNION only  │
                                             │   │ • no DML/DDL, SELECT INTO,    │
                                             │   │   FOR UPDATE, pg_*/dblink/... │
                                             │   │ • only known public tables    │
                                             │   └──────┬─────────────┬──────────┘
                                             │       rejected       accepted
                                             │          │             ▼
                                             │          │   ┌──────────────────────┐
                                             │          │   │ Execute as chinook_ro│
                                             │          │   │ READ ONLY txn, 5s    │
                                             │          │   │ timeout, ≤200 rows   │
                                             │          │   └──┬────────┬──────┬───┘
                                             │          │    error   0 rows  rows
                                             │          ▼      ▼        ▼      │
                                             │   error text goes back to the   │
                                             │   agent, which may retry        │
                                             │   (max 3 queries / 8 turns)     │
                                             │                 │ gives up      │
                                             ▼                 ▼               ▼
                                   ┌──────────────────────────────┐  ┌──────────────────────┐
                                   │ status "not_available"       │  │ 3. Answer writer     │
                                   │ "INFO not available"         │  │ (gpt-6-luna)         │
                                   │ no SQL, schema or DB errors  │  │ input: question +    │
                                   │ in the response              │  │ aliased rows only    │
                                   └──────────────────────────────┘  └──────────┬───────────┘
                                                                                ▼
                                                     status "ok" + results + evidence
```

- **Decision model:** besides gating, it rewrites follow-ups into complete standalone
  questions using the session history, so the SQL agent always gets a full question.
- **Table views on demand:** the SQL agent's instructions list only table names and
  descriptions. Column detail comes from `describe_tables`, so the context stays small as
  the schema grows (the concept diagram's "25 tables" case).
- **Where schema reaches the LLM:** the answer writer gets the question and rows only, never
  the schema.

### Read-only enforcement (defense in depth)

1. **SQL guard** (`api/app/sql_guard.py`): rejects anything that isn't a single `SELECT`
   over the known tables. Covered by `api/tests/test_sql_guard.py`.
2. **Transaction:** the pool sets `read_only` on every connection, and each query runs with
   `statement_timeout`, then rolls back.
3. **DB role:** `chinook_ro` has only `SELECT` on the tables, `default_transaction_read_only
   = on`, no access to other databases or `pg_shadow`, and no `CREATE` on `public`.

Read-only safety never depends on the LLM.

### No schema leakage

- The SQL agent is told to alias every output column (e.g. `AS "Revenue"`). Any column it
  forgets to alias gets a readable name (`unit_price` → `Unit price`) before leaving the api.
- SQL, DB errors and guard rejections are logged under `request_id`, never returned
  (unless `EXPOSE_SQL=true`).

## 3. API contract

```
POST /api/chat
Content-Type: application/json

{"query": "<user question>", "session_id": "<optional, from a previous response>"}
```

`"request"` is accepted as an alias for `"query"`. An unknown or expired `session_id`
starts a new session.

```
200 OK
{
  "session_id": "3f0c…",
  "status":     "ok" | "not_available" | "not_in_scope" | "not_allowed" | "error",
  "results":    "Iron Maiden leads with $138.60 in sales, followed by U2 ($105.93)…",
  "evidence":   { "row_count": 3, "truncated": false,
                  "rows": [{"Artist": "Iron Maiden", "Revenue": 138.6}, …] },
  "request_id": "6f5daf546c78"
}
```

| status          | When                                                        | results                 | evidence |
|-----------------|-------------------------------------------------------------|-------------------------|----------|
| `ok`            | Query ran and returned rows                                 | Natural-language answer | rows     |
| `not_available` | Agent reports not available, 0 rows, or retries exhausted   | `INFO not available`    | `null`   |
| `not_in_scope`  | Decision model: unrelated to the store's data               | `Not in scope: …`       | `null`   |
| `not_allowed`   | Decision model: asks to modify data                         | `Not allowed: …`        | `null`   |
| `error`         | Upstream failure (LLM API error, DB down)                   | Generic message         | `null`   |

## 4. Repo layout

```
synthio/
├── docker-compose.yml
├── .env                       # OPENAI_API_KEY, DB passwords (not committed)
├── .env.example
├── docs/ARCHITECTURE.md       # this file
├── synthio-concept-diagram.png
├── chinook-database/          # upstream repo; source of the seed SQL
├── db/init/02_readonly_role.sh
├── api/
│   ├── app/main.py            # FastAPI app, POST /api/chat
│   ├── app/pipeline.py        # decision model → SQL agent → answer writer
│   ├── app/sql_guard.py       # deterministic read-only check
│   ├── app/db.py              # read-only connection pool + executor
│   ├── app/schema.py          # catalog from information_schema, describe_tables
│   ├── app/sessions.py        # in-memory sessions
│   └── tests/test_sql_guard.py
└── ui/                        # Next.js chat page + /api/chat proxy route
```

## Running

```bash
cp .env.example .env               # set OPENAI_API_KEY and both passwords
docker compose up -d --build
open http://localhost:3000
docker compose run --rm api python -m pytest -q tests   # guard tests
```

The seed runs only on the first start (it lives in the `pgdata` volume). To reseed, run
`docker compose down -v`.
