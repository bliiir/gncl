"""The talk-to-the-data chat.

The model is never called here. What is testable without a network is what
actually matters: that it refuses to answer from nothing, that the grounding
carries what the answers must cite, and that the key never reaches the browser.
"""

import pandas as pd
import pytest

from gncl import chat, config


@pytest.fixture
def df():
    return pd.DataFrame(
        {
            "guest_id": ["G-1", "G-2", "G-3"],
            "match_method": ["deterministic", "rule_fuzzy", "single source"],
            "match_confidence": [0.98, 0.55, 0.0],
            "total_spend": [100.0, 50.5, 0.0],
            "audit": ["", "not run", ""],
        }
    )


def test_no_key_means_no_answer_not_a_guess(monkeypatch):
    """Same discipline as the pipeline audit: report that it did not run."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    answerer = chat.build()
    assert isinstance(answerer, chat.NullAnswerer)
    result = answerer.answer("why is G-1 accepted?", "grounding")
    assert result.usable is False
    assert "ANTHROPIC_API_KEY" in result.text
    assert result.model == ""


def test_unusable_is_distinct_from_a_negative_answer():
    """`usable` exists so "no answer" cannot be read as an answer of no."""
    assert chat.Answer("no, they are different people").usable is True
    assert chat.Answer.unavailable("no model configured").usable is False


def test_a_key_selects_the_hosted_answerer(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    answerer = chat.build()
    assert isinstance(answerer, chat.AnthropicAnswerer)
    assert answerer.name == config.anthropic_model()


def test_counts_are_computed_not_left_to_the_model(df):
    """A model asked to count 35 CSV rows answers confidently and wrongly."""
    facts = chat.facts(df, 0.80)
    assert "Guests resolved: 3" in facts
    assert "Accept threshold: 0.8" in facts
    assert "1 deterministic" in facts
    assert "150.50" in facts  # summed in pandas, not by the model


def test_grounding_carries_the_documents_answers_must_cite(df, tmp_path):
    grounding = chat.context(df, 0.80, outdir=tmp_path)
    for rel in chat.GROUNDING:
        assert rel in grounding, f"{rel} missing from grounding"
    assert "MEASURED FACTS" in grounding
    # The brief is what the work is judged against; without it "does this meet
    # the case requirements?" cannot be answered.
    assert "cabin number recorded correctly in one system" in grounding


def test_grounding_includes_the_output_csv_when_it_exists(df, tmp_path):
    (tmp_path / "guests.csv").write_text("guest_id,review\nG-1,accepted\n")
    grounding = chat.context(df, 0.80, outdir=tmp_path)
    assert "guests.csv" in grounding
    assert "G-1,accepted" in grounding


def test_grounding_carries_the_raw_sources(df, tmp_path):
    """The output records that two rows were merged; only the source says what
    each row actually held."""
    grounding = chat.context(df, 0.80, outdir=tmp_path)
    for rel in chat.SOURCES:
        assert rel in grounding, f"{rel} missing from grounding"
    assert "raw source, as delivered" in grounding
    # The short form reaches the model as LS Retail recorded it: "Andy" on
    # TX5034 is the only place the audit's input is visible.
    assert "TX5034,3145,Andy" in grounding


def test_source_row_counts_are_measured_not_left_to_the_model(df, tmp_path):
    counts = chat.source_facts()
    assert "bookit_guests.csv: 32 rows" in counts
    assert "ls_retail_transactions.csv: 57 rows" in counts
    assert "hubspot_contacts.csv: 31 rows" in counts
    assert counts in chat.context(df, 0.80, outdir=tmp_path)


def test_missing_output_is_not_fatal(df, tmp_path):
    """Chat must still answer questions about the approach before resolve is run."""
    grounding = chat.context(df, 0.80, outdir=tmp_path / "nope")
    assert "MEASURED FACTS" in grounding
    assert "docs/DATA_ANALYSIS.md" in grounding


def test_history_becomes_earlier_turns_not_a_pasted_blob():
    """ "Why?" only means something if the prior exchange is a real turn."""
    history = [("user", "who are the two Anna Larsens?"), ("assistant", "BK1002 and BK1019.")]
    msgs = chat.messages("why are they different people?", "GROUNDING", history)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == "BK1002 and BK1019."
    assert msgs[-1]["content"] == "why are they different people?"


def test_the_evidence_pack_is_sent_once_per_call_not_once_per_turn():
    """63 KB per turn would make a ten-turn conversation ten times the cost."""
    history = [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
    msgs = chat.messages("q3", "GROUNDING", history)
    assert sum(m["content"].count("GROUNDING") for m in msgs) == 1
    assert msgs[0]["content"].startswith("GROUNDING")


def test_system_prompt_forbids_deriving_numbers_and_requires_citation():
    assert "Do not calculate" in chat.SYSTEM
    assert "guest_id" in chat.SYSTEM
    # Spec section 7: a single-source row is a correct outcome, not a failure.
    # The model is told so, or it will describe the 5 lone records as errors.
    assert "correct outcomes, not errors" in chat.SYSTEM


def test_available_tracks_the_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert chat.available() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    assert chat.available() is True


def test_the_key_never_reaches_the_page_or_the_response(monkeypatch):
    """The browser posts a question; the server holds the credential.

    This is why chat is served rather than baked into the standalone file: a
    static page calling the model directly would have to carry the key.
    """
    import importlib

    from fastapi.testclient import TestClient

    canary = "sk-ant-canary-must-not-leak"
    monkeypatch.setenv("ANTHROPIC_API_KEY", canary)
    monkeypatch.setenv("GNCL_AUTH_USER", "u")
    monkeypatch.setenv("GNCL_AUTH_PASSWORD", "p")
    from gncl import api

    reloaded = importlib.reload(api)
    try:
        client = TestClient(reloaded.app)
        page = client.get("/", auth=("u", "p"))
        assert page.status_code == 200
        assert canary not in page.text
        assert "ANTHROPIC_API_KEY" not in page.text
        # The chat tab is live, so the page must not claim it is unavailable.
        assert "Chat needs a hosted model" not in page.text
    finally:
        monkeypatch.undo()
        importlib.reload(api)


def test_chat_endpoint_reports_unavailable_rather_than_failing(monkeypatch):
    """No key is a state of the system, not a 500."""
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GNCL_AUTH_USER", "u")
    monkeypatch.setenv("GNCL_AUTH_PASSWORD", "p")
    from gncl import api

    reloaded = importlib.reload(api)
    try:
        client = TestClient(reloaded.app)
        r = client.post("/chat", json={"question": "why is G-BK1021 in review?"}, auth=("u", "p"))
        assert r.status_code == 200
        body = r.json()
        assert body["usable"] is False
        assert "ANTHROPIC_API_KEY" in body["answer"]
        assert body["grounded_on"]
    finally:
        monkeypatch.undo()
        importlib.reload(api)


def test_chat_requires_auth(monkeypatch):
    """An open endpoint that reaches a paid model is an open proxy."""
    import importlib

    from fastapi.testclient import TestClient

    monkeypatch.setenv("GNCL_AUTH_USER", "u")
    monkeypatch.setenv("GNCL_AUTH_PASSWORD", "p")
    from gncl import api

    reloaded = importlib.reload(api)
    try:
        client = TestClient(reloaded.app)
        assert client.post("/chat", json={"question": "hi"}).status_code == 401
        assert client.get("/").status_code == 401
    finally:
        monkeypatch.undo()
        importlib.reload(api)
