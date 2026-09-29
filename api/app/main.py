import logging
import uuid
from contextlib import asynccontextmanager

import openai
from fastapi import FastAPI
from pydantic import AliasChoices, BaseModel, Field

from . import db
from .pipeline import Pipeline
from .schema import load_catalog
from .sessions import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("synthio")

state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.open_pool()
    with db.pool.connection() as conn:
        catalog = load_catalog(conn)
    log.info("schema loaded: %d tables", len(catalog.tables))
    state["pipeline"] = Pipeline(openai.OpenAI(), catalog)
    yield
    db.pool.close()


app = FastAPI(title="Synthio", lifespan=lifespan)


class ChatRequest(BaseModel):
    # The diagram uses "query"; the original spec used "request". Accept both.
    query: str = Field(min_length=1, max_length=1000,
                       validation_alias=AliasChoices("query", "request"))
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    status: str
    results: str  # natural-language answer
    evidence: dict | None = None
    request_id: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Sync handler: FastAPI runs it in a worker thread, so blocking LLM/DB calls are fine.
@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    request_id = uuid.uuid4().hex[:12]
    session = store.get_or_create(req.session_id)
    question = req.query.strip()
    log.info("[%s] session=%s question=%r", request_id, session.id, question)

    result = state["pipeline"].run(question, list(session.turns), request_id)
    log.info("[%s] status=%s", request_id, result.status)

    store.append(session, question, result.answer)
    return ChatResponse(
        session_id=session.id,
        status=result.status,
        results=result.answer,
        evidence=result.evidence,
        request_id=request_id,
    )
