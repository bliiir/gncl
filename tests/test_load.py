"""Loader tests double as regression guards on the spec's measured claims.

Numbers here come from docs/SPECIFICATION.md section 2. If a claim in the spec
is wrong, one of these should fail.
"""

from datetime import date

from gncl.load import load_all
from gncl.normalize import in_stay_window


def test_row_counts():
    frames = load_all()
    assert len(frames["bookit"]) == 32
    assert len(frames["hubspot"]) == 31
    assert len(frames["ls_retail"]) == 57


def test_gap_counts():
    frames = load_all()
    assert (frames["bookit"]["email_norm"] == "").sum() == 6
    assert (frames["bookit"]["cabin"] == "").sum() == 1
    assert (frames["hubspot"]["email_norm"] == "").sum() == 7
    assert (frames["ls_retail"]["cabin"] == "").sum() == 4


def test_dates_parsed_not_silently_dropped():
    frames = load_all()
    assert frames["bookit"]["checkin"].notna().all()
    assert frames["bookit"]["checkout"].notna().all()
    assert frames["ls_retail"]["txn_date"].notna().all()
    assert frames["hubspot"]["last_contacted"].notna().all()


def test_amounts_numeric():
    ls = load_all()["ls_retail"]
    assert ls["amount_num"].notna().all()
    assert ls["amount_num"].gt(0).all()


def test_cabin_6208_is_reused_by_two_guests():
    """Spec 2.2: cabin alone is not a key."""
    bk = load_all()["bookit"]
    rows = bk[bk["cabin"] == "6208"].sort_values("checkin")
    assert len(rows) == 2
    assert rows["guest_name"].tolist() == ["Nils Berg", "Marcus Berg"]
    # Disjoint stays, so cabin + window disambiguates but cabin alone does not.
    first, second = rows.iloc[0], rows.iloc[1]
    assert first["checkout"] < second["checkin"]


def test_two_distinct_anna_larsens():
    """Spec 2.3: the load-bearing assumption, asserted so a change is visible."""
    bk = load_all()["bookit"]
    annas = bk[bk["name_norm"] == "anna larsen"].sort_values("checkin")
    assert len(annas) == 2
    assert annas["cabin"].tolist() == ["4022", "4021"]
    assert annas["nationality"].tolist() == ["SE", "DK"]
    assert annas.iloc[0]["checkout"] < annas.iloc[1]["checkin"]


def test_cabin_matched_boundary_transactions():
    """Scope: cabin-matched rows only. The full count is in test_match.py.

    An earlier version of this test skipped no-cabin rows and was cited as
    evidence for a total, which undercounted by 2. It now says what it covers.
    """
    frames = load_all()
    bk, ls = frames["bookit"], frames["ls_retail"]
    on_checkin = on_checkout = 0
    for _, t in ls.iterrows():
        if not t["cabin"]:
            continue
        for _, b in bk[bk["cabin"] == t["cabin"]].iterrows():
            if t["txn_date"] == b["checkin"]:
                on_checkin += 1
            if t["txn_date"] == b["checkout"]:
                on_checkout += 1
    assert (on_checkin, on_checkout) == (8, 6)


def test_cabin_and_window_resolves_53_of_57_with_zero_ambiguity():
    """Spec 2.1. The 4 unresolved are the rows with no cabin recorded."""
    frames = load_all()
    bk, ls = frames["bookit"], frames["ls_retail"]
    resolved = ambiguous = no_cabin = 0
    for _, t in ls.iterrows():
        if not t["cabin"]:
            no_cabin += 1
            continue
        hits = [
            b
            for _, b in bk[bk["cabin"] == t["cabin"]].iterrows()
            if in_stay_window(t["txn_date"], b["checkin"], b["checkout"])
        ]
        if len(hits) == 1:
            resolved += 1
        elif len(hits) > 1:
            ambiguous += 1
    assert (resolved, ambiguous, no_cabin) == (53, 0, 4)


def test_raw_values_preserved_for_evidence():
    ls = load_all()["ls_retail"]
    beth = ls[ls["guest_name_captured"] == "Beth"]
    assert len(beth) == 2
    assert set(beth["transaction_id"]) == {"TX5023", "TX5024"}
    assert (beth["cabin"] == "3067").all()


def test_stay_window_helper_matches_loaded_dates():
    bk = load_all()["bookit"]
    row = bk[bk["booking_ref"] == "BK1001"].iloc[0]
    assert row["checkin"] == date(2026, 7, 11)
    assert row["checkout"] == date(2026, 7, 14)
    assert in_stay_window(date(2026, 7, 14), row["checkin"], row["checkout"])


def _sample_copy(tmp_path, drop_rows=0):
    """Copy the bundled CSVs, optionally dropping transactions."""
    import shutil

    from gncl.load import DATA_DIR

    for name in ("bookit_guests.csv", "hubspot_contacts.csv", "ls_retail_transactions.csv"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    if drop_rows:
        path = tmp_path / "ls_retail_transactions.csv"
        lines = path.read_text().splitlines()
        path.write_text("\n".join(lines[: len(lines) - drop_rows]) + "\n")
    return tmp_path


def test_row_counts_are_enforced_on_the_bundled_sample():
    """The guard that catches a corrupted or truncated checkout of this repo."""
    from gncl.load import EXPECTED_ROWS, load_all

    frames = load_all()
    for name, expected in EXPECTED_ROWS.items():
        assert len(frames[name]) == expected


def test_a_user_supplied_directory_is_not_held_to_the_sample_counts(tmp_path):
    """`--data` exists to run other datasets. EXPECTED_ROWS rejected all of them.

    The counts are a regression guard on the sample, not a schema constraint.
    """
    from gncl.load import load_all

    other = _sample_copy(tmp_path, drop_rows=3)
    frames = load_all(other)
    assert len(frames["ls_retail"]) == 54  # 57 - 3, and it loaded rather than raising


def test_an_unparseable_amount_raises_rather_than_becoming_nan(tmp_path):
    """`errors="coerce"` turned "1,234.50" into NaN and NaN into a silent total."""
    import pytest

    from gncl.load import load_all

    other = _sample_copy(tmp_path)
    path = other / "ls_retail_transactions.csv"
    lines = path.read_text().splitlines()
    header, first = lines[0].split(","), lines[1].split(",")
    first[header.index("amount")] = '"1,234.50"'
    lines[1] = ",".join(first)
    path.write_text("\n".join(lines) + "\n")

    with pytest.raises(ValueError, match="unparseable amounts"):
        load_all(other)
