"""In-memory chat sessions. Lost on restart, not shared across api replicas."""
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from .config import settings


@dataclass
class Turn:
    question: str
    answer: str


@dataclass
class Session:
    id: str
    turns: list[Turn] = field(default_factory=list)
    last_used: float = field(default_factory=time.monotonic)


class SessionStore:
    def __init__(self):
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None) -> Session:
        now = time.monotonic()
        with self._lock:
            self._evict(now)
            s = self._sessions.get(session_id) if session_id else None
            if s is None:
                s = Session(id=str(uuid.uuid4()))
                self._sessions[s.id] = s
            s.last_used = now
            self._sessions.move_to_end(s.id)
            return s

    def append(self, session: Session, question: str, answer: str) -> None:
        with self._lock:
            session.turns.append(Turn(question, answer))
            del session.turns[: -settings.session_max_turns]

    def _evict(self, now: float) -> None:
        expired = [k for k, s in self._sessions.items() if now - s.last_used > settings.session_ttl_s]
        for k in expired:
            del self._sessions[k]
        while len(self._sessions) >= settings.max_sessions:
            self._sessions.popitem(last=False)


store = SessionStore()
