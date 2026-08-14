"""The output shape: one table, widening left to right.

One file, not two. The accept decision is a `review` column, not a choice of
file -- splitting rows across `golden_guests.csv` and `review_queue.csv` encoded
it in a filename, which analyses badly. A minimal/full split had the same
problem one level up: two files holding the same 35 rows, so every consumer had
to know which one it wanted, and a column added to one silently did not appear
in the other. The narrow table is the first 13 columns of the wide one.

Columns are ordered so a reader can stop at any block boundary and have a
coherent table:

1.  **identity and decision** (13) -- who the guest is and what was decided.
    Read alone, this is the whole answer.
2.  **evidence** (1) -- the readable reason, next to the decision it explains.
3.  **why that decision** (4) -- name provenance and the audit outcome.
4.  **what the confidence is made of** (4) -- `match_confidence` is the *minimum*
    of these per-link weights, so they sit together.
5.  **activity** (2) -- facts about the guest rather than about the match.
6.  **`sources`** (1) -- names which of the blocks below carry values, so it
    reads as an index to them.
7.  **raw source columns** (12) -- grouped by system in the order `sources`
    names them; name and email lead each block, mirroring the head.

`bookit_*` and `hubspot_*` are 1:1 with a guest and widen directly.
`ls_retail_*` is 0 to 3 transactions, pipe-joined in transaction order to match
the `transaction_ids` convention.

Two kinds of source column are dropped. `DERIVED` ones are ours, not the source
system's, and a second `cabin` beside `cabin_number` invites reading the wrong
one. `COPIES` are source columns the resolved column reproduces exactly -- the
join keys, and the four BookIT fields no other system supplies. A test asserts
every one of them still matches on every row, so a dataset where they diverge
fails loudly instead of quietly serving one of two disagreeing values.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from gncl.match import MatchResult, node_id
from gncl.output import SINGLE_SOURCE, GuestRecord

# Identity plus the decision: the answer, and the only block most readers need.
# The source ids stay because the case asks for "one row per real guest, with
# the corresponding IDs from each source" -- without them a row cannot be traced
# back to the system it came from, which is the product.
HEAD_COLUMNS = [
    "guest_id",
    "booking_ref",
    "contact_id",
    "transaction_ids",
    "name",
    "email",
    "cabin",
    "stay_start",
    "stay_end",
    "nationality",
    "match_confidence",
    "match_method",
    "review",
]

RESOLUTION_COLUMNS = [
    *HEAD_COLUMNS,
    # the readable reason, beside the decision it explains
    "evidence",
    # why that decision
    "name_source",
    "audit",
    "audit_confidence",
    "audit_model",
    # what match_confidence is the minimum of
    "crm_link_method",
    "crm_link_weight",
    "txn_weight_min",
    "txn_weight_max",
    # the guest rather than the match
    "transaction_count",
    "total_spend",
    # which source blocks below carry values
    "sources",
]


def review_band(confidence: float, threshold: float, method: str = "") -> str:
    """The accept decision, derived once and shared by both shapes.

    A row below the threshold is flagged, not moved: the same guest appears in
    both files either way, and changing the threshold rewrites one column rather
    than redistributing rows between files.

    **A single-source guest is accepted.** Its 0.0 confidence is the absence of
    a link, not doubt about one, and the queue is for adjudicating joins: asked
    to review G-HS229 a person would be second-guessing HubSpot's own record,
    with nothing to compare it against. Scoring it as though a match had been
    attempted and failed put 5 of 6 flagged rows in the queue and overstated the
    doubt. `match_method` carries what is true of the row instead.
    """
    if method == SINGLE_SOURCE:
        return "accepted"
    return "accepted" if confidence >= threshold else "review"


DERIVED = frozenset(
    {
        "source",
        "source_id",
        "name_norm",
        "name_keys",
        "email_norm",
        "phone_norm",
        "cabin",
        "checkin",
        "checkout",
        "last_contacted",
        "txn_date",
        "amount_num",
    }
)

KEYS = {"bookit": "booking_ref", "hubspot": "contact_id", "ls_retail": "transaction_id"}

# Block order for the source columns, matching how `sources` spells a guest out
# ("bookit+hubspot+ls_retail"): bookings are the spine, the CRM is the identity
# beside it, the POS is what happened onboard.
SOURCE_ORDER = ("bookit", "hubspot", "ls_retail")

# Source columns the resolved column already reproduces, exactly, on every row.
# The three join keys are `booking_ref`, `contact_id` and `transaction_ids`
# under another name. The four BookIT fields are copies because no other system
# supplies cabin, stay dates or nationality, so the resolved value can only have
# come from there. Verified per row by a test rather than assumed -- a source
# that starts disagreeing must not be hidden behind the pipeline's own value.
COPIES = {
    "bookit": {"booking_ref", "cabin_number", "checkin_date", "checkout_date", "nationality"},
    "hubspot": {"contact_id"},
    "ls_retail": {"transaction_id"},
}

# Read order inside a source block: who, then how to reach them, then the rest
# in the order the source declares. Keyword-matched rather than listed by name,
# so a source gaining a column still lands somewhere sensible on its own.
LEAD = ("name", "email")


def _lead_rank(column: str) -> int:
    return next((i for i, word in enumerate(LEAD) if word in column), len(LEAD))


def _source_columns(frames: dict[str, pd.DataFrame]) -> dict[str, list[str]]:
    """Per source, the columns that survive, in reading order.

    `sorted` is stable, so anything past the lead keeps the source's own order.
    """
    return {
        name: sorted(
            (c for c in df.columns if c not in DERIVED and c not in COPIES.get(name, set())),
            key=_lead_rank,
        )
        for name, df in frames.items()
    }


def _indexed(frames: dict[str, pd.DataFrame]) -> dict[str, dict[str, dict]]:
    """Each source keyed by its own id, as plain dicts. 120 rows; no index needed."""
    return {
        name: {str(row[KEYS[name]]): row for row in df.to_dict("records")}
        for name, df in frames.items()
        if name in KEYS
    }


def _edges(result: MatchResult) -> dict[frozenset[str], tuple[float, str]]:
    """Undirected lookup. `Link` has a direction; the graph does not."""
    return {frozenset((link.src, link.dst)): (link.weight, link.method) for link in result.links}


def _nodes(rec: GuestRecord) -> tuple[str, str, list[str]]:
    """Rebuild this guest's node ids from the columns that already carry them.

    Cannot drift from the CSV, because it is derived from the CSV.
    """
    booking = node_id("bookit", rec.booking_ref) if rec.booking_ref else ""
    crm = node_id("hubspot", rec.contact_id) if rec.contact_id else ""
    txns = [node_id("ls_retail", t) for t in rec.transaction_ids.split("|") if t]
    return booking, crm, txns


def columns(frames: dict[str, pd.DataFrame]) -> list[str]:
    """The one column order, head first and source blocks last.

    Blocks follow `SOURCE_ORDER`, which is the order `sources` names them, so
    that column reads as an index to what follows. Iterating `frames` instead
    would order the file by however the loader happened to build its dict.
    """
    cols = list(RESOLUTION_COLUMNS)
    by_source = _source_columns(frames)
    for source in [*SOURCE_ORDER, *(s for s in by_source if s not in SOURCE_ORDER)]:
        cols += [f"{source}_{c}" for c in by_source.get(source, [])]
    return cols


def joined_frame(
    guests: list[GuestRecord],
    result: MatchResult,
    frames: dict[str, pd.DataFrame],
    threshold: float,
) -> pd.DataFrame:
    edges = _edges(result)
    by_id = _indexed(frames)
    source_cols = _source_columns(frames)
    rows = []
    for rec in guests:
        booking, crm, txns = _nodes(rec)
        # Both endpoints must exist. A one-element frozenset is a different key
        # that would collide across every guest missing the same side.
        crm_weight, crm_method = (
            edges.get(frozenset((booking, crm)), (None, "")) if booking and crm else (None, "")
        )
        txn_weights = [edges[key][0] for t in txns if (key := frozenset((booking, t))) in edges]
        row = {
            "guest_id": rec.guest_id,
            "booking_ref": rec.booking_ref,
            "contact_id": rec.contact_id,
            "transaction_ids": rec.transaction_ids,
            "transaction_count": rec.transaction_count,
            "name": rec.name,
            "email": rec.email,
            "cabin": rec.cabin,
            "stay_start": rec.stay_start,
            "stay_end": rec.stay_end,
            "nationality": rec.nationality,
            "total_spend": rec.total_spend,
            "sources": rec.sources,
            "match_confidence": rec.match_confidence,
            "crm_link_weight": crm_weight,
            "txn_weight_min": min(txn_weights) if txn_weights else None,
            "txn_weight_max": max(txn_weights) if txn_weights else None,
            "audit_confidence": rec.audit_confidence,
            "review": review_band(rec.match_confidence, threshold, rec.match_method),
            "match_method": rec.match_method,
            "crm_link_method": crm_method,
            "audit": rec.audit or "none",
            "audit_model": rec.audit_model,
            "name_source": rec.name_source,
            "evidence": rec.evidence,
        }
        # 1:1 sources widen directly; absent ones stay blank rather than 0.
        for source, key in (("bookit", rec.booking_ref), ("hubspot", rec.contact_id)):
            src = by_id.get(source, {}).get(key, {})
            for col in source_cols.get(source, []):
                row[f"{source}_{col}"] = src.get(col)
        # 1:N: pipe-joined in transaction order, like transaction_ids itself.
        ids = [t for t in rec.transaction_ids.split("|") if t]
        for col in source_cols.get("ls_retail", []):
            values = [by_id["ls_retail"][i].get(col) for i in ids if i in by_id["ls_retail"]]
            row[f"ls_retail_{col}"] = "|".join("" if v is None else str(v) for v in values)
        rows.append(row)
    return pd.DataFrame(rows)[columns(frames)]


GUESTS_FILE = "guests.csv"


def write_outputs(
    guests: list[GuestRecord],
    result: MatchResult,
    frames: dict[str, pd.DataFrame],
    threshold: float,
    outdir: Path,
) -> tuple[Path, pd.DataFrame]:
    """Write the one table. Its first 13 columns are the answer on their own."""
    outdir.mkdir(parents=True, exist_ok=True)
    frame = joined_frame(guests, result, frames, threshold)
    path = outdir / GUESTS_FILE
    frame.to_csv(path, index=False)
    return path, frame
