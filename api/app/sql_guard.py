"""Deterministic read-only check for generated SQL. Layer 1 of 3: the transaction is
READ ONLY and the DB role can only SELECT, so this never has to be perfect, but it
rejects anything that isn't one plain SELECT over the known tables."""
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
    "TruncateTable", "Command", "Copy", "Grant", "Revoke", "Set", "Transaction",
    "Commit", "Rollback", "Into", "Lock", "LoadData", "Use", "Pragma", "Analyze",
)
_FORBIDDEN_NODES = tuple(getattr(exp, n) for n in _FORBIDDEN_NODE_NAMES if hasattr(exp, n))
_SET_OPS = tuple(getattr(exp, n) for n in ("Union", "Intersect", "Except") if hasattr(exp, n))

_FORBIDDEN_FUNC_PREFIXES = ("pg_", "lo_", "dblink", "file_", "txid_", "inet_")
_FORBIDDEN_FUNCS = {
    "set_config", "current_setting", "nextval", "setval", "currval", "lastval",
    "query_to_xml", "table_to_xml", "cursor_to_xml", "database_to_xml", "schema_to_xml",
    "version", "has_table_privilege", "has_database_privilege",
}


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""


def _func_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def check_sql(sql: str, allowed_tables: set[str]) -> GuardResult:
    if not sql or not sql.strip():
        return GuardResult(False, "empty query")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as e:
        return GuardResult(False, f"could not parse SQL: {str(e).splitlines()[0]}")
    if len(statements) != 1:
        return GuardResult(False, "exactly one statement is allowed")

    root = statements[0]
    if not isinstance(root, (exp.Select, *_SET_OPS)):
        return GuardResult(False, "only SELECT queries are allowed")

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return GuardResult(False, f"{type(node).__name__.upper()} is not allowed")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            return GuardResult(False, "row locking (FOR UPDATE/SHARE) is not allowed")
        if isinstance(node, exp.Func):
            name = _func_name(node)
            if name in _FORBIDDEN_FUNCS or name.startswith(_FORBIDDEN_FUNC_PREFIXES):
                return GuardResult(False, f"function {name}() is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        name = (table.name or "").lower()
        schema = (table.db or "").lower()
        if not name:
            # Table-valued function in FROM, e.g. generate_series(...): allowed
            # only if it isn't a denied function (checked above).
            continue
        if schema and schema != "public":
            return GuardResult(False, f"schema {schema} is not accessible")
        if table.catalog:
            return GuardResult(False, "cross-database references are not allowed")
        if name not in allowed_tables and name not in cte_names:
            return GuardResult(False, f"unknown table {name}")

    return GuardResult(True)
