"""The resolution graph, drawn.

Small multiples, not a hairball. The graph is 120 nodes in 35 components and
none is bigger than five, so a single force-directed blob would hide the only
thing worth seeing: what evidence holds each guest together, and which guest
rests on one weak edge.

Layout is semantic rather than computed. Edges only ever run BookIT to HubSpot
and BookIT to LS Retail, so the booking sits on the left and everything it
links to sits on the right. That also makes the drawing deterministic: same
input, same bytes, which a spring layout with a random seed would not give.

Colour repeats the page: teal for a deterministic link, amber for rule/fuzzy,
and the weight is written on the edge because the number is the point.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from gncl.join import review_band
from gncl.match import MatchResult, node_id
from gncl.output import GuestRecord

INK = "#1D2A2E"
MUTED = "#8A9599"
LINE = "#DEDDD3"
SAND = "#EEEEE6"
TEAL = "#2C615F"
AMBER = "#B8860B"
RED = "#A81212"

SOURCE_FILL = {"bookit": "#E7EFEE", "hubspot": "#F3F0E4", "ls_retail": "#EFEFEA"}
SOURCE_EDGE = {"bookit": "#9CC0BE", "hubspot": "#D9C08A", "ls_retail": "#D3D2C7"}
SOURCE_LABEL = {"bookit": "BookIT", "hubspot": "HubSpot", "ls_retail": "LS Retail"}

PANEL_W, PANEL_H = 250, 132
COLUMNS = 5
PAD = 24
NODE_W, NODE_H = 78, 24


@dataclass(frozen=True)
class Placed:
    """A node and where it landed, in panel-local coordinates."""

    node: str
    source: str
    label: str
    x: float
    y: float


def _split(nodes: list[str]) -> tuple[list[str], list[str], list[str]]:
    def of(prefix: str) -> list[str]:
        return sorted(n for n in nodes if n.startswith(f"{prefix}:"))

    return of("bookit"), of("hubspot"), of("ls_retail")


def _place(nodes: list[str]) -> list[Placed]:
    """Booking on the left, everything it links to on the right.

    A component with no booking is a lone HubSpot contact, so it takes the left
    position itself rather than leaving a gap where the booking would be.
    """
    bookit, hubspot, retail = _split(nodes)
    left = bookit or hubspot or retail
    right = [n for n in (*hubspot, *retail) if n not in left[:1]]
    placed = []
    if left:
        placed.append(_placed(left[0], 18, PANEL_H / 2 + 6))
    step = 30
    top = PANEL_H / 2 + 6 - (len(right) - 1) * step / 2
    for i, node in enumerate(right):
        placed.append(_placed(node, PANEL_W - NODE_W - 18, top + i * step))
    return placed


def _placed(node: str, x: float, y: float) -> Placed:
    source, ident = node.split(":", 1)
    return Placed(node=node, source=source, label=ident, x=x, y=y)


def _node_svg(p: Placed) -> str:
    return (
        f'<rect x="{p.x:.1f}" y="{p.y - NODE_H / 2:.1f}" width="{NODE_W}" height="{NODE_H}"'
        f' rx="4" fill="{SOURCE_FILL[p.source]}" stroke="{SOURCE_EDGE[p.source]}"/>'
        f'<text x="{p.x + NODE_W / 2:.1f}" y="{p.y + 4:.1f}" text-anchor="middle"'
        f' font-size="11" font-family="ui-monospace, SFMono-Regular, Menlo, monospace"'
        f' fill="{INK}">{html.escape(p.label)}</text>'
    )


def _edge_svg(a: Placed, b: Placed, weight: float, method: str) -> str:
    x1, y1 = a.x + NODE_W, a.y
    x2, y2 = b.x, b.y
    colour = TEAL if method == "deterministic" else AMBER
    width = 1.0 + weight * 1.6
    # Not the midpoint: edges fan out from one booking, so midpoints crowd near
    # the shared end. Two thirds along, they spread with their targets.
    mid_x, mid_y = x1 + (x2 - x1) * 0.66, y1 + (y2 - y1) * 0.66
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"'
        f' stroke="{colour}" stroke-width="{width:.1f}"/>'
        f'<rect x="{mid_x - 15:.1f}" y="{mid_y - 8:.1f}" width="30" height="14" rx="7"'
        f' fill="#FFFFFF" stroke="{LINE}"/>'
        f'<text x="{mid_x:.1f}" y="{mid_y + 2:.1f}" text-anchor="middle" font-size="9.5"'
        f' font-family="ui-monospace, SFMono-Regular, Menlo, monospace"'
        f' fill="{colour}">{weight:.2f}</text>'
    )


def _panel(rec: GuestRecord, nodes: list[str], result: MatchResult, threshold: float) -> str:
    placed = _place(sorted(nodes))
    by_node = {p.node: p for p in placed}
    band = review_band(rec.match_confidence, threshold, rec.match_method)

    parts = []
    for src, dst, data in result.graph.edges(sorted(nodes), data=True):
        a, b = by_node.get(src), by_node.get(dst)
        if a is None or b is None:
            continue
        # Edges are stored undirected; draw from whichever end is on the left.
        if a.x > b.x:
            a, b = b, a
        parts.append(_edge_svg(a, b, data["weight"], data["method"]))
    parts += [_node_svg(p) for p in placed]

    flag = f'<circle cx="{PANEL_W - 14}" cy="16" r="4" fill="{RED}"/>' if band == "review" else ""
    name = html.escape(rec.name or "")
    return (
        f'<rect x="0.5" y="0.5" width="{PANEL_W - 1}" height="{PANEL_H - 1}" rx="5"'
        f' fill="#FFFFFF" stroke="{LINE}"/>'
        f'<text x="12" y="19" font-size="11.5" font-weight="600"'
        f' font-family="ui-monospace, SFMono-Regular, Menlo, monospace"'
        f' fill="{INK}">{html.escape(rec.guest_id)}</text>'
        f'<text x="12" y="34" font-size="11" fill="{MUTED}">{name}</text>'
        f'<text x="12" y="{PANEL_H - 10}" font-size="10" fill="{MUTED}">'
        f"{html.escape(rec.match_method)} &#183; {rec.match_confidence:.2f}</text>"
        f"{flag}{''.join(parts)}"
    )


def panel_id(guest_id: str) -> str:
    """The symbol id a page uses to point at one guest's panel."""
    return f"panel-{guest_id}"


def _nodes_of(rec: GuestRecord) -> list[str]:
    nodes = []
    if rec.booking_ref:
        nodes.append(node_id("bookit", rec.booking_ref))
    if rec.contact_id:
        nodes.append(node_id("hubspot", rec.contact_id))
    nodes += [node_id("ls_retail", t) for t in rec.transaction_ids.split("|") if t]
    return nodes


def _legend(y: float, records: int, links: int, guests: int) -> str:
    """Counts come from what was drawn, so a filtered render cannot overclaim."""
    items = [
        (TEAL, "deterministic link"),
        (AMBER, "rule / fuzzy link"),
        (RED, "flagged for review"),
    ]
    counted = (
        f"{records} records &#183; {links} evidence links &#183; "
        f"{guests} resolved guest{'' if guests == 1 else 's'}"
    )
    out = [f'<text x="{PAD}" y="{y}" font-size="11" fill="{MUTED}">{counted}</text>']
    x = PAD + 300
    for colour, label in items:
        out.append(
            f'<line x1="{x}" y1="{y - 4}" x2="{x + 18}" y2="{y - 4}"'
            f' stroke="{colour}" stroke-width="2.5"/>'
            f'<text x="{x + 24}" y="{y}" font-size="11" fill="{MUTED}">{label}</text>'
        )
        x += 150
    return "".join(out)


def render(
    result: MatchResult,
    guests: list[GuestRecord],
    threshold: float = 0.80,
    columns: int = COLUMNS,
) -> str:
    """One panel per resolved guest, in guest_id order."""
    ordered = sorted(guests, key=lambda g: g.guest_id)
    rows = (len(ordered) + columns - 1) // columns
    width = PAD * 2 + columns * PANEL_W + (columns - 1) * 12
    height = PAD * 2 + 52 + rows * (PANEL_H + 12)

    # Panels are symbols, placed by reference. The poster reads the same either
    # way, and it makes the file a sprite sheet: the browser page inlines this
    # `defs` block and points one guest's row at one symbol, rather than the
    # page having to re-run the pipeline to draw anything.
    symbols, uses, records, links = [], [], 0, 0
    for i, rec in enumerate(ordered):
        nodes = _nodes_of(rec)
        records += len(nodes)
        links += result.graph.subgraph(nodes).number_of_edges()
        symbols.append(
            f'<symbol id="{panel_id(rec.guest_id)}" viewBox="0 0 {PANEL_W} {PANEL_H}">'
            f"{_panel(rec, nodes, result, threshold)}</symbol>"
        )
        x = PAD + (i % columns) * (PANEL_W + 12)
        y = PAD + 52 + (i // columns) * (PANEL_H + 12)
        uses.append(
            f'<use href="#{panel_id(rec.guest_id)}" x="{x}" y="{y}"'
            f' width="{PANEL_W}" height="{PANEL_H}"/>'
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}"'
        f' width="{width}" height="{height}" font-family="system-ui, -apple-system, sans-serif">'
        f"<defs>{''.join(symbols)}</defs>"
        f'<rect width="{width}" height="{height}" fill="{SAND}"/>'
        f'<text x="{PAD}" y="{PAD + 18}" font-size="16" font-weight="600" fill="{INK}">'
        f"Resolution graph</text>"
        f"<g>{_legend(PAD + 40, records, links, len(ordered))}</g>"
        f"{''.join(uses)}"
        "</svg>"
    )
