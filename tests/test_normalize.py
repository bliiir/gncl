"""Folding tests target the functions directly.

Every name in the GNCL dataset is spelled identically across sources, so a test
that only asserts on that data passes with a completely broken normalizer. These
assert on the characters themselves.
"""

import unicodedata
from datetime import date

import pytest

from gncl.normalize import (
    Name,
    fold,
    fold_stripped,
    in_stay_window,
    name_keys,
    normalize_email,
    normalize_name,
    normalize_phone,
    parse_date,
    repaired_name,
)


def test_nfkd_alone_is_insufficient():
    """The reason this module exists. Guards against a 'simplification' back to NFKD."""
    for ch in ("ø", "æ"):
        decomposed = unicodedata.normalize("NFKD", ch)
        stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
        assert stripped == ch, f"{ch!r} unexpectedly decomposed; revisit the fold tables"
    # a-ring does decompose, but to "a" rather than the conventional "aa".
    assert (
        "".join(c for c in unicodedata.normalize("NFKD", "å") if not unicodedata.combining(c))
        == "a"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Bjørn", "bjoern"),
        ("Sørensen", "soerensen"),
        ("Ærø", "aeroe"),
        ("Århus", "aarhus"),
        ("Malmö", "malmoe"),
        ("Sjöberg", "sjoeberg"),
    ],
)
def test_conventional_fold(raw, expected):
    assert fold(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("Bjørn", "bjorn"), ("Sørensen", "sorensen"), ("Århus", "arhus"), ("Malmö", "malmo")],
)
def test_stripped_fold(raw, expected):
    assert fold_stripped(raw) == expected


def test_non_scandinavian_diacritics_still_fold():
    assert fold("José") == "jose"
    assert fold("Müller") == "mueller"  # umlaut handled by the table, not NFKD


def test_name_keys_bridge_transliterations():
    """'Bjorn' and 'Bjoern' are the same person spelled two ways."""
    assert name_keys("Bjørn Kristiansen") & name_keys("Bjorn Kristiansen")
    assert name_keys("Bjørn Kristiansen") & name_keys("Bjoern Kristiansen")
    assert not name_keys("Bjørn Kristiansen") & name_keys("Nils Berg")


def test_normalize_name_strips_punctuation_and_case():
    assert normalize_name("  O'Brien-Smith  ") == "o brien smith"
    assert normalize_name("Anna  Larsen") == "anna larsen"
    assert normalize_name("") == ""


def test_normalize_email():
    assert normalize_email("  Anna.Larsen@GMail.com ") == "anna.larsen@gmail.com"
    assert normalize_email("") == ""
    assert normalize_email(None) == ""


def test_normalize_phone():
    assert normalize_phone("+47 2727 0733") == "+4727270733"
    assert normalize_phone(None) == ""


@pytest.mark.parametrize(
    "raw", ["2026-06-11", "11/06/2026", "11-06-2026", "2026/06/11", "11.06.2026"]
)
def test_parse_date_formats(raw):
    assert parse_date(raw) == date(2026, 6, 11)


def test_parse_date_empty_and_bad():
    assert parse_date("") is None
    assert parse_date(None) is None
    with pytest.raises(ValueError):
        parse_date("not a date")


def test_stay_window_is_closed_on_both_ends():
    checkin, checkout = date(2026, 7, 11), date(2026, 7, 14)
    assert in_stay_window(checkin, checkin, checkout), "check-in day must be inside"
    assert in_stay_window(checkout, checkin, checkout), "check-out day must be inside"
    assert in_stay_window(date(2026, 7, 12), checkin, checkout)
    assert not in_stay_window(date(2026, 7, 10), checkin, checkout)
    assert not in_stay_window(date(2026, 7, 15), checkin, checkout)


def test_stay_window_missing_bounds():
    assert not in_stay_window(date(2026, 7, 12), None, date(2026, 7, 14))
    assert not in_stay_window(date(2026, 7, 12), date(2026, 7, 11), None)


class TestName:
    """Eight call sites used to split name strings by hand; one crashed."""

    def test_parts_of_a_normal_name(self):
        n = Name("Anna Larsen")
        assert n.first == "anna"
        assert n.surname == "larsen"
        assert bool(n)

    def test_empty_name_yields_empty_parts_not_an_indexerror(self):
        for raw in ("", "   ", None):
            n = Name(raw or "")
            assert n.first == ""
            assert n.surname == ""
            assert not n

    def test_single_token_has_no_surname(self):
        """'Beth' from the POS system is a first name, not a surname."""
        n = Name("Beth")
        assert n.first == "beth"
        assert n.surname == ""

    def test_diacritics_survive_into_the_parts(self):
        assert Name("Bjørn Kristiansen").first == "bjoern"
        assert Name("Freja Sørensen").surname == "soerensen"

    def test_keys_bridge_transliterations(self):
        assert Name("Bjørn Kristiansen").keys & Name("Bjorn Kristiansen").keys


class TestRepairedName:
    """Moved here from the analysis module: the matcher depends on it."""

    def test_email_local_part_corrects_a_typo(self):
        row = {"guest_name": "Mikael Svnesson", "email_norm": "mikael.svensson@yahoo.com"}
        assert repaired_name(row) == "mikael svensson"

    def test_an_initial_is_an_abbreviation_not_a_correction(self):
        row = {"guest_name": "Maja Karlsson", "email_norm": "m.karlsson@gmail.com"}
        assert repaired_name(row) == "maja karlsson"

    def test_no_email_returns_the_normalized_name(self):
        assert repaired_name({"guest_name": "Anna Larsen", "email_norm": ""}) == "anna larsen"

    def test_mismatched_token_count_is_left_alone(self):
        row = {"guest_name": "Anna Larsen", "email_norm": "annalarsen@gmail.com"}
        assert repaired_name(row) == "anna larsen"


def test_repair_refuses_an_unrelated_name_from_a_shared_email():
    """A household email must not rewrite whose booking this is.

    "Anna Berg" with nils.berg@gmail.com repaired to "nils berg" and then matched
    HubSpot's Nils Berg at 0.90 -- a silent identity swap, and shared household
    addresses are exactly the messy-data class this case is about.
    """
    from gncl.normalize import repaired_name

    row = {"guest_name": "Anna Berg", "email_norm": "nils.berg@gmail.com"}
    assert repaired_name(row) == "anna berg"


def test_repair_still_fixes_every_genuine_typo_in_the_dataset():
    """The guard must not cost the four corrections it exists alongside."""
    from gncl.normalize import repaired_name

    cases = [
        ("Camilla Strnad", "camilla.strand@gmail.com", "camilla strand"),
        ("Camilla Stradn", "camilla.strand@gmail.com", "camilla strand"),
        ("Mikael Svnesson", "mikael.svensson@yahoo.com", "mikael svensson"),
        ("Mikael Sevnsson", "mikael.svensson@yahoo.com", "mikael svensson"),
    ]
    for name, email, expected in cases:
        assert repaired_name({"guest_name": name, "email_norm": email}) == expected, name


def test_the_repair_threshold_sits_between_the_two_populations():
    """Derived from measurement, not chosen: typos 0.83-0.88, wrong names 0.18-0.25."""
    from difflib import SequenceMatcher

    from gncl.normalize import MIN_REPAIR_RATIO

    typos = [("strnad", "strand"), ("svnesson", "svensson"), ("stradn", "strand")]
    others = [("anna", "nils"), ("marcus", "nils"), ("tone", "fredrik")]
    worst_typo = min(SequenceMatcher(None, a, b).ratio() for a, b in typos)
    best_other = max(SequenceMatcher(None, a, b).ratio() for a, b in others)
    assert best_other < MIN_REPAIR_RATIO < worst_typo, (
        f"threshold {MIN_REPAIR_RATIO} is not between {best_other:.2f} and {worst_typo:.2f}"
    )


def test_an_initial_is_still_an_abbreviation_not_a_correction():
    from gncl.normalize import repaired_name

    assert (
        repaired_name({"guest_name": "Jon Hansen", "email_norm": "j.hansen@x.com"}) == "jon hansen"
    )
