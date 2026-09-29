import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_host: str = os.getenv("DB_HOST", "localhost")
    db_port: int = int(os.getenv("DB_PORT", "5432"))
    db_name: str = os.getenv("DB_NAME", "chinook")
    db_user: str = os.getenv("CHINOOK_RO_USER", "chinook_ro")
    db_password: str = os.getenv("CHINOOK_RO_PASSWORD", "")

    # OpenAI models. The decision model (gate) and answer writer use the efficient
    # model; SQL generation uses the stronger one.
    gate_model: str = os.getenv("GATE_MODEL", "gpt-6-luna")
    planner_model: str = os.getenv("PLANNER_MODEL", "gpt-6.1-sol")
    answer_model: str = os.getenv("ANSWER_MODEL", "gpt-6-luna")
    gate_effort: str = os.getenv("GATE_EFFORT", "low")
    planner_effort: str = os.getenv("PLANNER_EFFORT", "medium")
    answer_effort: str = os.getenv("ANSWER_EFFORT", "low")

    max_rows: int = int(os.getenv("MAX_ROWS", "200"))
    evidence_rows: int = int(os.getenv("EVIDENCE_ROWS", "50"))
    statement_timeout_ms: int = int(os.getenv("STATEMENT_TIMEOUT_MS", "5000"))
    max_agent_turns: int = int(os.getenv("MAX_AGENT_TURNS", "8"))
    max_query_attempts: int = int(os.getenv("MAX_QUERY_ATTEMPTS", "3"))

    # The diagram lists "query ran as evidence" as optional. Off by default,
    # since SQL reveals table and column names.
    expose_sql: bool = _bool("EXPOSE_SQL", False)

    session_ttl_s: int = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
    session_max_turns: int = int(os.getenv("SESSION_MAX_TURNS", "10"))
    max_sessions: int = int(os.getenv("MAX_SESSIONS", "1000"))


settings = Settings()
