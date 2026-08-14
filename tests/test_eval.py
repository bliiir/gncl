"""Eval harness tests. Run against stubs; real model runs are #7's blocked half.

These inject a judge rather than patching `gncl.eval.adjudicate`. Needing to
monkeypatch a module attribute was the tell that the seam was missing; now the
seam is a constructor argument.
"""

import pytest

from gncl.eval import GOLD, SWEDISH, ModelResult, render, run_model
from gncl.llm import Verdict


def test_gold_set_is_balanced_enough_to_catch_a_constant_answerer():
    """A model that always says True, or always False, must not score well."""
    yes = sum(1 for c in GOLD if c.same_person)
    no = len(GOLD) - yes
    assert yes >= 5 and no >= 5, f"{yes} positive / {no} negative is too lopsided"
    assert max(yes, no) / len(GOLD) <= 0.7


def test_gold_set_contains_the_live_cases():
    live = {(c.captured, c.booking) for c in GOLD if c.kind == "diminutive"}
    assert live == {("Beth", "Elisabeth Holm"), ("Andy", "Anders Moe")}


def test_gold_set_pairs_each_diminutive_with_a_same_ratio_negative():
    """The point of the set: 0.60 positives and 0.60 negatives coexist."""
    assert any(c.kind == "shared surname" and not c.same_person for c in GOLD)
    assert any(c.kind == "diminutive" and c.same_person for c in GOLD)


def test_substring_trap_is_present_in_both_directions():
    assert any(
        c.captured == "Fredrik" and c.booking == "Tone Fredriksen" and not c.same_person
        for c in GOLD
    )
    assert any(
        c.captured == "Fredrik" and c.booking == "Fredrik Lund" and c.same_person for c in GOLD
    )


class StubJudge:
    """A `NameJudge` with a fixed or scripted answer."""

    def __init__(self, answer=None, name="stub"):
        self._answer = answer
        self.name = name
        self.calls = []

    def judge(self, captured, booking, context=""):
        self.calls.append((captured, booking))
        if callable(self._answer):
            return self._answer(captured, booking)
        return self._answer


class OracleJudge(StubJudge):
    """Always right, by looking the answer up in the gold set."""

    def judge(self, captured, booking, context=""):
        self.calls.append((captured, booking))
        case = next(c for c in GOLD + SWEDISH if c.captured == captured and c.booking == booking)
        return Verdict(case.same_person, 0.99, "oracle", self.name)


def test_perfect_model_scores_100():
    r = run_model("oracle", judge=OracleJudge(name="oracle"))
    assert r.accuracy == 1.0
    assert r.correct == len(GOLD)
    assert not r.failures


def test_always_true_model_is_caught():
    r = run_model("always-true", judge=StubJudge(Verdict(True, 0.9, "always yes", "stub")))
    assert r.accuracy < 0.7
    assert r.failures


def test_always_false_model_is_caught():
    r = run_model("always-false", judge=StubJudge(Verdict(False, 0.9, "always no", "stub")))
    assert r.accuracy < 0.7
    assert r.failures


def test_unavailable_model_reports_not_run_rather_than_zero():
    """A missing model must not look like a model that scored 0%."""
    r = run_model("absent", judge=StubJudge(Verdict.unavailable("connection refused")))
    assert r.unavailable
    assert r.total == 0
    assert "not run" in render([r])


def test_render_carries_the_calibration_warning():
    out = render([ModelResult(model="stub", correct=13, total=14, latencies=[0.4])])
    assert "7 percentage points" in out
    assert "93%" in out


def test_render_lists_failures():
    out = render([run_model("always-true", judge=StubJudge(Verdict(True, 0.9, "y", "stub")))])
    assert "got these wrong" in out
    assert "Nils Berg" in out


@pytest.mark.parametrize("case", GOLD, ids=lambda c: f"{c.captured}|{c.booking}")
def test_every_gold_case_is_documented(case):
    assert case.kind and case.note, "each case must say what it tests and where it came from"


def test_report_structure_is_current():
    """Catches a stale gold set or candidate list, everywhere.

    Deliberately not a byte comparison. The report embeds the reason each model
    did not run, and that text is environment-specific: a machine with an Ollama
    server but no models records a 404, one with no server records a
    ConnectionError. Byte-matching made the suite pass only on the machine that
    generated the file, which is what CI caught.
    """
    from pathlib import Path

    from gncl.eval import CANDIDATES, SETS

    text = (Path(__file__).resolve().parent.parent / "docs" / "MODEL_EVAL.md").read_text()
    for model in CANDIDATES:
        assert model in text, f"{model} missing from the report"
    for name, cases in SETS.items():
        assert f"Gold set: {len(cases)} pairs" in text, f"{name} set size is stale"
    assert "baseline: always yes" in text
    assert "baseline: always no" in text


def test_report_bytes_are_current_when_a_model_is_reachable():
    """The strict check, run only where the report can actually be reproduced.

    Latency columns are blanked on both sides first. They are wall-clock
    measurements, so a byte-comparison including them can never pass twice on
    the same machine, let alone another one -- the earlier version of this test
    was unfalsifiable rather than strict, and only looked green because no
    server was reachable to run it. Everything that carries a claim (models
    scored, accuracy, correct counts, every case each model got wrong) is still
    compared byte for byte.
    """
    import re
    from pathlib import Path

    from gncl.eval import CANDIDATES, SETS, render_all, run_model
    from gncl.llm import available

    missing = [m for m in CANDIDATES if not available(model=m)]
    if missing:
        pytest.skip(f"not installed locally, so the table cannot be reproduced: {missing}")

    def without_latencies(text: str) -> str:
        return re.sub(r"\| \d+\.\d\ds \| \d+\.\d\ds \|", "| - | - |", text)

    path = Path(__file__).resolve().parent.parent / "docs" / "MODEL_EVAL.md"
    sections = {
        name: [run_model(m, gold=cases) for m in CANDIDATES] for name, cases in SETS.items()
    }
    assert without_latencies(path.read_text()) == without_latencies(render_all(sections)), (
        "MODEL_EVAL.md is stale"
    )


def test_swedish_set_is_balanced():
    from gncl.eval import SWEDISH

    yes = sum(1 for c in SWEDISH if c.same_person)
    assert 0.4 <= yes / len(SWEDISH) <= 0.6, "a constant answerer must not score well"


def test_swedish_set_contains_genuinely_interleaved_scores():
    """The point of this set: identical ratios, opposite truths.

    The English trap set separates at 0.55, so on its own it does not justify a
    model. This one does not separate at any threshold.
    """
    from difflib import SequenceMatcher

    from gncl.eval import SWEDISH
    from gncl.normalize import normalize_name

    def first(name):
        return normalize_name(name).split()[0]

    def ratio(case):
        return SequenceMatcher(None, first(case.captured), first(case.booking)).ratio()

    pos = [ratio(c) for c in SWEDISH if c.same_person]
    neg = [ratio(c) for c in SWEDISH if not c.same_person]
    assert min(pos) < max(neg), "some true match must score below some true negative"
    overlap = [p for p in pos for n in neg if abs(p - n) < 0.01]
    assert overlap, "some positive and negative must share a score"


def test_both_sets_are_reported():
    from gncl.eval import SETS

    assert set(SETS) == {"traps", "swedish"}
