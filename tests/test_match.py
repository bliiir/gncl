"""Matching cascade tests.

Targets come from docs/SPECIFICATION.md sections 2 and 5, and from the
the measured acceptance criteria.
"""

import pytest

from gncl.load import load_all
from gncl.match import (
    W_CABIN_WINDOW,
    W_EMAIL,
    W_NAME_AMBIGUOUS,
    W_NAME_UNIQUE,
    W_NAME_WINDOW,
    bookit_hubspot_candidates,
    build,
    closure_violations,
    must_not_link,
)
from gncl.ports import CsvSource


@pytest.fixture(scope="module")
def result():
    return build(source=CsvSource())


@pytest.fixture(scope="module")
def bh_links(result):
    return [link for link in result.links if "hubspot" in link.src or "hubspot" in link.dst]


def _side(link, source):
    for node in (link.src, link.dst):
        if node.startswith(source):
            return node.split(":", 1)[1]
    return None


def test_28_bookit_hubspot_links(bh_links):
    assert len(bh_links) == 28


def test_link_weights_split_21_email_6_unique_1_ambiguous(bh_links):
    weights = sorted(link.weight for link in bh_links)
    assert weights.count(W_EMAIL) == 21
    assert weights.count(W_NAME_UNIQUE) == 6
    assert weights.count(W_NAME_AMBIGUOUS) == 1


def test_residual_records_are_left_unmatched(bh_links, result):
    frames = load_all()
    matched_bk = {_side(link, "bookit") for link in bh_links}
    matched_hs = {_side(link, "hubspot") for link in bh_links}
    assert sorted(set(frames["bookit"]["source_id"]) - matched_bk) == [
        "BK1029",
        "BK1030",
        "BK1031",
        "BK1032",
    ]
    assert sorted(set(frames["hubspot"]["source_id"]) - matched_hs) == [
        "HS229",
        "HS230",
        "HS231",
    ]


def test_matching_does_not_force_cardinality(bh_links):
    """4 BookIT and 3 HubSpot residuals share no evidence and must not be paired."""
    frames = load_all()
    residual_bk = {"BK1029", "BK1030", "BK1031", "BK1032"}
    residual_hs = {"HS229", "HS230", "HS231"}
    for link in bh_links:
        assert not (_side(link, "bookit") in residual_bk and _side(link, "hubspot") in residual_hs)
    assert len(frames["bookit"]) == 32


def test_anna_larsen_resolved_by_construction(bh_links):
    """Spec 2.3. Email locks one pair; the other survives only by exclusion."""
    pairs = {(_side(link, "bookit"), _side(link, "hubspot")): link for link in bh_links}
    assert ("BK1001", "HS201") in pairs
    assert ("BK1021", "HS221") in pairs
    assert pairs[("BK1001", "HS201")].weight == W_EMAIL
    # The by-elimination link must carry low confidence and say why.
    weak = pairs[("BK1021", "HS221")]
    assert weak.weight == W_NAME_AMBIGUOUS
    assert "not unique" in weak.evidence


def test_anna_candidate_edges_are_genuinely_ambiguous():
    """All four Anna cross-edges exist; the matching, not the edge set, resolves it."""
    frames = load_all()
    cands = bookit_hubspot_candidates(frames["bookit"], frames["hubspot"])
    anna = {
        (_side(c, "bookit"), _side(c, "hubspot"))
        for c in cands
        if _side(c, "bookit") in {"BK1001", "BK1021"} and _side(c, "hubspot") in {"HS201", "HS221"}
    }
    assert anna == {
        ("BK1001", "HS201"),
        ("BK1001", "HS221"),
        ("BK1021", "HS201"),
        ("BK1021", "HS221"),
    }


def test_all_57_transactions_attach(result):
    tx = [link for link in result.links if "ls_retail" in link.src]
    assert len(tx) == 57
    assert result.unresolved_transactions == []
    assert sum(1 for link in tx if link.weight == W_CABIN_WINDOW) == 53
    assert sum(1 for link in tx if link.weight == W_NAME_WINDOW) == 4


def test_cabin_6208_splits_by_date(result):
    """Spec 2.2: same cabin, two guests, disjoint windows."""
    tx = {
        link.src.split(":", 1)[1]: _side(link, "bookit")
        for link in result.links
        if "ls_retail" in link.src
    }
    assert tx["TX5006"] == "BK1004"  # Marcus Berg, July
    assert tx["TX5041"] == tx["TX5040"] == tx["TX5039"] == "BK1022"  # Nils Berg, June


def test_fredrik_does_not_attach_to_fredriksen(result):
    """Spec 2.5: 'Fredrik' substring-matches Tone Fredriksen; the window rejects it."""
    tx = {
        link.src.split(":", 1)[1]: _side(link, "bookit")
        for link in result.links
        if "ls_retail" in link.src
    }
    assert tx["TX5036"] == "BK1020"  # Fredrik Lund
    assert tx["TX5036"] != "BK1015"  # Tone Fredriksen


def test_name_conflicts_are_exactly_beth_and_andy(result):
    """Spec 5: the only work for the model is 2 guests across 4 transactions."""
    assert len(result.name_conflicts) == 4
    assert {c["booking_ref"] for c in result.name_conflicts} == {"BK1013", "BK1018"}
    assert {c["captured"] for c in result.name_conflicts} == {"Beth", "Andy"}
    for c in result.name_conflicts:
        assert 0.55 <= c["ratio"] <= 0.70, "both sit in the band string metrics cannot split"


def test_conflicts_are_flagged_not_dropped(result):
    """A name disagreement annotates the link; it does not veto it."""
    conflicted = [link for link in result.links if "pending audit" in link.evidence]
    assert len(conflicted) == 4


def test_35_components_one_per_real_guest(result):
    comps = result.components()
    assert len(comps) == 35  # 32 bookings + 3 HubSpot-only contacts


def test_no_component_holds_two_bookings(result):
    """Transitive closure guard: a guest is at most one booking."""
    for comp in result.components():
        assert sum(1 for n in comp if n.startswith("bookit:")) <= 1


def test_must_not_link_rejects_overlapping_stays_in_different_cabins():
    frames = load_all()
    bk = frames["bookit"].set_index("source_id")
    # Overlapping stays, different cabins: cannot be one person.
    a, b = bk.loc["BK1021"], bk.loc["BK1015"]  # Jun 11-16 cab 4022, Jun 9-16 cab 2088
    assert a["cabin"] != b["cabin"]
    assert must_not_link(a, b)
    # Non-overlapping stays in different cabins are not constrained.
    assert not must_not_link(bk.loc["BK1001"], bk.loc["BK1011"])  # Jul 11-14 vs Jul 21-28
    # Same cabin at different times is the 6208 case and must stay linkable.
    assert not must_not_link(bk.loc["BK1004"], bk.loc["BK1022"])


def test_closure_invariant_holds_on_this_dataset(result):
    assert result.closure_violations == []


def test_closure_invariant_reports_a_contaminated_component():
    """The guard is only worth keeping if it can fire. Contaminate the closure.

    `test_no_component_holds_two_bookings` proves the cascade never builds such a
    component today, which is exactly why the check has to be exercised against
    one that is built by hand.
    """
    frames = load_all()
    result = build(frames)
    contaminated = result.graph.copy()
    # Jun 11-16 in cabin 4022 and Jun 9-16 in cabin 2088: one person, two cabins.
    contaminated.add_edge("bookit:BK1021", "bookit:BK1015", weight=0.9)

    violations = closure_violations(contaminated, frames)

    assert len(violations) == 1
    assert {violations[0]["left"], violations[0]["right"]} == {
        "bookit:BK1021",
        "bookit:BK1015",
    }
    assert "4022" in violations[0]["reason"] and "2088" in violations[0]["reason"]


def test_every_link_carries_evidence(result):
    for link in result.links:
        assert link.evidence.strip()
        assert link.method in {"deterministic", "rule_fuzzy"}


def test_boundary_transactions_over_all_attachments(result):
    """Spec 2.2, counted over every attachment rather than cabin matches only.

    16 land on a boundary: 10 check-in, 6 check-out. The 2 extra against a
    cabin-only count are TX5046 and TX5036, no-cabin rows resolved by the name
    fallback. A half-open [checkin, checkout) drops the 6 on check-out day.
    """
    frames = load_all()
    bk = {r["source_id"]: r for _, r in frames["bookit"].iterrows()}
    ls = {r["source_id"]: r for _, r in frames["ls_retail"].iterrows()}
    on_checkin = on_checkout = 0
    for link in result.links:
        if "ls_retail" not in link.src:
            continue
        t, b = ls[link.src.split(":", 1)[1]], bk[_side(link, "bookit")]
        on_checkin += t["txn_date"] == b["checkin"]
        on_checkout += t["txn_date"] == b["checkout"]
    assert (on_checkin, on_checkout) == (10, 6)


def test_residual_max_cross_similarity(result):
    """Backs the 0.47 figure quoted in the README, which nothing asserted."""
    from difflib import SequenceMatcher

    frames = load_all()
    rb = {"BK1029", "BK1030", "BK1031", "BK1032"}
    rh = {"HS229", "HS230", "HS231"}
    bk = [r for _, r in frames["bookit"].iterrows() if r["source_id"] in rb]
    hs = [r for _, r in frames["hubspot"].iterrows() if r["source_id"] in rh]
    best = max(
        SequenceMatcher(None, b["name_norm"], h["name_norm"]).ratio() for b in bk for h in hs
    )
    assert round(best, 2) == 0.47


def test_name_window_fallback_is_not_labelled_deterministic(result):
    """A first name plus a date range is not a deterministic identification."""
    fallback = [link for link in result.links if link.weight == W_NAME_WINDOW]
    assert len(fallback) == 4
    assert {link.method for link in fallback} == {"rule_fuzzy"}
