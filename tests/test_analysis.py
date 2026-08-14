"""Pins the measured findings in docs/DATA_ANALYSIS.md.

If a number in the report changes, a test here fails and the report must be
regenerated with `python -m gncl.analysis`.
"""

import pytest

from gncl.analysis import (
    blocking_with_repair,
    email_localpart_vs_name,
    known_true_pairs,
    repaired_name,
    run,
)
from gncl.load import load_all


@pytest.fixture(scope="module")
def result():
    return run()


def test_ground_truth_is_21_email_locked_pairs(result):
    assert len(result["pairs"]) == 21


def test_phone_country_code_is_anti_correlated(result):
    """Weaker on true pairs than on random ones, so it is not just noise."""
    sig = next(s for s in result["signals"] if "phone" in s.name)
    assert sig.true_rate == pytest.approx(4 / 21, abs=0.01)
    assert sig.base_rate > sig.true_rate, "phone CC should be worse than chance"
    assert sig.verdict == "reject"


def test_contact_date_looks_strong_but_has_no_lift(result):
    """100% on true pairs, 93% on random pairs. The baseline is what kills it."""
    sig = next(s for s in result["signals"] if "contact" in s.name)
    assert sig.true_rate == 1.0
    assert sig.base_rate > 0.9
    assert sig.lift < 1.2
    assert sig.verdict == "reject"


def test_exact_name_misses_exactly_the_two_typo_pairs(result):
    sig = next(s for s in result["signals"] if s.name == "exact normalized name")
    assert sig.true_n == 21
    assert round(sig.true_rate * 21) == 19, "19 of 21 true pairs share an exact name"


def test_email_localpart_repairs_all_four_corrupted_names():
    frames = load_all()
    e = email_localpart_vs_name(frames["bookit"], frames["hubspot"])
    corrected = {(n, tok) for n, _local, tok, _raw in e["corrections"]}
    assert corrected == {
        ("Camilla Strnad", "strand"),
        ("Camilla Stradn", "strand"),
        ("Mikael Svnesson", "svensson"),
        ("Mikael Sevnsson", "svensson"),
    }


def test_initials_are_not_treated_as_corrections():
    frames = load_all()
    e = email_localpart_vs_name(frames["bookit"], frames["hubspot"])
    abbrev = {n for n, _l, _t, _r in e["abbreviations"]}
    assert "Maja Karlsson" in abbrev
    assert "Pernille Rasmussen" in abbrev
    assert not any(n == "Maja Karlsson" for n, _l, _t, _r in e["corrections"])


def test_name_repair_converges_the_typo_pairs():
    frames = load_all()
    bk, hs = frames["bookit"], frames["hubspot"]
    bk_i = {r["booking_ref"]: repaired_name(r) for _, r in bk.iterrows()}
    hs_i = {r["contact_id"]: repaired_name(r) for _, r in hs.iterrows()}
    assert bk_i["BK1014"] == hs_i["HS214"] == "mikael svensson"
    assert bk_i["BK1019"] == hs_i["HS219"] == "camilla strand"


def test_repair_lifts_blocking_recall_to_100_percent():
    frames = load_all()
    bk, hs = frames["bookit"], frames["hubspot"]
    pairs = known_true_pairs(bk, hs)
    plain = next(b for b in run()["blocking"] if b["key"] == "full name")
    repaired = blocking_with_repair(bk, hs, pairs)
    assert plain["recall"] < 1.0
    assert repaired["recall"] == 1.0
    assert repaired["reduction"] > 0.95


def test_surname_morphology_is_accurate_but_inapplicable(result):
    """94% accurate, and useless: HubSpot has no nationality column."""
    sm = result["surname_morphology"]
    assert sm["rate"] > 0.9
    assert "nationality" not in load_all()["hubspot"].columns


def test_report_is_current():
    """Regenerate with `python -m gncl.analysis` if this fails."""
    from pathlib import Path

    from gncl.analysis import render

    path = Path(__file__).resolve().parent.parent / "docs" / "DATA_ANALYSIS.md"
    assert path.exists(), "run python -m gncl.analysis"
    assert path.read_text() == render(), "DATA_ANALYSIS.md is stale"


def test_cabin_adjacency_survives_an_unnamed_booking():
    """Raised IndexError before: an unnamed row next to an occupied cabin."""
    import pandas as pd

    from gncl.analysis import cabin_structure

    base = {"nationality": "SE", "guest_name": "X Y", "name_norm": "x y"}
    df = pd.DataFrame(
        [{**base, "cabin": "4021"}, {**base, "cabin": "4022", "guest_name": "", "name_norm": ""}]
    )
    out = cabin_structure(df)
    assert out["adjacent"], "adjacency should still be reported"
    assert all(match is False for *_, match in out["adjacent"])


def test_cabin_adjacency_survives_a_non_numeric_cabin():
    """Raised ValueError before: int() on a cabin id like 'A12'."""
    import pandas as pd

    from gncl.analysis import cabin_structure

    df = pd.DataFrame(
        [{"nationality": "SE", "guest_name": "X Y", "name_norm": "x y", "cabin": "A12"}]
    )
    assert cabin_structure(df)["adjacent"] == []


def test_cabin_adjacency_keeps_both_occupants_of_a_reused_cabin():
    """Cabin 6208 has two guests; a dict keyed on cabin kept only the last."""
    from gncl.analysis import cabin_structure
    from gncl.load import load_all

    out = cabin_structure(load_all()["bookit"])
    # 6208 is occupied by Nils Berg in June and Marcus Berg in July; 6209 is
    # Thomas Berg. Both pairings must appear, and both are same-surname.
    pairs = {(c, name, nxt, other) for c, name, nxt, other, _ in out["adjacent"]}
    assert ("6208", "Nils Berg", "6209", "Thomas Berg") in pairs
    assert ("6208", "Marcus Berg", "6209", "Thomas Berg") in pairs
    matches = {m for c, _, _, _, m in out["adjacent"] if c == "6208"}
    assert matches == {True}, "all three are Bergs"
