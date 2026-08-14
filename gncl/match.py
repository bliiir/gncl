"""Matching cascade (L1-L4), expressed as a graph.

Records are nodes, evidence links are weighted edges, and a resolved guest is a
connected component. The BookIT<->HubSpot layer is a single max-weight bipartite
matching rather than a greedy loop: greedy gets the two Anna Larsens right only
because email happens to be processed first, whereas matching gets it right by
construction and stays right if input order changes or a third Anna appears.

Transactions attach separately. They are many-to-one (a guest has several), so
they are not part of the one-to-one matching.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import networkx as nx
import pandas as pd

from gncl.normalize import Name, in_stay_window, repaired_name

# Confidence per evidence type. Deterministic links sit at the top; a link that
# survives only because everything else was claimed sits near the floor.
W_EMAIL = 0.98
W_CABIN_WINDOW = 0.95
W_NAME_UNIQUE = 0.90
W_NAME_WINDOW = 0.80
W_NAME_AMBIGUOUS = 0.55


@dataclass
class Link:
    src: str
    dst: str
    weight: float
    method: str
    evidence: str


@dataclass
class MatchResult:
    graph: nx.Graph
    links: list[Link]
    name_conflicts: list[dict] = field(default_factory=list)
    unresolved_transactions: list[str] = field(default_factory=list)
    closure_violations: list[dict] = field(default_factory=list)

    def components(self) -> list[set[str]]:
        return list(nx.connected_components(self.graph))


def node_id(source: str, source_id: str) -> str:
    return f"{source}:{source_id}"


def _repaired(df: pd.DataFrame) -> dict[str, str]:
    return {r["source_id"]: repaired_name(r) for _, r in df.iterrows()}


def must_not_link(a: pd.Series, b: pd.Series) -> bool:
    """Two bookings overlapping in time in different cabins cannot be one person.

    The predicate behind the closure invariant checked in `build()`. It is a
    property of a finished component, not of a candidate edge: two records can
    each be a defensible link and still be impossible together once the closure
    puts them in the same guest.
    """
    for key in ("checkin", "checkout", "cabin"):
        if key not in a or key not in b:
            return False
    if not (a["checkin"] and a["checkout"] and b["checkin"] and b["checkout"]):
        return False
    overlaps = a["checkin"] <= b["checkout"] and b["checkin"] <= a["checkout"]
    return bool(overlaps and a["cabin"] and b["cabin"] and a["cabin"] != b["cabin"])


def bookit_hubspot_candidates(bk: pd.DataFrame, hs: pd.DataFrame) -> list[Link]:
    """All plausible BookIT<->HubSpot edges, weighted. The matching picks among them."""
    bk_names, hs_names = _repaired(bk), _repaired(hs)
    bk_by_name: dict[str, list[str]] = defaultdict(list)
    hs_by_name: dict[str, list[str]] = defaultdict(list)
    for sid, n in bk_names.items():
        bk_by_name[n].append(sid)
    for sid, n in hs_names.items():
        hs_by_name[n].append(sid)

    links: list[Link] = []
    for _, b in bk.iterrows():
        for _, h in hs.iterrows():
            src, dst = node_id("bookit", b["source_id"]), node_id("hubspot", h["source_id"])
            if b["email_norm"] and b["email_norm"] == h["email_norm"]:
                links.append(
                    Link(src, dst, W_EMAIL, "deterministic", f"exact email {b['email_norm']}")
                )
                continue
            bn, hn = bk_names[b["source_id"]], hs_names[h["source_id"]]
            if not bn or bn != hn:
                continue
            ambiguous = len(bk_by_name[bn]) > 1 or len(hs_by_name[hn]) > 1
            if ambiguous:
                links.append(
                    Link(
                        src,
                        dst,
                        W_NAME_AMBIGUOUS,
                        "rule_fuzzy",
                        f"name '{bn}' matches but is not unique "
                        f"({len(bk_by_name[bn])} BookIT, {len(hs_by_name[hn])} HubSpot); "
                        "link rests on every stronger pairing being taken",
                    )
                )
            else:
                note = "" if bn == b["name_norm"] else " (name repaired from email local part)"
                links.append(
                    Link(src, dst, W_NAME_UNIQUE, "rule_fuzzy", f"unique name '{bn}'{note}")
                )
    return links


def resolve_bookit_hubspot(bk: pd.DataFrame, hs: pd.DataFrame) -> list[Link]:
    """Max-weight matching over the candidate edges.

    `maxcardinality=False` on purpose. Forcing maximum cardinality would pair the
    leftover BookIT and HubSpot records with each other despite no shared
    evidence, which is precisely the failure the case is testing for.
    """
    candidates = bookit_hubspot_candidates(bk, hs)
    g = nx.Graph()
    by_pair = {}
    # Every candidate goes in. A floor was specified at 0.50, but the weakest
    # weight this can emit is W_NAME_AMBIGUOUS at 0.55, so it never rejected an
    # edge -- a guard that cannot fire reads as protection the graph does not
    # have. Adding a weight below 0.55 means deciding the floor then, against a
    # real case, rather than inheriting a number chosen for a different one.
    for link in candidates:
        g.add_edge(link.src, link.dst, weight=link.weight)
        by_pair[frozenset((link.src, link.dst))] = link
    chosen = nx.max_weight_matching(g, maxcardinality=False)
    return [by_pair[frozenset(pair)] for pair in chosen]


def attach_transactions(
    bk: pd.DataFrame, ls: pd.DataFrame
) -> tuple[list[Link], list[dict], list[str]]:
    """Cabin + closed stay-window, with a name + window fallback.

    The fallback fires when no cabin was recorded *or* when a recorded cabin
    matches no booking window. Only the first case occurs in this dataset.
    """
    links: list[Link] = []
    conflicts: list[dict] = []
    unresolved: list[str] = []
    bk_names = _repaired(bk)

    for _, t in ls.iterrows():
        tx = node_id("ls_retail", t["source_id"])
        hits = []
        if t["cabin"]:
            hits = [
                b
                for _, b in bk[bk["cabin"] == t["cabin"]].iterrows()
                if in_stay_window(t["txn_date"], b["checkin"], b["checkout"])
            ]
            basis, weight, method = "cabin + stay window", W_CABIN_WINDOW, "deterministic"
        if not t["cabin"] or not hits:
            captured = t["name_norm"]
            hits = [
                b
                for _, b in bk.iterrows()
                if captured
                and Name(bk_names[b["source_id"]]).first == captured
                and in_stay_window(t["txn_date"], b["checkin"], b["checkout"])
            ]
            # A first name plus a date range is not a deterministic identification.
            basis, weight, method = (
                "first name + stay window (no cabin recorded)",
                W_NAME_WINDOW,
                "rule_fuzzy",
            )

        if len(hits) != 1:
            unresolved.append(t["source_id"])
            continue

        b = hits[0]
        captured_first = t["name_norm"]
        booking_first = Name(bk_names[b["source_id"]]).first
        evidence = (
            f"{basis}: cabin {t['cabin'] or '-'} on {t['txn_date']} "
            f"falls in {b['checkin']}..{b['checkout']}"
        )
        if captured_first and booking_first and captured_first != booking_first:
            ratio = SequenceMatcher(None, captured_first, booking_first).ratio()
            conflicts.append(
                {
                    "transaction_id": t["source_id"],
                    "booking_ref": b["source_id"],
                    "captured": t["guest_name_captured"],
                    "booking_name": b["guest_name"],
                    "ratio": round(ratio, 2),
                    "basis": basis,
                }
            )
            evidence += (
                f"; captured name '{t['guest_name_captured']}' disagrees with "
                f"'{b['guest_name']}' (ratio {ratio:.2f}) - pending audit"
            )
        links.append(Link(tx, node_id("bookit", b["source_id"]), weight, method, evidence))
    return links, conflicts, unresolved


def closure_violations(g: nx.Graph, frames: dict[str, pd.DataFrame]) -> list[dict]:
    """Every pair inside a component that `must_not_link` says cannot be one guest.

    Reported, not repaired. Which of the two edges to cut is a judgement the
    cascade has not earned -- surfacing the contradiction is honest, silently
    picking a survivor is not.

    On this dataset it finds nothing, and the reason is worth stating precisely
    because my first one was wrong. I wrote that HubSpot carrying no cabin and no
    dates is what keeps it quiet. That is not it. The BookIT<->HubSpot layer is a
    1:1 matching and transactions attach as leaves, so no component holds two
    records that both carry a cabin and a stay -- measured, every component holds
    at most one BookIT node (32 hold one, the 3 CRM-only leads hold none). The
    guard is unreachable through the shape of the
    graph, not through a missing column, and it becomes reachable the moment a
    source arrives that can put two stays under one guest.
    """
    rows: dict[str, pd.Series] = {}
    for src_name, df in frames.items():
        for _, r in df.iterrows():
            rows[node_id(src_name, r["source_id"])] = r

    violations: list[dict] = []
    for component in nx.connected_components(g):
        members = sorted(n for n in component if n in rows)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                if must_not_link(rows[a], rows[b]):
                    violations.append(
                        {
                            "left": a,
                            "right": b,
                            "reason": (
                                f"cabin {rows[a]['cabin']} ({rows[a]['checkin']}.."
                                f"{rows[a]['checkout']}) overlaps cabin {rows[b]['cabin']} "
                                f"({rows[b]['checkin']}..{rows[b]['checkout']})"
                            ),
                        }
                    )
    return violations


def build(frames: dict[str, pd.DataFrame] | None = None, source=None) -> MatchResult:
    """Run the cascade over already-loaded frames, or over a `GuestSource`.

    No concrete default: a core module that falls back to reading three named
    CSVs from a sibling directory has a hidden dependency, not a seam.
    """
    if frames is None:
        if source is None:
            raise ValueError("build() needs frames or a GuestSource; see gncl.ports")
        frames = source.frames()
    bk, hs, ls = frames["bookit"], frames["hubspot"], frames["ls_retail"]

    links = resolve_bookit_hubspot(bk, hs)
    tx_links, conflicts, unresolved = attach_transactions(bk, ls)
    links += tx_links

    g = nx.Graph()
    for src_name, df in (("bookit", bk), ("hubspot", hs), ("ls_retail", ls)):
        for _, r in df.iterrows():
            g.add_node(node_id(src_name, r["source_id"]), source=src_name, source_id=r["source_id"])
    for link in links:
        g.add_edge(
            link.src, link.dst, weight=link.weight, method=link.method, evidence=link.evidence
        )
    return MatchResult(
        graph=g,
        links=links,
        name_conflicts=conflicts,
        unresolved_transactions=unresolved,
        closure_violations=closure_violations(g, frames),
    )
