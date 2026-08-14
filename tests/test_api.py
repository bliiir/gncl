"""API tests, including the security properties that matter on a public box."""

import pytest
from fastapi.testclient import TestClient

USER, PASSWORD = "reviewer", "s3cret"
AUTH = (USER, PASSWORD)


@pytest.fixture
def client(monkeypatch):
    from gncl import api

    monkeypatch.setattr(api, "AUTH_USER", USER)
    monkeypatch.setattr(api, "AUTH_PASSWORD", PASSWORD)
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MIN", 10_000)
    api._hits.clear()
    return TestClient(api.app)


def test_health_needs_no_auth(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_every_other_endpoint_requires_auth(client):
    for path in ("/guests", "/guests/G-BK1001", "/raw/bookit", "/stats", "/review-queue"):
        assert client.get(path).status_code == 401, path


def test_wrong_password_rejected(client):
    assert client.get("/guests", auth=(USER, "wrong")).status_code == 401


def test_refuses_to_serve_when_no_credentials_configured(monkeypatch):
    """An unset credential must fail closed, not serve the world."""
    from gncl import api

    monkeypatch.setattr(api, "AUTH_USER", "")
    monkeypatch.setattr(api, "AUTH_PASSWORD", "")
    r = TestClient(api.app).get("/guests", auth=("a", "b"))
    assert r.status_code == 503
    assert "refusing to serve unauthenticated" in r.json()["detail"]


def test_rate_limit_returns_429(monkeypatch):
    from gncl import api

    monkeypatch.setattr(api, "AUTH_USER", USER)
    monkeypatch.setattr(api, "AUTH_PASSWORD", PASSWORD)
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MIN", 3)
    api._hits.clear()
    c = TestClient(api.app)
    codes = [c.get("/stats", auth=AUTH).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:]


def test_guests_returns_all_35_at_zero_threshold(client):
    body = client.get("/guests?threshold=0", auth=AUTH).json()
    assert body["total"] == 35
    assert body["returned"] == 35


def test_threshold_filters_without_rerunning(client):
    """Same stored scores, different slices."""
    lo = client.get("/guests?threshold=0.0", auth=AUTH).json()
    hi = client.get("/guests?threshold=0.8", auth=AUTH).json()
    assert hi["returned"] == 34
    assert lo["total"] == hi["total"] == 35
    by_id = {g["guest_id"]: g["match_confidence"] for g in lo["guests"]}
    for g in hi["guests"]:
        assert by_id[g["guest_id"]] == g["match_confidence"]


def test_review_queue_is_the_complement(client):
    accepted = client.get("/guests?threshold=0.8", auth=AUTH).json()["returned"]
    queued = client.get("/review-queue?threshold=0.8", auth=AUTH).json()["returned"]
    assert accepted + queued == 35
    assert queued == 1, "the queue holds contested joins, not lone records"


def test_review_queue_holds_no_single_source_guests(client):
    """The bug this guards: the endpoint compared confidence to the threshold
    itself, so it kept returning the 5 records the CSV calls accepted."""
    body = client.get("/review-queue?threshold=0.8", auth=AUTH).json()
    assert [g["guest_id"] for g in body["guests"]] == ["G-BK1021"]
    assert all(g["match_method"] != "single source" for g in body["guests"])


def test_threshold_is_validated(client):
    assert client.get("/guests?threshold=1.5", auth=AUTH).status_code == 422
    assert client.get("/guests?threshold=-1", auth=AUTH).status_code == 422


def test_guest_detail_exposes_the_evidence_chain(client):
    body = client.get("/guests/G-BK1021", auth=AUTH).json()
    assert body["contact_id"] == "HS221"
    assert body["match_confidence"] == 0.55
    assert any("not unique" in e for e in body["evidence_chain"])


def test_unknown_guest_is_404(client):
    assert client.get("/guests/G-NOPE", auth=AUTH).status_code == 404


def test_filter_by_method(client):
    body = client.get("/guests?threshold=0&method=single+source", auth=AUTH).json()
    assert body["returned"] == 5
    assert {g["match_method"] for g in body["guests"]} == {"single source"}


@pytest.mark.parametrize("source,rows", [("bookit", 32), ("hubspot", 31), ("ls_retail", 57)])
def test_raw_sources(client, source, rows):
    body = client.get(f"/raw/{source}", auth=AUTH).json()
    assert body["rows"] == rows
    assert len(body["records"]) == rows


def test_unknown_source_is_404(client):
    assert client.get("/raw/nope", auth=AUTH).status_code == 404


def test_stats(client):
    body = client.get("/stats", auth=AUTH).json()
    assert body["guests"] == 35
    assert body["by_method"] == {"deterministic": 20, "rule_fuzzy": 10, "single source": 5}
    assert body["name_conflicts"] == 4
    assert body["distinct_conflict_questions"] == 2
    assert body["unattached_transactions"] == 0


def test_failed_logins_are_rate_limited(monkeypatch):
    """The bug this guards: auth-first dependency order meant a 401 never
    reached the limiter, so a single shared credential could be brute-forced at
    request speed. Rate limit now runs first, on a tighter budget for failures.
    """
    from gncl import api

    monkeypatch.setattr(api, "AUTH_USER", USER)
    monkeypatch.setattr(api, "AUTH_PASSWORD", PASSWORD)
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MIN", 10_000)
    monkeypatch.setattr(api, "AUTH_FAIL_PER_MIN", 3)
    api._hits.clear()
    c = TestClient(api.app)
    codes = [c.get("/stats", auth=(USER, "wrong")).status_code for _ in range(6)]
    assert codes[:3] == [401, 401, 401]
    assert 429 in codes[3:], f"brute force not throttled: {codes}"


def test_wrong_password_burns_the_auth_budget_not_the_request_budget(monkeypatch):
    from gncl import api

    monkeypatch.setattr(api, "AUTH_USER", USER)
    monkeypatch.setattr(api, "AUTH_PASSWORD", PASSWORD)
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MIN", 10_000)
    monkeypatch.setattr(api, "AUTH_FAIL_PER_MIN", 2)
    api._hits.clear()
    c = TestClient(api.app)
    for _ in range(2):
        c.get("/stats", auth=(USER, "wrong"))
    assert c.get("/stats", auth=(USER, "wrong")).status_code == 429
    assert c.get("/stats", auth=AUTH).status_code == 200, "valid users must not be locked out"


def test_schema_routes_are_not_exposed(client):
    """They enumerate every endpoint; not worth publishing on a public box."""
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_api_honours_the_threshold_env_default(monkeypatch):
    import importlib

    monkeypatch.setenv("GNCL_THRESHOLD", "0.95")
    monkeypatch.setenv("GNCL_AUTH_USER", USER)
    monkeypatch.setenv("GNCL_AUTH_PASSWORD", PASSWORD)
    from gncl import api

    reloaded = importlib.reload(api)
    try:
        assert reloaded.DEFAULT_THRESHOLD == 0.95
        body = TestClient(reloaded.app).get("/guests", auth=AUTH).json()
        assert body["threshold"] == 0.95
        # The 0.95 tier, plus the 5 lone records, which no threshold flags.
        assert body["returned"] == 25
    finally:
        monkeypatch.undo()
        importlib.reload(api)


def test_malformed_rate_limit_env_is_refused_not_defaulted(monkeypatch):
    """A limit that cannot be parsed must not be served as the default limit.

    This used to fall back to 60 silently, so an instance configured for 10
    accepted 60 and nothing said so. Refusing to start is the safe direction
    for a value whose whole purpose is to cap abuse.
    """
    import importlib

    import pytest

    from gncl.config import ConfigError

    monkeypatch.setenv("GNCL_RATE_LIMIT_PER_MIN", "not-a-number")
    from gncl import api

    try:
        with pytest.raises(ConfigError, match="GNCL_RATE_LIMIT_PER_MIN"):
            importlib.reload(api)
    finally:
        monkeypatch.undo()
        importlib.reload(api)


def test_rate_limit_holds_under_concurrent_requests():
    """Sync endpoints run in a threadpool, so read-filter-append must be atomic.

    Unlocked, two threads both read a window below the limit and both append,
    admitting over it. The auth-fail budget is deliberately small, so a few extra
    admissions there is a real widening of the brute-force window.

    `setswitchinterval` is what makes this reproducible. At the default interval
    the critical section is too short to be preempted and the unlocked version
    passes every run; at 1e-9 it admits over the limit in roughly a quarter of
    trials, so the trials are repeated. Correct code never exceeds the limit, so
    this cannot fail spuriously -- it can only miss a regression, never invent
    one.
    """
    import sys
    import threading

    from fastapi import HTTPException

    from gncl import api

    limit, threads, trials = 20, 60, 12
    worst = 0
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    try:
        for _ in range(trials):
            api._hits.clear()
            start = threading.Barrier(threads)
            admitted: list[int] = []
            tally = threading.Lock()  # guards the count, not the thing under test

            def hit(barrier=start, seen=admitted, guard=tally) -> None:
                barrier.wait()
                try:
                    api._bump("concurrent", limit)
                except HTTPException:
                    return
                with guard:
                    seen.append(1)

            workers = [threading.Thread(target=hit) for _ in range(threads)]
            for w in workers:
                w.start()
            for w in workers:
                w.join()
            worst = max(worst, len(admitted))
    finally:
        sys.setswitchinterval(previous_interval)
        api._hits.clear()

    assert worst == limit, f"admitted {worst} against a limit of {limit}"


@pytest.fixture
def offline(monkeypatch):
    """No key, so /chat resolves to the NullAnswerer.

    Without this the developer's own key is in the environment and these tests
    bill a real 63 KB call each. What is under test is the request contract, not
    the model.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("gncl.config.anthropic_key", lambda: "")


def _post(client, **body):
    return client.post("/chat", json={"question": "why?", **body}, auth=AUTH)


def test_chat_accepts_a_prior_exchange(client, offline):
    r = _post(client, history=[{"role": "user", "text": "q1"}, {"role": "assistant", "text": "a1"}])
    assert r.status_code == 200
    assert r.json()["usable"] is False, "the null answerer, not a billed call"


def test_chat_rejects_a_history_the_model_api_would_refuse(client, offline):
    """Caught here, or it surfaces later as "the model could not be reached"."""
    assert _post(client, history=[{"role": "assistant", "text": "a1"}]).status_code == 422
    assert _post(client, history=[{"role": "user", "text": "q1"}]).status_code == 422
    pair = [{"role": "user", "text": "q"}, {"role": "assistant", "text": "a"}]
    assert _post(client, history=pair * 11).status_code == 422


def test_chat_works_without_history(client, offline):
    """The first question of a conversation, and the standalone client."""
    assert client.post("/chat", json={"question": "why?"}, auth=AUTH).status_code == 200
