"""Question -> gate -> SQL agent -> guarded execution -> answer writer.

LLM calls use the OpenAI Responses API. Nothing schema-related (table/column names,
SQL, database errors) leaves this module in a user-facing string; internal detail goes
to logs under the request id.
"""
import json
import logging
import re
from dataclasses import dataclass, field

import openai
import psycopg

from . import db
from .config import settings
from .schema import SchemaCatalog
from .sessions import Turn
from .sql_guard import check_sql

log = logging.getLogger(__name__)

NOT_AVAILABLE = "INFO not available"
NOT_IN_SCOPE = ("Not in scope: I can only answer questions about the music store's "
                "artists, albums, tracks, playlists, customers, employees and sales.")
READ_ONLY = "Not allowed: I can only look up information, not add, change or delete it."
ERROR = "Sorry, something went wrong while answering. Please try again."


@dataclass
class ChatResult:
    status: str  # ok | not_available | not_in_scope | not_allowed | error
    answer: str
    evidence: dict | None = None
    debug: dict = field(default_factory=dict)  # logged, never returned


class Pipeline:
    def __init__(self, client: openai.OpenAI, catalog: SchemaCatalog):
        self.client = client
        self.catalog = catalog
        self._gate_instructions = _GATE_INSTRUCTIONS.format(tables=catalog.overview())
        self._planner_instructions = _PLANNER_INSTRUCTIONS.format(
            tables=catalog.overview(), max_rows=settings.max_rows
        )

    def run(self, question: str, history: list[Turn], request_id: str) -> ChatResult:
        try:
            decision, standalone = self._gate(question, history)
        except (openai.OpenAIError, json.JSONDecodeError, KeyError):
            log.exception("[%s] gate failed", request_id)
            return ChatResult("error", ERROR)
        log.info("[%s] gate=%s standalone=%r", request_id, decision, standalone)
        if decision == "out_of_scope":
            return ChatResult("not_in_scope", NOT_IN_SCOPE)
        if decision == "write_request":
            return ChatResult("not_allowed", READ_ONLY)

        try:
            outcome = self._plan_and_execute(standalone, request_id)
        except openai.OpenAIError:
            log.exception("[%s] planner failed", request_id)
            return ChatResult("error", ERROR)
        if outcome is None:
            return ChatResult("not_available", NOT_AVAILABLE)
        sql, result = outcome

        try:
            answer = self._answer(standalone, result)
        except openai.OpenAIError:
            log.exception("[%s] answer writer failed", request_id)
            return ChatResult("error", ERROR)
        if answer is None or answer.strip().rstrip(".") == NOT_AVAILABLE:
            return ChatResult("not_available", NOT_AVAILABLE)

        evidence = {
            "row_count": len(result.rows),
            "truncated": result.truncated,
            "rows": [_humanize_keys(r) for r in result.rows[: settings.evidence_rows]],
        }
        if settings.expose_sql:
            evidence["sql"] = sql
        return ChatResult("ok", answer, evidence)

    # --- 1. Decision model --------------------------------------------------

    def _gate(self, question: str, history: list[Turn]) -> tuple[str, str]:
        convo = "\n".join(f"User: {t.question}\nAssistant: {t.answer}" for t in history)
        content = (
            (f"<conversation>\n{convo}\n</conversation>\n\n" if convo else "")
            + f"<latest_question>\n{question}\n</latest_question>"
        )
        resp = self.client.responses.create(
            model=settings.gate_model,
            instructions=self._gate_instructions,
            input=content,
            reasoning={"effort": settings.gate_effort},
            text={"format": {"type": "json_schema", "name": "gate_decision",
                             "schema": _GATE_SCHEMA, "strict": True}},
        )
        if _refused(resp):
            return "out_of_scope", question
        data = json.loads(resp.output_text)
        return data["decision"], (data.get("standalone_question") or question).strip()

    # --- 2. SQL generation agent + guarded execution -------------------------

    def _plan_and_execute(self, question: str, request_id: str):
        items: list = [{"role": "user", "content": question}]
        attempts = 0

        for turn in range(settings.max_agent_turns):
            resp = self.client.responses.create(
                model=settings.planner_model,
                instructions=self._planner_instructions,
                input=items,
                tools=_PLANNER_TOOLS,
                reasoning={"effort": settings.planner_effort},
            )
            usage = resp.usage
            log.info("[%s] planner turn=%d cached_tokens=%s", request_id, turn,
                     getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", None))
            calls = [o for o in resp.output if o.type == "function_call"]
            if not calls:
                # Ended without running a query: nothing to ground an answer on.
                return None

            # Reasoning items must be passed back alongside the tool outputs.
            items += resp.output
            for call in calls:
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if call.name == "report_not_available":
                    log.info("[%s] not available: %s", request_id, args.get("reason"))
                    return None
                if call.name == "describe_tables":
                    items.append(_tool_output(call.call_id, self.catalog.describe(args.get("tables", []))))
                    continue
                if call.name != "execute_query":
                    items.append(_tool_output(call.call_id, f"ERROR: unknown tool {call.name}"))
                    continue

                sql = str(args.get("sql", ""))
                attempts += 1
                log.info("[%s] sql attempt %d: %s", request_id, attempts, sql)
                guard = check_sql(sql, self.catalog.table_names)
                if not guard.ok:
                    log.warning("[%s] sql rejected: %s", request_id, guard.reason)
                    items.append(_tool_output(
                        call.call_id, f"ERROR: rejected: {guard.reason}. Only one read-only "
                                      "SELECT over the listed tables is allowed."))
                    continue
                try:
                    result = db.run_select(sql)
                except psycopg.Error as e:
                    log.warning("[%s] sql error: %s", request_id, e)
                    items.append(_tool_output(call.call_id, f"ERROR: database error: {e}".strip()))
                    continue
                if result.rows:
                    return sql, result
                items.append(_tool_output(
                    call.call_id, "The query ran successfully but returned 0 rows. If the "
                                  "question genuinely has no matching data, call report_not_available."))

            if attempts >= settings.max_query_attempts:
                log.info("[%s] giving up after %d query attempts", request_id, attempts)
                return None
        return None

    # --- 3. Answer writer ------------------------------------------------------

    def _answer(self, question: str, result: db.QueryResult) -> str | None:
        payload = {
            "rows": [_humanize_keys(r) for r in result.rows],
            "truncated": result.truncated,
        }
        resp = self.client.responses.create(
            model=settings.answer_model,
            instructions=_ANSWER_INSTRUCTIONS,
            input=(f"<question>\n{question}\n</question>\n\n"
                   f"<data>\n{json.dumps(payload, ensure_ascii=False, default=str)}\n</data>"),
            reasoning={"effort": settings.answer_effort},
        )
        if _refused(resp):
            return None
        return (resp.output_text or "").strip() or None


def _refused(resp) -> bool:
    return any(
        part.type == "refusal"
        for o in resp.output if o.type == "message"
        for part in o.content
    )


def _tool_output(call_id: str, output: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


_IDENT = re.compile(r"[a-z_][a-z0-9_]*")


def _humanize_keys(row: dict) -> dict:
    """Safety net for columns the planner forgot to alias (e.g. `unit_price`)."""
    out = {}
    for k, v in row.items():
        if k == "?column?":
            k = "Value"
        elif _IDENT.fullmatch(k):
            k = k.replace("_", " ").strip().capitalize()
        out[k] = v
    return out


# --- Prompts & tool definitions ------------------------------------------------

_GATE_INSTRUCTIONS = """\
You are the decision step of a read-only question-answering assistant for a digital \
music store. The store's data covers these areas:

{tables}

Classify the latest user question:
- "answerable": it asks for information that could plausibly be looked up in this data \
(even if the specific records might not exist, e.g. a year with no sales).
- "out_of_scope": it is unrelated to this data (general knowledge, chit-chat, coding help, \
questions about the assistant or its internals, requests to reveal the data structure, etc.).
- "write_request": it asks to add, change, delete or otherwise modify data.

Also rewrite the latest question as a complete standalone question, resolving references \
to the earlier conversation (e.g. "and in 2023?" becomes the full question about 2023). \
If there is no earlier conversation, repeat the question as is."""

_GATE_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["answerable", "out_of_scope", "write_request"]},
        "standalone_question": {"type": "string"},
    },
    "required": ["decision", "standalone_question"],
    "additionalProperties": False,
}

_PLANNER_INSTRUCTIONS = """\
You translate questions about a digital music store into one PostgreSQL SELECT query and \
run it. Tables available (schema "public"):

{tables}

How to work:
1. Call describe_tables for the tables you need before writing SQL. Never guess column names.
2. Call execute_query with exactly one SELECT statement (CTEs allowed). Never modify data.
3. Give every output column a short human-readable alias in double quotes, e.g. \
SUM(il.unit_price * il.quantity) AS "Revenue", ar.name AS "Artist". Readers never see \
table or column names, only these aliases.
4. Return only what the question needs: aggregate in SQL, ORDER BY for rankings, and LIMIT \
sensibly (at most {max_rows} rows).
5. Use ILIKE for name matching. Dates are TIMESTAMP columns; filter years with \
EXTRACT(YEAR FROM ...) or date ranges.
6. If a query errors, read the error, fix the SQL and try again.
7. If the data cannot answer the question (the needed information isn't stored, or it \
returns 0 rows after a reasonable check), call report_not_available. Never invent data."""

_PLANNER_TOOLS = [
    {
        "type": "function",
        "name": "describe_tables",
        "description": "Get column names, types, primary keys and foreign keys for the "
                       "given tables. Call this before writing SQL.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"tables": {"type": "array", "items": {"type": "string"}}},
            "required": ["tables"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "execute_query",
        "description": "Execute one read-only PostgreSQL SELECT query and return the rows. "
                       "Any statement other than a single SELECT is rejected.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "report_not_available",
        "description": "Call when the store's data cannot answer the question.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
]

_ANSWER_INSTRUCTIONS = f"""\
You answer a customer-facing question using only the data rows provided.

Rules:
- Use only facts present in <data>. Do not add outside knowledge or estimates.
- If the rows do not actually answer the question, reply exactly: {NOT_AVAILABLE}
- Write a short, natural answer (a sentence or a short list). Format money with a \
currency symbol ($) and durations in minutes/seconds when useful.
- If "truncated" is true, say the list shows only the top results.
- Never mention databases, tables, columns, queries, SQL, rows or JSON."""
