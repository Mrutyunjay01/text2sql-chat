import datetime as dt
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import psycopg
from psycopg_pool import ConnectionPool

from .config import settings

log = logging.getLogger(__name__)


def _configure(conn: psycopg.Connection) -> None:
    # Layer 2: every transaction on these connections is READ ONLY.
    conn.read_only = True


pool = ConnectionPool(
    conninfo=psycopg.conninfo.make_conninfo(
        host=settings.db_host, port=settings.db_port, dbname=settings.db_name,
        user=settings.db_user, password=settings.db_password,
        application_name="synthio-api",
    ),
    min_size=1,
    max_size=5,
    configure=_configure,
    open=False,
)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict]
    truncated: bool


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return "<binary>"
    return v


def run_select(sql: str) -> QueryResult:
    """Run an already-guarded SELECT. Raises psycopg.Error on failure."""
    with pool.connection() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('statement_timeout', %s, true)",
                            (str(settings.statement_timeout_ms),))
                cur.execute(sql)
                if cur.description is None:
                    raise psycopg.ProgrammingError("statement returned no result set")
                columns = [d.name for d in cur.description]
                fetched = cur.fetchmany(settings.max_rows + 1)
        finally:
            conn.rollback()
    truncated = len(fetched) > settings.max_rows
    rows = [
        {col: _jsonable(val) for col, val in zip(columns, row)}
        for row in fetched[: settings.max_rows]
    ]
    return QueryResult(columns, rows, truncated)


def open_pool(retries: int = 30, delay_s: float = 2.0) -> None:
    pool.open()
    for attempt in range(1, retries + 1):
        try:
            with pool.connection(timeout=5) as conn:
                conn.execute("SELECT 1")
                conn.rollback()
            return
        except Exception as e:  # DB may still be starting
            log.warning("database not ready (attempt %d/%d): %s", attempt, retries, e)
            time.sleep(delay_s)
    raise RuntimeError("database unavailable")
