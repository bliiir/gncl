"""Output layer tests: the shape of the deliverable."""

import pandas as pd
import pytest
from click.testing import CliRunner

from gncl.cli import main
from gncl.output import COLUMNS, build_guests, to_dataframe
from gncl.ports import CsvSource


@pytest.fixture(scope="module")
def guests():
    return build_guests(source=CsvSource())


@pytest.fixture(scope="module")
def df(guests):
    return to_dataframe(guests)


def test_one_row_per_real_guest(df):
    assert len(df) == 35
    assert list(df.columns) == COLUMNS
    assert df["guest_id"].is_unique


def test_source_ids_are_never_duplicated(df):
    for col in ("booking_ref", "contact_id"):
        present = df[df[col] != ""][col]
        assert present.is_unique, f"{col} appears in more than one guest"
    assert (df["booking_ref"] != "").sum() == 32
    assert (df["contact_id"] != "").sum() == 31


def test_every_transaction_is_assigned_exactly_once(df):
    ids = [t for row in df["transaction_ids"] if row for t in row.split("|")]
    assert len(ids) == 57
    assert len(set(ids)) == 57


def test_single_source_rows_are_kept_not_dropped(df):
    lone = df[df["match_method"] == "single source"]
    assert set(lone["booking_ref"]) - {""} == {"BK1029", "BK1031"}
    assert set(lone["contact_id"]) - {""} == {"HS229", "HS230", "HS231"}
    assert (lone["match_confidence"] == 0.0).all()


def test_negative_controls_are_never_matched(df):
    """The three HubSpot-only leads must stay unlinked."""
    for contact_id in ("HS229", "HS230", "HS231"):
        row = df[df["contact_id"] == contact_id].iloc[0]
        assert row["booking_ref"] == ""
        assert row["match_method"] == "single source"


def test_anna_larsen_carries_low_confidence(df):
    """Spec 2.3: the by-elimination link must not look certain."""
    weak = df[df["booking_ref"] == "BK1021"].iloc[0]
    strong = df[df["booking_ref"] == "BK1001"].iloc[0]
    assert weak["contact_id"] == "HS221"
    assert strong["contact_id"] == "HS201"
    assert weak["match_confidence"] == 0.55
    assert weak["match_confidence"] < strong["match_confidence"]
    assert "not unique" in weak["evidence"]


def test_confidence_is_the_weakest_link(df):
    """A component is only as trustworthy as its shakiest edge."""
    anna = df[df["booking_ref"] == "BK1021"].iloc[0]
    # Its transactions attach at 0.95, but the HubSpot link is 0.55.
    assert anna["transaction_count"] == 2
    assert anna["match_confidence"] == 0.55


def test_display_names_preserve_diacritics(df):
    names = set(df["name"])
    assert "Freja Sørensen" in names
    assert "Bjørn Kristiansen" in names


def test_repaired_names_are_corrected_and_labelled(df):
    repaired = df[df["name_source"] != "as recorded"]
    assert set(repaired["name"]) == {"Mikael Svensson", "Camilla Strand"}
    assert set(repaired["booking_ref"]) == {"BK1014", "BK1019"}
    assert (repaired["name_source"] == "repaired from email local part").all()


def test_every_row_has_evidence(df):
    assert (df["evidence"].str.strip() != "").all()
    for _, row in df[df["match_method"] == "single source"].iterrows():
        assert "appears in one system only" in row["evidence"]


def test_spend_totals_match_source(df):
    from gncl.load import load_all

    assert df["total_spend"].sum() == pytest.approx(
        load_all()["ls_retail"]["amount_num"].sum(), abs=0.01
    )


def test_guest_ids_are_stable_across_runs():
    a = {g.guest_id for g in build_guests(source=CsvSource())}
    b = {g.guest_id for g in build_guests(source=CsvSource())}
    assert a == b


def test_write_flags_rather_than_splits(tmp_path, guests, match_result, frames):
    """Every guest is in the one file; the threshold sets a column, not a filename."""
    from gncl.join import write_outputs

    path, df = write_outputs(guests, match_result, frames, 0.80, tmp_path)
    assert path.name == "guests.csv"
    assert [p.name for p in tmp_path.glob("*.csv")] == ["guests.csv"], "one table, not two"
    assert len(pd.read_csv(path)) == 35
    flagged = df[df["review"] == "review"]
    assert len(flagged) == 1  # the ambiguous Anna Larsen; a lone record is not a queue item
    assert (flagged["match_confidence"] < 0.80).all()


def test_threshold_is_a_filter_not_a_rerun(tmp_path, guests, match_result, frames):
    """Same scores, different flags -- and the row count never moves."""
    from gncl.join import write_outputs

    _, lo = write_outputs(guests, match_result, frames, 0.50, tmp_path / "lo")
    _, hi = write_outputs(guests, match_result, frames, 0.99, tmp_path / "hi")
    assert lo["match_confidence"].tolist() == hi["match_confidence"].tolist()
    assert len(lo) == len(hi) == 35
    # No threshold flags a lone record: 30 guests have a join to doubt, and at
    # 0.50 every one of those joins clears it.
    assert (lo["review"] == "review").sum() == 0
    assert (hi["review"] == "review").sum() == 30


def test_cli_resolve_runs(tmp_path):
    res = CliRunner().invoke(main, ["resolve", "--out", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "35 guests resolved from 120 records" in res.output
    assert "single source" in res.output
    assert (tmp_path / "guests.csv").exists()


def test_cli_show_prints_evidence(tmp_path):
    runner = CliRunner()
    runner.invoke(main, ["resolve", "--out", str(tmp_path)])
    res = runner.invoke(main, ["show", "G-BK1021", "--out", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "HS221" in res.output
    assert "not unique" in res.output


def test_cli_show_rejects_unknown_guest(tmp_path):
    runner = CliRunner()
    runner.invoke(main, ["resolve", "--out", str(tmp_path)])
    res = runner.invoke(main, ["show", "G-NOPE", "--out", str(tmp_path)])
    assert res.exit_code != 0


def test_confidence_distribution(df):
    """Backs the distribution quoted in the README.

    3 guests sit exactly on the 0.80 accept threshold, so the headline 29/6
    split is knife-edge: `>` instead of `>=` moves 9% of the output.
    """
    counts = df["match_confidence"].value_counts().sort_index().to_dict()
    assert counts == {0.0: 5, 0.55: 1, 0.8: 3, 0.9: 6, 0.95: 20}


def test_method_split(df):
    """Backs the headline block in the README."""
    assert df["match_method"].value_counts().to_dict() == {
        "deterministic": 20,
        "rule_fuzzy": 10,
        "single source": 5,
    }


def test_readme_headline_numbers_match_live_output(tmp_path):
    """The README's headline tables must carry the command's actual numbers.

    They have drifted twice: once showing a stale method split, once omitting
    the pending-audit line that discloses the model did not run. A reviewer
    reads them first, so they are the worst place in the repo to carry a stale
    number. The block was pasted stdout pinned byte for byte until it became a
    table; labels and counts are compared now, formatting is not.
    """
    from pathlib import Path

    # --no-audit so the output does not depend on whether a model is reachable:
    # with the audit on, a machine without Ollama prints a note first.
    runner = CliRunner()
    res = runner.invoke(main, ["resolve", "--out", str(tmp_path), "--no-audit"])
    assert res.exit_code == 0, res.output

    # Indented "<label>  <count>" lines. The name-conflicts line ends in prose
    # rather than a count, so it falls out here: it varies with whether a model
    # is reachable, and pinning it would fail on any machine but this one.
    live = {}
    for line in res.output.splitlines():
        if not line.startswith("  "):
            continue
        label, _, value = line.strip().rpartition(" ")
        if value.isdigit():
            live[label.strip()] = value
    assert live, f"no counts parsed from output:\n{res.output}"

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
    assert res.output.splitlines()[0] in readme, "README headline sentence is stale"

    # Two-column rows with a numeric second cell. Header and separator rows
    # fail that test, as does the file index further down the README.
    #
    # The block ends at the next heading, whatever it is called. Naming one
    # ("## Matching approach") made this test fail when that section moved to
    # docs/OVERVIEW.md, which is a reshuffle, not a stale number.
    start = readme.index("35 guests resolved")
    end = readme.find("\n## ", start)
    head = readme[start : end if end != -1 else len(readme)]
    quoted = {}
    for line in head.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 2 and cells[1].isdigit():
            quoted[cells[0]] = cells[1]

    assert quoted == live, f"README tables are stale.\nREADME:\n{quoted}\n\nlive:\n{live}"
