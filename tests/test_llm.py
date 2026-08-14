"""Audit integration tests.

These inject verdicts rather than calling a model. The integration logic is what
can be wrong; whether a given model answers well is #7's question, measured on a
gold set. Tests that need a 7.7 GB download to run are tests nobody runs.
"""

import pytest

from gncl.llm import VERDICT_SCHEMA, Verdict, audit_conflicts
from gncl.match import build
from gncl.output import build_guests
from gncl.ports import CsvSource


@pytest.fixture(scope="module")
def result():
    return build(source=CsvSource())


def verdicts_for(result, same_person=True, reason="diminutive of the booking name"):
    return {
        c["transaction_id"]: Verdict(same_person, 0.93, reason, "stub-model")
        for c in result.name_conflicts
    }


def test_schema_is_closed_and_minimal():
    assert set(VERDICT_SCHEMA["required"]) == {"same_person", "confidence", "reason"}
    assert VERDICT_SCHEMA["properties"]["same_person"]["type"] == "boolean"


def test_unavailable_verdict_is_not_a_silent_match():
    v = Verdict.unavailable("connection refused")
    assert v.same_person is False
    assert v.confidence == 0.0
    assert v.usable is False
    assert "unavailable" in v.reason


def test_usable_is_the_flag_consumers_must_check():
    """same_person is False on both failure kinds because the field needs a value."""
    assert Verdict.unavailable("x").usable is False
    assert Verdict.malformed("m", "x").usable is False
    assert Verdict(True, 0.9, "r", "m").usable is True


def test_confirming_audit_records_corroboration_without_changing_the_match(result):
    """A rule made the link. The model agreeing does not make it an LLM match.

    Folding the audit into match_method relabelled email-locked guests as
    LLM-matched and made the headline figures depend on whether a model happened
    to be reachable, so the suite only passed while the model was broken.
    """
    plain = {g.booking_ref: g for g in build_guests(result, source=CsvSource())}
    audited = {
        g.booking_ref: g
        for g in build_guests(result, verdicts=verdicts_for(result), source=CsvSource())
    }
    for ref in ("BK1013", "BK1018"):  # Elisabeth Holm, Anders Moe
        assert audited[ref].audit == "confirmed"
        assert audited[ref].match_method == plain[ref].match_method
        assert audited[ref].match_confidence == plain[ref].match_confidence
        assert "are the same person" in audited[ref].evidence
        assert "stub-model" in audited[ref].evidence


def test_confirming_audit_leaves_the_headline_figures_alone(result):
    """Running the model must not change the method split or the accept count."""
    from gncl.output import to_dataframe

    plain = to_dataframe(build_guests(result, source=CsvSource()))
    audited = to_dataframe(build_guests(result, verdicts=verdicts_for(result), source=CsvSource()))
    assert plain["match_method"].value_counts().to_dict() == (
        audited["match_method"].value_counts().to_dict()
    )
    assert (plain["match_confidence"] >= 0.8).sum() == (audited["match_confidence"] >= 0.8).sum()


def test_malformed_response_is_not_reported_as_a_contradiction(result):
    """The model said nothing usable. Saying it disagreed inverts its meaning."""
    bad = {
        c["transaction_id"]: Verdict.malformed("stub-model", "non-JSON: yes same person")
        for c in result.name_conflicts
    }
    guests = {g.booking_ref: g for g in build_guests(result, verdicts=bad, source=CsvSource())}
    for ref in ("BK1013", "BK1018"):
        assert guests[ref].audit == "not run"
        assert "CONTRADICTS" not in guests[ref].evidence
        assert guests[ref].match_confidence >= 0.8, "an unusable answer must not demote the link"


def test_contradicting_audit_drops_confidence(result):
    guests = {
        g.booking_ref: g
        for g in build_guests(
            result, verdicts=verdicts_for(result, same_person=False), source=CsvSource()
        )
    }
    for ref in ("BK1013", "BK1018"):
        assert guests[ref].audit == "contradicted"
        assert guests[ref].match_confidence <= 0.50
        assert "CONTRADICTS" in guests[ref].evidence


def test_contradicted_links_fall_into_the_review_queue(result):
    guests = build_guests(
        result, verdicts=verdicts_for(result, same_person=False), source=CsvSource()
    )
    below = [g for g in guests if g.match_confidence < 0.80]
    assert {"BK1013", "BK1018"} <= {g.booking_ref for g in below}


def test_missing_model_keeps_the_link_and_says_why(result):
    """Degradation must be visible in the output, not silent."""
    guests = {g.booking_ref: g for g in build_guests(result, verdicts=None, source=CsvSource())}
    beth = guests["BK1013"]
    assert beth.audit == "not run"
    assert "name conflict unresolved" in beth.evidence
    assert "link kept, flagged" in beth.evidence


def test_audit_asks_each_distinct_question_once(result):
    """4 transactions, 2 distinct name pairs, 2 model calls.

    This is the caching that makes the approach affordable at scale, where name
    variants repeat heavily across a guest population.
    """

    class CountingJudge:
        name = "stub"

        def __init__(self):
            self.calls = []

        def judge(self, captured, booking, context=""):
            self.calls.append((captured, booking))
            return Verdict(True, 0.9, "stub", self.name)

    judge = CountingJudge()
    out = audit_conflicts(result.name_conflicts, judge=judge)
    calls = judge.calls
    assert len(result.name_conflicts) == 4
    assert len(calls) == 2
    assert len(out) == 4
    assert {c[0] for c in calls} == {"Beth", "Andy"}


def test_evidence_is_not_duplicated_per_transaction(result):
    guests = {
        g.booking_ref: g
        for g in build_guests(result, verdicts=verdicts_for(result), source=CsvSource())
    }
    evidence = guests["BK1013"].evidence
    assert evidence.count("are the same person") == 1


def test_audit_only_touches_flagged_components(result):
    """A confirming audit must not change guests that had no name conflict."""
    plain = {g.guest_id: g for g in build_guests(result, source=CsvSource())}
    audited = {
        g.guest_id: g
        for g in build_guests(result, verdicts=verdicts_for(result), source=CsvSource())
    }
    flagged = {"G-BK1013", "G-BK1018"}
    for gid, g in plain.items():
        if gid in flagged:
            continue
        assert g.match_method == audited[gid].match_method
        assert g.match_confidence == audited[gid].match_confidence
        assert g.evidence == audited[gid].evidence


def test_protocols_are_satisfied_by_the_real_adapters():
    """The seams exist as types, not just as convention."""
    from gncl.llm import OllamaJudge
    from gncl.ports import CsvSource, GuestSource, NameJudge, NullJudge

    assert isinstance(CsvSource(), GuestSource)
    assert isinstance(OllamaJudge(), NameJudge)
    assert isinstance(NullJudge(), NameJudge)


def test_core_modules_do_not_import_concrete_infrastructure():
    """match, output and analysis must not reach for load_all or ollama.

    A `frames or load_all()` default is a hidden dependency, not a seam: it lets
    a core module resolve three named CSVs from a sibling directory on its own.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "gncl"
    for module in ("match.py", "output.py"):
        text = (root / module).read_text()
        assert "load_all" not in text, f"{module} still reaches for the loader"
        assert "import ollama" not in text


def test_build_refuses_to_invent_a_source():
    """No silent fallback to reading the sample data."""
    import pytest

    from gncl.match import build

    with pytest.raises(ValueError, match="GuestSource"):
        build()
