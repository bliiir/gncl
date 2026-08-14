"""FastAPI surface.

The pipeline runs once at startup and the result is held in memory. `threshold`
filters stored scores; it never re-runs matching. That is the *shape* of the
scaling answer, not an implementation of it -- this serialises the whole frame
per request and filters in Python, with no pagination. Correct at 35 rows; at
scale the filter belongs in the query and the endpoints need limit/offset.

Everything except /health sits behind basic auth. An unauthenticated endpoint on
a public IP that reaches a model is an open proxy and a cost risk.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, field_validator

from gncl import config
from gncl.join import review_band
from gncl.match import build
from gncl.output import build_guests, to_dataframe
from gncl.ports import CsvSource

# uvicorn imports this module directly, bypassing the CLI, so the check runs
# here too. Compose injects the variables rather than mounting a file, which
# `config.require` accepts.
config.require()

AUTH_USER = config.auth_user()
AUTH_PASSWORD = config.auth_password()

WINDOW_SECONDS = 60
MAX_TRACKED_CLIENTS = 10_000
IDLE_EVICT_SECONDS = 300

# Read at import so `importlib.reload` picks up a changed environment, and
# strict so a typo in a limit is not served as the default limit.
RATE_LIMIT_PER_MIN = config.rate_limit_per_min()
AUTH_FAIL_PER_MIN = config.auth_fail_per_min()
DEFAULT_THRESHOLD = config.threshold()

SOURCES = frozenset({"bookit", "hubspot", "ls_retail"})

security = HTTPBasic(auto_error=True)
_hits: dict[str, list[float]] = defaultdict(list)
_hits_lock = threading.Lock()


class State:
    """Pipeline output, computed once."""

    def __init__(self, source=None) -> None:
        # Composition root for the service. Swap the source here, not below.
        self.frames = (source or CsvSource()).frames()
        self.result = build(self.frames)
        verdicts = None
        if self.result.name_conflicts:
            from gncl.llm import OllamaJudge, audit_conflicts, available

            if available():
                verdicts = audit_conflicts(self.result.name_conflicts, judge=OllamaJudge())
        self.guests = build_guests(self.result, self.frames, verdicts)
        self.df = to_dataframe(self.guests)

    def as_records(self) -> list[dict[str, Any]]:
        return self.df.to_dict(orient="records")


state: State | None = None
_state_lock = threading.Lock()


def get_state() -> State:
    global state  # noqa: PLW0603 - module-level singleton, built once under a lock
    if state is None:
        with _state_lock:
            if state is None:  # re-check: sync endpoints run in a threadpool
                state = State()
    return state


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Build eagerly, so HEALTHCHECK cannot pass before the app can serve."""
    get_state()
    yield


# Schema routes off: they enumerate every endpoint and are not worth exposing
# on a public box behind a single shared credential.
app = FastAPI(
    title="GNCL guest identity resolution",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def check_auth(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(security)],
) -> str:
    """Constant-time comparison so the credential cannot be guessed by timing."""
    if not AUTH_USER or not AUTH_PASSWORD:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "GNCL_AUTH_USER and GNCL_AUTH_PASSWORD are unset; refusing to serve unauthenticated",
        )
    ok_user = secrets.compare_digest(credentials.username, AUTH_USER)
    ok_pass = secrets.compare_digest(credentials.password, AUTH_PASSWORD)
    if not (ok_user and ok_pass):
        # A wrong password burns a far smaller budget than a valid request, so a
        # single shared credential cannot be brute-forced at request speed.
        _bump(f"auth-fail:{_client_key(request)}", AUTH_FAIL_PER_MIN)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def _bump(key: str, limit: int) -> None:
    """Sliding window, mutated under a lock.

    Sync endpoints run in a threadpool, so read-filter-append races: two
    requests can both read a window below the limit and both append. The
    eviction sweep is the worse half, deleting from `_hits` while another thread
    may be reading it.
    """
    now = time.monotonic()
    with _hits_lock:
        recent = [t for t in _hits[key] if now - t < WINDOW_SECONDS]
        if len(recent) >= limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
        recent.append(now)
        _hits[key] = recent
        if len(_hits) > MAX_TRACKED_CLIENTS:  # idle clients are never revisited
            for k in [k for k, v in _hits.items() if not v or now - max(v) > IDLE_EVICT_SECONDS]:
                del _hits[k]


def _client_key(request: Request) -> str:
    """Sliding window key.

    Behind a proxy this is the proxy's address unless uvicorn is started with
    --forwarded-allow-ips, so the limit is effectively global. Trusting
    X-Forwarded-For instead would make the key attacker-controlled, which is
    worse. See deploy/README.md.
    """
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    """Sliding window per client key. In-memory: one process only."""
    _bump(_client_key(request), RATE_LIMIT_PER_MIN)


# Order matters. FastAPI resolves dependencies in sequence and an HTTPException
# aborts the rest, so auth-first means a failed login never reaches the limiter
# and basic-auth brute force is unbounded. Rate limit first.
Guarded = [Depends(rate_limit), Depends(check_auth)]


@app.get("/health", dependencies=[Depends(rate_limit)])
def health() -> dict[str, Any]:
    """Unauthenticated on purpose, so a load balancer can probe it."""
    return {"status": "ok", "version": app.version}


def _band(record: dict[str, Any], threshold: float) -> str:
    """The band for a served record, from the one definition of it.

    Both endpoints compared `match_confidence` against the threshold directly,
    which is the same rule twice -- and it stopped being the same rule the
    moment a single-source guest became accepted at 0.0. `/review-queue` would
    have kept returning the 5 records the CSV calls accepted.
    """
    return review_band(float(record["match_confidence"]), threshold, str(record["match_method"]))


@app.get("/guests", dependencies=Guarded)
def list_guests(
    st: Annotated[State, Depends(get_state)],
    threshold: Annotated[float, Query(ge=0.0, le=1.0)] = DEFAULT_THRESHOLD,
    method: str | None = None,
) -> dict[str, Any]:
    accepted = [r for r in st.as_records() if _band(r, threshold) == "accepted"]
    rows = [r for r in accepted if r["match_method"] == method] if method else accepted
    return {
        "threshold": threshold,
        "total": len(st.df),
        "returned": len(rows),
        "below_threshold": len(st.df) - len(accepted),
        "guests": rows,
    }


@app.get("/guests/{guest_id}", dependencies=Guarded)
def get_guest(guest_id: str, st: Annotated[State, Depends(get_state)]) -> dict[str, Any]:
    rows = [r for r in st.as_records() if r["guest_id"] == guest_id]
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no guest {guest_id}")
    row = dict(rows[0])
    row["evidence_chain"] = [e for e in str(row.get("evidence", "")).split(" | ") if e]
    return row


@app.get("/review-queue", dependencies=Guarded)
def review_queue(
    st: Annotated[State, Depends(get_state)],
    threshold: Annotated[float, Query(ge=0.0, le=1.0)] = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    rows = [r for r in st.as_records() if _band(r, threshold) == "review"]
    return {"threshold": threshold, "returned": len(rows), "guests": rows}


@app.get("/raw/{source}", dependencies=Guarded)
def raw(source: str, st: Annotated[State, Depends(get_state)]) -> dict[str, Any]:
    if source not in SOURCES:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"unknown source {source}; expected one of {sorted(SOURCES)}"
        )
    df = st.frames[source]
    public = [c for c in df.columns if c not in {"name_keys"}]
    return {
        "source": source,
        "rows": len(df),
        "records": df[public].astype(str).to_dict(orient="records"),
    }


@app.get("/stats", dependencies=Guarded)
def stats(st: Annotated[State, Depends(get_state)]) -> dict[str, Any]:
    df = st.df
    return {
        "guests": len(df),
        "source_records": {k: len(v) for k, v in st.frames.items()},
        "by_method": df["match_method"].value_counts().to_dict(),
        "name_conflicts": len(st.result.name_conflicts),
        "distinct_conflict_questions": len(
            {(c["captured"], c["booking_name"]) for c in st.result.name_conflicts}
        ),
        "unattached_transactions": len(st.result.unresolved_transactions),
        "total_spend": round(float(df["total_spend"].sum()), 2),
    }


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=8000)


class ChatQuestion(BaseModel):
    """A question, plus the transcript the browser is holding for it.

    History comes from the client because the server holds no session: nothing
    to expire, and a second uvicorn worker answers identically. The cost is that
    a caller can post an invented `assistant` turn -- it is their own
    conversation to poison, behind auth, and the grounding is still assembled
    server-side, so no fabricated turn can change what the model is shown as
    evidence. Capped so a long conversation cannot grow the prompt without
    bound.
    """

    question: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)

    @field_validator("history")
    @classmethod
    def _alternates_from_the_user(cls, turns: list[ChatTurn]) -> list[ChatTurn]:
        """Complete exchanges only: user, assistant, user, assistant.

        The model API requires alternation, and a malformed history would
        otherwise surface as "the hosted model could not be reached", blaming
        the network for a client bug.
        """
        if len(turns) % 2:
            raise ValueError("history must end with an assistant turn")
        for i, turn in enumerate(turns):
            if turn.role != ("user" if i % 2 == 0 else "assistant"):
                raise ValueError("history must alternate user, assistant, starting with user")
        return turns


@app.post("/chat", dependencies=Guarded)
def chat(
    body: ChatQuestion,
    st: Annotated[State, Depends(get_state)],
) -> dict[str, Any]:
    """Answer a question about the output, grounded in the CSVs and the docs.

    The key never leaves the server. The browser posts a question here and this
    calls the hosted model, so no credential is ever rendered into the page.

    `usable` false means no answer was produced and `answer` explains why. That
    is deliberately not an HTTP error: "no model configured" is a state of the
    system a reviewer should see described, not a 500.
    """
    from gncl import chat as chat_mod

    answerer = chat_mod.build()
    grounding = chat_mod.context(st.df, DEFAULT_THRESHOLD)
    history = [(turn.role, turn.text) for turn in body.history]
    result = answerer.answer(body.question, grounding, history)
    return {
        "answer": result.text,
        "model": result.model,
        "usable": result.usable,
        "grounded_on": list(chat_mod.GROUNDING),
    }


@app.get("/", response_class=HTMLResponse, dependencies=Guarded)
def page() -> str:
    """The browser view, served rather than written to a file.

    `gncl ui` still writes a standalone `out/index.html`; that copy has no server
    to ask, so its chat tab says so. This route is the interactive one.
    """
    from gncl import chat as chat_mod
    from gncl.ui import render

    return render(Path("out"), chat=chat_mod.available())
