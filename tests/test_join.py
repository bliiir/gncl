"""The joined export: one row per guest, every score and category a column.

These assert the *relationships* between the new columns and the ones that
already shipped, because the failure mode of a flattened export is a column that
looks authoritative and was computed from the wrong thing.
"""

import pytest

from gncl.join import COPIES, DERIVED, HEAD_COLUMNS, RESOLUTION_COLUMNS, joined_frame
from gncl.output import SINGLE_SOURCE

THRESHOLD = 0.80


@pytest.fixture(scope="module")
def joined(sample_guests, match_result, frames):
    return joined_frame(sample_guests, match_result, frames, THRESHOLD)


def test_one_row_per_guest_and_every_column_present(joined, sample_guests):
    assert len(joined) == len(sample_guests) == 35
    assert list(joined.columns)[: len(RESOLUTION_COLUMNS)] == RESOLUTION_COLUMNS


def test_band_reproduces_the_headline_split(joined):
    """34/1 is the figure in the README, and it must not be computed twice.

    The two shipped CSVs encode this by which file a row is in. If the band
    column and that split ever disagree, one of them is lying to a reviewer.
    """
    counts = joined["review"].value_counts().to_dict()
    assert counts == {"accepted": 34, "review": 1}
    # A flagged row is a contested *join*: below threshold, and not a lone
    # record. Accepted rows are either above the threshold or single source.
    flagged = joined[joined["review"] == "review"]
    assert (flagged["match_confidence"] < THRESHOLD).all()
    assert (flagged["match_method"] != SINGLE_SOURCE).all()
    accepted = joined[joined["review"] == "accepted"]
    assert (
        (accepted["match_confidence"] >= THRESHOLD) | (accepted["match_method"] == SINGLE_SOURCE)
    ).all()


def test_match_confidence_is_the_minimum_of_the_links_it_summarises(joined):
    """The point of publishing per-link weights: they must explain the score.

    A guest scores its weakest edge. If that stops being true the per-link
    columns are decoration rather than evidence. Contradicted audits are
    excluded because a contradiction caps confidence at 0.50 independently.
    """
    scored = joined[joined["crm_link_weight"].notna() & (joined["audit"] != "contradicted")]
    assert not scored.empty
    expected = scored[["crm_link_weight", "txn_weight_min"]].min(axis=1)
    assert (scored["match_confidence"] - expected).abs().max() < 1e-9


def test_anna_larsen_shows_the_weak_edge_that_holds_her_down(joined):
    """The case's hardest guest, and the reason the review queue exists."""
    row = joined[joined["guest_id"] == "G-BK1021"].iloc[0]
    assert row["match_confidence"] == 0.55
    assert row["crm_link_weight"] == 0.55  # the CRM link is the weak one
    assert row["txn_weight_min"] == 0.95  # her transactions are not
    assert row["review"] == "review"


@pytest.fixture(scope="module")
def joined_with_verdicts(match_result, frames):
    """Guests built with injected verdicts, so this does not need a live model.

    The shared `sample_guests` fixture passes no verdicts, so every audit column
    is empty under it and any assertion about them would skip rather than run.
    """
    from gncl.llm import Verdict
    from gncl.output import build_guests

    verdicts = {
        c["transaction_id"]: Verdict(True, 0.95, "a diminutive", "stub-model")
        for c in match_result.name_conflicts
    }
    guests = build_guests(match_result, frames, verdicts)
    return joined_frame(guests, match_result, frames, THRESHOLD)


def test_audit_confidence_never_overwrites_match_confidence(joined_with_verdicts):
    """The model audits a link; it does not score it.

    Folding the model's number into `match_confidence` would make the headline
    figures depend on whether a model was reachable, which is the bug the audit
    column was introduced to avoid. Here the stub answers 0.95 on links whose
    rule weight is also 0.95, so the assertion is that the two columns are
    computed from different places, not that they differ numerically.
    """
    audited = joined_with_verdicts[joined_with_verdicts["audit"] == "confirmed"]
    assert len(audited) == 2, "the sample raises exactly two name conflicts"
    for _, row in audited.iterrows():
        assert row["audit_confidence"] == 0.95
        assert row["audit_model"] == "stub-model"
        # Unchanged by the audit: still the minimum of the rule-made edges.
        assert row["match_confidence"] == min(row["crm_link_weight"], row["txn_weight_min"])


def test_a_contradicting_verdict_caps_confidence_and_moves_the_band(match_result, frames):
    """The one case where the model does move a number, and it only lowers it."""
    from gncl.llm import Verdict
    from gncl.output import build_guests

    verdicts = {
        c["transaction_id"]: Verdict(False, 0.9, "different people", "stub-model")
        for c in match_result.name_conflicts
    }
    df = joined_frame(build_guests(match_result, frames, verdicts), match_result, frames, THRESHOLD)
    contradicted = df[df["audit"] == "contradicted"]
    assert len(contradicted) == 2
    assert (contradicted["match_confidence"] == 0.50).all()
    assert (contradicted["review"] == "review").all(), "a contradicted link must not stay accepted"


def test_single_source_guests_carry_no_link_weights(joined):
    """Unmatched means no edge. Any weight here would be evidence of a link."""
    lone = joined[joined["match_method"] == SINGLE_SOURCE]
    assert len(lone) == 5
    for col in ("crm_link_weight", "txn_weight_min", "txn_weight_max"):
        assert lone[col].isna().all()
    assert (lone["match_confidence"] == 0.0).all()
    # 0.0 is the absence of a link, not doubt about one, so it is not a queue item.
    assert (lone["review"] == "accepted").all()


def test_audit_column_is_never_blank(joined):
    """`none` rather than empty: a blank cell reads as missing data.

    It is not missing; it means no name conflict was raised for that guest.
    """
    assert joined["audit"].notna().all()
    assert (joined["audit"].str.len() > 0).all()
    assert set(joined["audit"]) <= {"none", "confirmed", "contradicted", "not run"}


def test_every_source_column_is_present_under_its_prefix(joined, frames):
    """A full join means the source columns are all there, minus the two exclusions.

    A source column is dropped only for being pipeline-derived (`DERIVED`) or an
    exact copy of a resolved column (`COPIES`). Anything else must widen, or the
    file is a chosen subset dressed as a join.
    """
    for source in ("bookit", "hubspot", "ls_retail"):
        for col in frames[source].columns:
            if col not in DERIVED and col not in COPIES.get(source, set()):
                assert f"{source}_{col}" in joined.columns, f"{source}.{col} missing"


def test_pipeline_derived_columns_are_excluded(joined):
    """`name_norm` and friends are ours, not the source system's.

    A second `cabin` beside `cabin_number` invites reading the wrong one.
    """
    for source in ("bookit", "hubspot", "ls_retail"):
        for col in DERIVED:
            assert f"{source}_{col}" not in joined.columns


def test_one_to_one_sources_carry_the_actual_source_row(joined, frames):
    """Widened values must be the source's, not a guess reconstructed from it."""
    bookit = {r["booking_ref"]: r for r in frames["bookit"].to_dict("records")}
    row = joined[joined["guest_id"] == "G-BK1013"].iloc[0]
    src = bookit[row["booking_ref"]]
    assert row["bookit_guest_name"] == src["guest_name"]
    assert row["bookit_email"] == src["email"]


def test_transactions_are_pipe_joined_in_transaction_order(joined):
    """1:N cannot widen, so it concatenates -- and must stay aligned with the ids."""
    multi = joined[joined["transaction_count"] > 1]
    assert not multi.empty
    for _, row in multi.iterrows():
        ids = row["transaction_ids"].split("|")
        assert len(ids) == row["transaction_count"]
        assert len(row["ls_retail_amount"].split("|")) == row["transaction_count"]
        assert len(row["ls_retail_transaction_date"].split("|")) == row["transaction_count"]


def test_guests_missing_a_source_have_blank_columns_not_zero(joined):
    """3 guests have no BookIT row and 4 no HubSpot row. Absent is not 0.0."""
    no_booking = joined[joined["booking_ref"] == ""]
    assert len(no_booking) == 3
    assert no_booking["bookit_guest_name"].isna().all()
    no_crm = joined[joined["contact_id"] == ""]
    assert len(no_crm) == 4
    assert no_crm["hubspot_full_name"].isna().all()


def test_the_narrow_table_is_the_first_thirteen_columns(joined):
    """The file opens as the table that used to be `guests_minimal.csv`.

    One file, so this ordering *is* the projection: stop reading at column 13
    and you have the answer with nothing missing and nothing to join.
    """
    assert list(joined.columns)[: len(HEAD_COLUMNS)] == HEAD_COLUMNS
    assert joined.columns[len(HEAD_COLUMNS)] == "evidence", "the reason follows the decision"


def test_the_head_keeps_the_source_ids_the_brief_asks_for(joined):
    """Case requirement 1: one row per guest "with the corresponding IDs from each source"."""
    for col in ("booking_ref", "contact_id", "transaction_ids"):
        assert col in HEAD_COLUMNS


def test_column_blocks_are_ordered_head_reason_scores_then_sources(joined):
    """The order is the argument: decision, why, what it is made of, raw."""
    at = {c: i for i, c in enumerate(joined.columns)}
    assert at["review"] < at["evidence"] < at["name_source"] < at["crm_link_weight"]
    assert at["crm_link_weight"] < at["total_spend"] < at["sources"]
    # `sources` names the blocks that follow it, in the order they follow.
    assert at["sources"] < at["bookit_guest_name"] < at["hubspot_full_name"]
    assert at["hubspot_full_name"] < at["ls_retail_guest_name_captured"]
    # Within a block, who first: the name leads, then the rest as declared.
    assert at["hubspot_full_name"] < at["hubspot_email"] < at["hubspot_phone"]
    assert at["ls_retail_guest_name_captured"] < at["ls_retail_cabin_number"]


COPY_OF = {
    "bookit_booking_ref": "booking_ref",
    "bookit_cabin_number": "cabin",
    "bookit_checkin_date": "stay_start",
    "bookit_checkout_date": "stay_end",
    "bookit_nationality": "nationality",
    "hubspot_contact_id": "contact_id",
    "ls_retail_transaction_id": "transaction_ids",
}


def test_dropped_source_columns_carried_nothing_the_head_does_not(
    monkeypatch, frames, match_result, sample_guests
):
    """The whole justification for dropping them, checked against the data.

    `COPIES` claims these source columns reproduce a resolved column exactly. If
    a dataset ever makes one diverge -- HubSpot starts carrying a cabin, BookIT
    and the resolved nationality disagree -- this fails, and the column comes
    back deliberately instead of the file quietly serving one of two answers.
    """
    import gncl.join as join_mod

    monkeypatch.setattr(join_mod, "COPIES", {})
    wide = join_mod.joined_frame(sample_guests, match_result, frames, 0.80)

    def norm(series):
        """Absent is absent: None, NaN and "" are the same thing here, and a
        float cabin renders as 4021.0 against the source's 4021."""
        filled = series.where(series.notna(), "").astype(str).str.strip()
        return filled.str.replace(r"\.0$", "", regex=True)

    for source_col, resolved_col in COPY_OF.items():
        a, b = norm(wide[source_col]), norm(wide[resolved_col])
        assert (a == b).all(), f"{source_col} is not a copy of {resolved_col}"
        assert (a != "").any(), f"{source_col} is empty; the check proves nothing"


def test_review_flag_and_confidence_never_disagree(frames, match_result, sample_guests):
    """The flag is derived, so a row cannot be flagged and above threshold at once."""
    from gncl.join import joined_frame

    full = joined_frame(sample_guests, match_result, frames, 0.80)
    joins = full[full["match_method"] != SINGLE_SOURCE]
    accepted = joins[joins["review"] == "accepted"]["match_confidence"]
    flagged = joins[joins["review"] == "review"]["match_confidence"]
    assert (accepted >= 0.80).all()
    assert (flagged < 0.80).all()
    assert len(accepted) + len(flagged) == len(joins) == 30
    assert (full[full["match_method"] == SINGLE_SOURCE]["review"] == "accepted").all()
