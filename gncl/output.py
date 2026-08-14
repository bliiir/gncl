"""Guest records and output files (L4).

One row per connected component. Confidence is the *weakest* link holding a
component together, not the strongest: a guest joined by one solid email match
and one shaky name match is only as trustworthy as the shaky one. That is what
makes the `review` flag meaningful.

Components with no links are emitted as `single source` at confidence 0.0 rather
than dropped, because "this guest appears in one system only" is a result. A
component with no links has exactly one node, so the label is a fact about the
data rather than a verdict on the matching: there was nothing to join.

`COLUMNS` and `to_dataframe` are the in-memory view the API serves. The two file
shapes live in `gncl.join`, because minimal is a projection of the full frame
rather than a shape of its own.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from gncl.match import MatchResult, build
from gncl.normalize import normalize_name, repaired_name


def display_name(row, name_col: str) -> tuple[str, str]:
    """Best-evidence name, plus where it came from.

    Keeps the recorded spelling when nothing was corrected, so "Bjorn" retains
    its o-slash and casing. Where the email local part corrected a typo, the
    corrected form wins and says so.
    """
    raw = str(row[name_col] or "").strip()
    repaired = repaired_name(row)
    if not repaired or repaired == normalize_name(raw):
        return raw, "as recorded"
    return repaired.title(), "repaired from email local part"


# One record, no link to anything. Not a failed match -- there was no candidate
# to match against, so nothing here is being second-guessed.
SINGLE_SOURCE = "single source"

METHOD_RANK = {SINGLE_SOURCE: 0, "rule_fuzzy": 1, "deterministic": 2}

COLUMNS = [
    "guest_id",
    "booking_ref",
    "contact_id",
    "transaction_ids",
    "transaction_count",
    "total_spend",
    "name",
    "name_source",
    "email",
    "cabin",
    "stay_start",
    "stay_end",
    "nationality",
    "sources",
    "match_confidence",
    "match_method",
    "audit",
    "evidence",
]


@dataclass
class GuestRecord:
    guest_id: str
    booking_ref: str = ""
    contact_id: str = ""
    transaction_ids: str = ""
    transaction_count: int = 0
    total_spend: float = 0.0
    name: str = ""
    name_source: str = ""
    email: str = ""
    cabin: str = ""
    stay_start: str = ""
    stay_end: str = ""
    nationality: str = ""
    sources: str = ""
    match_confidence: float = 0.0
    match_method: str = SINGLE_SOURCE
    audit: str = ""
    """Outcome of the model audit, kept separate from `match_method`.

    A rule made the link and an audit does not change that. Folding the two
    together would relabel email-locked guests as LLM-matched and make the
    headline figures depend on whether a model happened to be reachable.
    """
    audit_confidence: float | None = None
    """The model's own confidence, not the pipeline's.

    Separate from `match_confidence`, which stays at the rule weight: the model
    audits a link, it does not score it. Kept off `COLUMNS` so the two shipped
    CSVs are unchanged; `gncl/join.py` surfaces it. None means no usable
    verdict, which is not a verdict of zero.
    """
    audit_model: str = ""
    evidence: str = ""


def _populate_fields(
    rec: GuestRecord, index: dict, booking: str | None, contact: str | None
) -> None:
    """Copy source fields onto the record. BookIT wins where both have a value."""
    if booking:
        b = index[booking]
        rec.booking_ref = b["source_id"]
        rec.name, rec.name_source = display_name(b, "guest_name")
        rec.email = b["email_norm"]
        rec.cabin = b["cabin"]
        rec.stay_start = str(b["checkin"] or "")
        rec.stay_end = str(b["checkout"] or "")
        rec.nationality = b["nationality"]
    if contact:
        h = index[contact]
        rec.contact_id = h["source_id"]
        if not rec.name:
            rec.name, rec.name_source = display_name(h, "full_name")
        rec.email = rec.email or h["email_norm"]


def _populate_transactions(rec: GuestRecord, index: dict, txs: list[str]) -> None:
    if not txs:
        return
    rows = [index[t] for t in txs]
    rec.transaction_ids = "|".join(r["source_id"] for r in rows)
    rec.transaction_count = len(rows)
    rec.total_spend = round(sum(float(r["amount_num"]) for r in rows), 2)


def _apply_audit(
    rec: GuestRecord, conflicts: list[dict], verdicts: dict | None, nodes: list[str]
) -> None:
    """Fold any model audit of this component's name conflicts into the record.

    Deduped on the name pair: four transactions raise two distinct questions.
    Only a contradiction moves confidence -- the model agreeing with a rule does
    not make the link an LLM match.
    """
    seen_pairs: set[tuple[str, str]] = set()
    for conflict in conflicts:
        if f"bookit:{conflict['booking_ref']}" not in nodes:
            continue
        pair = (conflict["captured"].casefold(), conflict["booking_name"].casefold())
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        verdict = (verdicts or {}).get(conflict["transaction_id"])
        if verdict is None or not verdict.usable:
            # No answer we can act on. Not the same as the model disagreeing.
            why = verdict.reason if verdict else "audit not run"
            rec.audit = "not run"
            rec.evidence += f" | name conflict unresolved ({why}); link kept, flagged"
        elif verdict.same_person:
            rec.audit = "confirmed"
            rec.audit_confidence, rec.audit_model = verdict.confidence, verdict.model
            rec.evidence += (
                f" | audited by {verdict.model}: '{conflict['captured']}' and "
                f"'{conflict['booking_name']}' are the same person "
                f"(confidence {verdict.confidence:.2f}) - {verdict.reason}"
            )
        else:
            rec.audit = "contradicted"
            rec.audit_confidence, rec.audit_model = verdict.confidence, verdict.model
            rec.match_confidence = min(rec.match_confidence, 0.50)
            rec.evidence += (
                f" | audited by {verdict.model}: name evidence CONTRADICTS this link "
                f"- {verdict.reason}"
            )


def build_guests(
    result: MatchResult | None = None,
    frames: dict | None = None,
    verdicts: dict | None = None,
    source=None,
) -> list[GuestRecord]:
    if frames is None:
        if source is None:
            raise ValueError("build_guests() needs frames or a GuestSource; see gncl.ports")
        frames = source.frames()
    result = result or build(frames)
    index = {
        f"{src}:{r['source_id']}": r
        for src, df in (
            ("bookit", frames["bookit"]),
            ("hubspot", frames["hubspot"]),
            ("ls_retail", frames["ls_retail"]),
        )
        for _, r in df.iterrows()
    }
    edges_by_node: dict[str, list] = {}
    for link in result.links:
        edges_by_node.setdefault(link.src, []).append(link)
        edges_by_node.setdefault(link.dst, []).append(link)

    guests: list[GuestRecord] = []
    for comp in result.components():
        nodes = sorted(comp)
        booking = next((n for n in nodes if n.startswith("bookit:")), None)
        contact = next((n for n in nodes if n.startswith("hubspot:")), None)
        txs = sorted(n for n in nodes if n.startswith("ls_retail:"))

        # Stable under insertion: derived from the record itself, not a counter.
        anchor = booking or contact or (txs[0] if txs else nodes[0])
        rec = GuestRecord(guest_id=f"G-{anchor.split(':', 1)[1]}")

        links = {id(link): link for n in nodes for link in edges_by_node.get(n, [])}.values()
        if links:
            rec.match_confidence = round(min(link.weight for link in links), 2)
            rec.match_method = min(
                (link.method for link in links), key=lambda m: METHOD_RANK.get(m, 0)
            )
            rec.evidence = " | ".join(sorted({link.evidence for link in links}))
        else:
            rec.evidence = (
                "no link to any other source; this guest appears in one system only, "
                "so no join was made and none was forced"
            )

        _populate_fields(rec, index, booking, contact)

        _apply_audit(rec, result.name_conflicts, verdicts, nodes)

        _populate_transactions(rec, index, txs)

        rec.sources = "+".join(
            s
            for s, present in (("bookit", booking), ("hubspot", contact), ("ls_retail", txs))
            if present
        )
        guests.append(rec)

    guests.sort(key=lambda g: (g.booking_ref or "zz", g.contact_id or "", g.guest_id))
    return guests


def to_dataframe(guests: list[GuestRecord]) -> pd.DataFrame:
    return pd.DataFrame([{k: v for k, v in asdict(g).items() if k in COLUMNS} for g in guests])[
        COLUMNS
    ]
