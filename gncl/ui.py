"""Browser presentation of whatever is in the output directory.

The visual design is GNCL's, ported from the brand bundle in `design/` rather
than approximated: palette, four-tier badge scale, stat tiles, sticky first
column, the core/all column toggle, header and footer. The bundle itself is a
component export that needs a 69 KB runtime, fetches the CSV over HTTP and links
Google Fonts. None of that can ship here, so what came across is the design, not
its delivery mechanism.

Self-contained by construction: no CDN, no webfonts, no external anything,
because the vessel profile assumes no egress. The typography holds the design's
three roles -- serif for headings and figures, sans for the interface, mono for
identifiers -- with system stacks, so the page stays offline and ~65 KB rather
than carrying a third of a megabyte of embedded font. Sort, filter and the
column toggle are hand-rolled for the same reason; a grid library is 6x this
repo to page 35 rows.

Spec section 7 binds even at this stage: `single source` is not rendered as an
error. It is a correct outcome, and colouring it as failure contradicts the
central claim of the project. Review-band rows need *a person*, not a fix.
"""

from __future__ import annotations

import base64
import csv
import html
import re
from pathlib import Path

TABLES = [
    (
        "Guests",
        "guests.csv",
        (
            "The decision first, then the evidence and every source column behind it."
            " Sort any column; the filter matches on the whole row, evidence included."
        ),
    ),
]

# Columns worth reading as a decision rather than a value.
BADGE = {"review", "audit", "match_method", "name_source", "crm_link_method"}

# The default view. The design's core set, plus the three source ids it left
# out: "one row per real guest, with the corresponding IDs from each source" is
# the first case requirement, and a page that hides them makes a reviewer press
# a button to check the thing being delivered. Everything else is one click away.
CORE = (
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
    "evidence",
    "transaction_count",
    "total_spend",
    "sources",
)

# Four tiers, from the design's legend: clean, needs a look, blocked, nothing to
# flag. `single source` is amber and never red -- see the note on spec section 7
# above. It is not a failure, but it is the row a reader should notice: one
# system's word for a guest, with nothing to corroborate it. Only a *model
# contradicting a rule* earns the red tier. The badge text carries the meaning;
# colour only reinforces it, so nothing is lost when it cannot be seen. Values
# outside this map render in the neutral tier, which is why `audit` values
# "none" and "not run" need no entry.
TONE = {
    "deterministic": "strong",
    "accepted": "strong",
    "as recorded": "strong",
    "confirmed": "strong",
    "rule_fuzzy": "partial",
    "review": "partial",
    "repaired from email local part": "partial",
    "single source": "partial",
    "contradicted": "blocked",
}

LEGEND = (
    ("strong", "Clean match"),
    ("partial", "Needs a look"),
    ("blocked", "Blocked or conflicting"),
    ("plain", "Nothing to flag"),
)

STYLE = """
:root {
  color-scheme: light;
  --ink:#1D2A2E; --body:#41565A; --mut:#8A9599; --faint:#98A2A5;
  --line:#DEDDD3; --rule:#EBEAE1; --sand:#EEEEE6; --bg:#FFFFFF;
  --teal:#2C615F; --teal-lit:#387A77; --red:#A81212;
  --serif: Georgia, 'Iowan Old Style', 'Times New Roman', serif;
  --sans: system-ui, -apple-system, 'Segoe UI', Helvetica, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, 'DejaVu Sans Mono', monospace;
}
* { box-sizing: border-box; }
body { margin:0; font:14px/1.55 var(--sans); color:var(--ink); background:var(--bg);
       display:flex; flex-direction:column; min-height:100vh; }
.bar { max-width:1600px; margin:0 auto; }
::selection { background:#CFE2E0; color:var(--ink); }

header { background:var(--bg); border-bottom:1px solid var(--line); padding:0 32px; }
header .bar { display:flex; align-items:center; gap:20px; height:74px; }
header svg { height:32px; width:auto; display:block; }
.sep { width:1px; height:32px; background:var(--line); }
h1 { margin:0; font-family:var(--serif); font-weight:500; font-size:17px; }
.systems { margin-left:auto; font-family:var(--mono); font-size:11.5px;
           letter-spacing:.06em; color:var(--mut); }

nav { background:var(--bg); border-bottom:1px solid var(--line); padding:0 32px; }
nav .bar { display:flex; gap:2px; }
nav button { font:500 13.5px var(--sans); border:0; background:none; color:var(--mut);
             padding:13px 16px; cursor:pointer; border-bottom:2px solid transparent; }
nav button:hover { color:var(--ink); }
nav button[aria-selected=true] { color:var(--ink); border-bottom-color:var(--teal-lit); }

main { flex:1; }
section { display:none; padding:26px 32px 56px; }
section[data-active] { display:block; }
section .bar { display:flex; flex-direction:column; gap:16px; }
.intro { display:flex; align-items:flex-end; gap:24px; flex-wrap:wrap; }
h2 { margin:0; font-family:var(--serif); font-weight:600; font-size:21px;
     letter-spacing:-.01em; }
.count { margin:0; max-width:70ch; font-size:13.5px; color:var(--body); }

/* Stat tiles: the headline split, in the same figures the CLI prints. */
.stats { margin-left:auto; display:flex; gap:22px; }
.stat { display:flex; flex-direction:column; gap:2px; }
.stat span { font-size:10.5px; letter-spacing:.13em; text-transform:uppercase;
             color:var(--mut); }
.stat b { font:600 23px/1.1 var(--serif); font-variant-numeric:tabular-nums; }
.stat.ok b { color:var(--teal); }
.stat.flagged b { color:var(--red); }

.tools { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.tools input, .cols, .dl { font:13.5px var(--sans); border:1px solid #D3D2C7;
                           border-radius:3px; background:var(--bg); color:var(--ink); }
.tools input { padding:8px 12px; min-width:28ch; outline:none; }
.tools input:focus { border-color:var(--teal-lit); box-shadow:0 0 0 3px rgba(56,122,119,.18); }
.cols { padding:7px 13px; cursor:pointer; color:var(--body); }
.cols:hover, .dl:hover { border-color:var(--teal-lit); color:var(--teal); }
.tally { font-size:12.5px; color:var(--mut); font-variant-numeric:tabular-nums; }
.dl { margin-left:auto; padding:7px 13px; color:var(--teal); text-decoration:none;
      font-size:12.5px; }

.legend { display:flex; align-items:center; gap:16px; flex-wrap:wrap;
          font-size:12px; color:var(--body); }
.legend b { font:500 10.5px var(--sans); letter-spacing:.13em; text-transform:uppercase;
            color:var(--mut); }
.legend span { display:flex; align-items:center; gap:7px; }
.dot { width:9px; height:9px; border-radius:50%; border:1px solid; }

.scroll { overflow:auto; max-height:70vh; border:1px solid var(--line);
          border-radius:4px; background:var(--bg); }
.scroll::-webkit-scrollbar { height:10px; width:10px; }
.scroll::-webkit-scrollbar-thumb { background:#CFCEC4; border-radius:6px; }
table { border-collapse:separate; border-spacing:0; width:100%; font-size:12.5px; }
th, td { text-align:left; padding:8px 11px; border-bottom:1px solid var(--rule);
         white-space:nowrap; vertical-align:top; }
th { position:sticky; top:0; z-index:2; background:var(--sand); color:#6B7C87;
     font:500 11px var(--mono); letter-spacing:.06em; text-transform:uppercase;
     border-bottom:1px solid #DED9D0; cursor:pointer; user-select:none; }
th:hover, th[aria-sort=ascending], th[aria-sort=descending] { color:var(--teal); }
th[aria-sort=ascending]::after { content:" \\2191"; }
th[aria-sort=descending]::after { content:" \\2193"; }
/* The identifier stays put while the other 36 columns scroll under it. */
th:first-child, td:first-child { position:sticky; left:0; background:var(--bg);
                                 border-right:1px solid #E4E3DA; }
td:first-child { z-index:1; font-family:var(--mono); font-weight:500; }
th:first-child { z-index:3; }
tbody tr:hover td { background:#FAFAF7; }
td.num { text-align:right; font-variant-numeric:tabular-nums; font-family:var(--mono);
         color:#2F4448; }
td.id { font-family:var(--mono); color:var(--body); }
td.date { font-family:var(--mono); color:#828C90; }
td.blank { color:#C3C8C9; }
/* One line, clipped, with the whole string on the cell's `title`. The browser
   already renders that as a balloon on hover -- a hand-built one was 30 lines
   of positioning to say the same thing a second time, in one browser. Clipping
   is CSS, never a shortened string: the filter box matches on textContent, so a
   truncated cell would make evidence the one column that cannot be searched. */
td.wide { max-width:44ch; overflow:hidden; text-overflow:ellipsis; color:#828C90;
          cursor:help; }
/* The core view is the default; the toggle adds the other twenty. */
table:not(.all) .extra { display:none; }

/* Hovering a guest id shows that guest's slice of the resolution graph. The
   browser has no built-in for this -- `title` carries text, not a drawing -- so
   unlike the evidence tooltip, this one has to be built. Fixed and appended to
   <body>, because inside `.scroll` it would be clipped by the container. */
td[data-graph] { cursor:zoom-in; }
td[data-graph]:focus { outline:1px solid var(--teal-lit); outline-offset:-1px; }
.peek { position:fixed; z-index:20; padding:6px; border-radius:6px;
        border:1px solid var(--line); background:var(--bg);
        box-shadow:0 6px 20px rgba(29,42,46,.18); pointer-events:none; }
.sprite { position:absolute; width:0; height:0; overflow:hidden; }

.badge { display:inline-block; padding:2px 9px; border-radius:11px;
         font:500 11.5px var(--sans); border:1px solid #DEDDD3;
         color:#7C8689; background:#F3F3EE; }
.badge.strong { color:var(--teal); background:#E7EFEE; border-color:#9CC0BE; }
.badge.partial { color:#7A5510; background:#F8F1DF; border-color:#D9C08A; }
.badge.blocked { color:#8E1616; background:#F7EAEA; border-color:#D79E9E; }
.dot.strong { background:#E7EFEE; border-color:#9CC0BE; }
.dot.partial { background:#F8F1DF; border-color:#D9C08A; }
.dot.blocked { background:#F7EAEA; border-color:#D79E9E; }
.dot.plain { background:#F3F3EE; border-color:#DEDDD3; }

.empty { color:var(--mut); font-style:italic; padding:18px 0; }
.fileline { margin:0; font-family:var(--mono); font-size:11.5px; color:var(--faint); }

/* Prose, not a table: hold the chat tab to a reading measure and centre it, the
   way a chat client does. The table panel keeps the full width -- it is wide
   because its content is. */
#chat .bar { max-width:74ch; padding-top:26px; gap:20px; }
.thread { display:flex; flex-direction:column; gap:16px; }
.msg { white-space:pre-wrap; border:1px solid var(--line); border-radius:4px;
       padding:12px 16px; font-size:13.5px; line-height:1.6; }
.msg.me { align-self:flex-end; max-width:85%; background:var(--sand); border-color:#E4E3DA; }
.msg.bot { align-self:stretch; border-left:3px solid var(--teal-lit); color:var(--body); }
.msg.muted { color:var(--mut); font-style:italic; }
.ask { display:flex; gap:8px; }
.ask input { flex:1; font:13.5px var(--sans); padding:10px 13px; border-radius:3px;
             border:1px solid #D3D2C7; background:var(--bg); color:var(--ink); outline:none; }
.ask input:focus { border-color:var(--teal-lit); box-shadow:0 0 0 3px rgba(56,122,119,.18); }
.ask button { font:500 13.5px var(--sans); padding:10px 20px; border-radius:3px;
              cursor:pointer; border:1px solid var(--ink); background:var(--ink); color:#fff; }
.ask button[disabled] { opacity:.5; cursor:not-allowed; }
.answer { border:1px solid var(--line); border-left:3px solid var(--teal-lit);
          border-radius:4px; padding:16px 18px; margin:0; font-size:13.5px;
          line-height:1.6; color:var(--body); }
.answer code { font-family:var(--mono); font-size:12.5px; background:var(--sand);
               padding:1px 5px; border-radius:2px; }
.examples { margin:0; font-size:12.5px; color:var(--mut); }
.examples code { font-family:var(--mono); cursor:pointer;
                 border-bottom:1px dotted #CAC9BF; }

footer { background:var(--sand); border-top:1px solid var(--line);
         padding:16px 32px; font-size:11.5px; letter-spacing:.04em; }
footer .bar { display:flex; justify-content:flex-end; }
footer a { font-family:var(--mono); color:var(--teal); text-decoration:none; }
footer a:hover { color:var(--ink); text-decoration:underline; }
"""

# Sorting reads the `num` class the renderer already assigns, rather than
# re-sniffing types in the browser: one definition of what counts as a number.
SCRIPT = """
const tabs=[...document.querySelectorAll('nav button')];
tabs.forEach(b=>b.onclick=()=>{
  tabs.forEach(o=>o.setAttribute('aria-selected', o===b));
  document.querySelectorAll('section').forEach(s=>
    s.toggleAttribute('data-active', s.id===b.dataset.panel));
});

const bodyRows = s => [...s.querySelectorAll('tbody tr')];

function tally(section){
  const all = bodyRows(section), shown = all.filter(r => r.style.display !== 'none').length;
  const out = section.querySelector('.tally');
  if (out) out.textContent = shown === all.length
    ? `${all.length} rows` : `${shown} of ${all.length} rows`;
}

document.querySelectorAll('.tools input').forEach(box => {
  box.oninput = () => {
    const q = box.value.trim().toLowerCase(), section = box.closest('section');
    bodyRows(section).forEach(r => {
      r.style.display = !q || r.textContent.toLowerCase().includes(q) ? '' : 'none';
    });
    tally(section);
  };
});

// The hidden columns stay in the DOM: the filter reads whole rows, so a source
// column is searchable whether or not it is on screen.
document.querySelectorAll('.cols').forEach(btn => {
  btn.onclick = () => {
    const table = btn.closest('section').querySelector('table');
    if (!table) return;
    const all = table.classList.toggle('all');
    btn.setAttribute('aria-pressed', all);
    btn.textContent = all ? btn.dataset.less : btn.dataset.more;
  };
});

document.querySelectorAll('thead th').forEach((th, i) => {
  th.onclick = () => {
    const table = th.closest('table'), body = table.tBodies[0];
    const dir = th.getAttribute('aria-sort') === 'ascending' ? -1 : 1;
    table.querySelectorAll('th').forEach(o => o.setAttribute('aria-sort', 'none'));
    th.setAttribute('aria-sort', dir === 1 ? 'ascending' : 'descending');
    const at = r => r.cells[i], text = r => at(r) ? at(r).textContent.trim() : '';
    const numeric = [...body.rows].every(r => !text(r) || at(r).classList.contains('num'));
    const cmp = numeric
      ? (a, b) => (parseFloat(text(a)) || 0) - (parseFloat(text(b)) || 0)
      : (a, b) => text(a).localeCompare(text(b), undefined, {numeric: true});
    [...body.rows].sort((a, b) => dir * cmp(a, b)).forEach(r => body.appendChild(r));
  };
});

document.querySelectorAll('section').forEach(tally);

// One guest's panel, on hover or keyboard focus. The drawing is already in the
// page as a <symbol>; this only positions a frame around a <use> of it.
const sprite = document.querySelector('.sprite');
if (sprite) {
  const peek = document.createElement('div');
  peek.className = 'peek';
  peek.hidden = true;
  peek.innerHTML = '<svg width="250" height="132" viewBox="0 0 250 132">'
    + '<use href=""></use></svg>';
  document.body.appendChild(peek);
  const use = peek.querySelector('use');

  const show = cell => {
    use.setAttribute('href', '#' + cell.dataset.graph);
    peek.hidden = false;
    const at = cell.getBoundingClientRect(), box = peek.getBoundingClientRect();
    const below = at.bottom + 8, above = at.top - box.height - 8;
    peek.style.top = (below + box.height < innerHeight || above < 0 ? below : above) + 'px';
    peek.style.left = Math.min(at.left, innerWidth - box.width - 8) + 'px';
  };
  const hide = () => { peek.hidden = true; };

  document.querySelectorAll('td[data-graph]').forEach(cell => {
    cell.onmouseenter = () => show(cell);
    cell.onfocus = () => show(cell);
    cell.onmouseleave = hide;
    cell.onblur = hide;
  });
  addEventListener('scroll', hide, true);
}

const ask = document.querySelector('.ask');
if (ask) {
  const box = ask.querySelector('input'), go = ask.querySelector('button');
  const thread = document.querySelector('.thread');
  const hint = document.getElementById('hint'), examples = document.querySelector('.examples');
  // The transcript lives here and is posted back with each question; the server
  // holds no session. textContent throughout -- an answer is displayed as text,
  // never parsed as markup.
  const history = [];
  const say = (kind, text) => {
    const el = document.createElement('div');
    el.className = 'msg ' + kind;
    el.textContent = text;
    thread.appendChild(el);
    el.scrollIntoView({block: 'nearest'});
    return el;
  };
  document.querySelectorAll('.examples code').forEach(ex =>
    ex.onclick = () => { box.value = ex.textContent; box.focus(); });
  const send = async () => {
    const q = box.value.trim();
    if (!q) return;
    box.value = '';
    go.disabled = true;
    if (hint) hint.hidden = true;
    if (examples) examples.hidden = true;
    say('me', q);
    const out = say('bot muted', 'Asking...');
    try {
      const r = await fetch('chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({question: q, history: history}),
      });
      const d = await r.json();
      // usable=false is a real state, not an error: say what it says.
      out.className = d.usable ? 'msg bot' : 'msg bot muted';
      out.textContent = d.answer || ('no answer (HTTP ' + r.status + ')');
      // Only a real exchange joins the history. "No model configured" is about
      // the plumbing, and feeding it back would have the model explain itself.
      if (d.usable) {
        history.push({role: 'user', text: q}, {role: 'assistant', text: d.answer});
        // The server rejects more than 20 turns. Drop the oldest exchange in
        // pairs, keeping the alternation it validates, rather than sending a
        // request that comes back 422 once a conversation gets long.
        while (history.length > 20) history.splice(0, 2);
      }
    } catch (e) {
      out.className = 'msg bot muted';
      out.textContent = 'Could not reach the server: ' + e.message;
    }
    go.disabled = false;
    box.focus();
  };
  go.onclick = send;
  box.onkeydown = e => { if (e.key === 'Enter') send(); };
}
"""

LOGO_FILE = Path(__file__).resolve().parent / "static" / "gncl-logo-wide.svg"

DATE_COLUMNS = ("stay_start", "stay_end", "date")


def _logo() -> str:
    """The wordmark, inlined. An `<img src>` would be a second file to lose."""
    if not LOGO_FILE.exists():
        return '<span class="systems">GO NORDIC CRUISELINE</span>'
    return LOGO_FILE.read_text(encoding="utf-8").strip()


GRAPH_FILE = "graph.svg"


def _graph_defs(outdir: Path) -> str:
    """The panel symbols from `gncl graph`, or nothing.

    Read out of the file rather than redrawn here: this module renders whatever
    is in the output directory and never runs the pipeline. No `graph.svg`
    means no hover previews and a page that is otherwise identical.
    """
    path = outdir / GRAPH_FILE
    if not path.exists():
        return ""
    found = re.search(r"<defs>.*?</defs>", path.read_text(encoding="utf-8"), re.S)
    return found.group(0) if found else ""


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return value.strip() != ""


def _classes(column: str, value: str) -> str:
    """Type styling from the column name, the way the design reads a table."""
    if column == "evidence":
        return "wide"
    if not value.strip():
        return "blank"
    if _is_number(value):
        return "num"
    if column.endswith(("_id", "_ref", "_ids", "_number")) or column in ("cabin", "guest_id"):
        return "id"
    if any(word in column for word in DATE_COLUMNS):
        return "date"
    return ""


def _cell(column: str, value: str, extra: bool, previews: bool = False) -> str:
    text = html.escape(value)
    classes = [c for c in (_classes(column, value), "extra" if extra else "") if c]
    attr = f' class="{" ".join(classes)}"' if classes else ""
    if previews and column == "guest_id" and value:
        # The id points at a symbol in the inlined sprite; the script draws it.
        return f'<td{attr} data-graph="panel-{text}" tabindex="0">{text}</td>'

    if column == "evidence":
        # `title` twice over: the cell shows one clipped line, the browser's own
        # tooltip carries the rest. Escaped by the same call, since an attribute
        # value ends at the first unescaped quote.
        return f'<td{attr} title="{text}">{text}</td>'
    if column in BADGE and value:
        tone = f"badge {TONE[value]}" if value in TONE else "badge"
        return f'<td{attr}><span class="{tone}">{text}</span></td>'
    # An em dash, not an empty cell: absent is a value a reader should see.
    return f"<td{attr}>{text or '&mdash;'}</td>"


def _table(path: Path, previews: bool = False) -> tuple[str, int, dict[str, int]]:
    """The table, its row count, and the figures the stat tiles show."""
    with path.open(newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return '<p class="empty">Empty file.</p>', 0, {}
    header, body = rows[0], rows[1:]
    extras = {c for c in header if c not in CORE}
    head = "".join(
        f'<th aria-sort="none"{' class="extra"' if c in extras else ""}>'
        f"{html.escape(c.replace('_', ' '))}</th>"
        for c in header
    )
    out = [f"<thead><tr>{head}</tr></thead><tbody>"]
    for row in body:
        cells = "".join(
            _cell(c, v, c in extras, previews) for c, v in zip(header, row, strict=False)
        )
        out.append(f"<tr>{cells}</tr>")
    out.append("</tbody>")
    bands = [r[header.index("review")] for r in body] if "review" in header else []
    stats = {
        "rows": len(body),
        "columns": len(header),
        "accepted": sum(b == "accepted" for b in bands),
        "review": sum(b == "review" for b in bands),
    }
    table = f'<div class="scroll"><table>{"".join(out)}</table></div>'
    return table, len(body), stats


def _stats(stats: dict[str, int]) -> str:
    if not stats:
        return ""
    tiles = [("", "Rows", stats["rows"])]
    if stats["accepted"] or stats["review"]:
        tiles += [
            ("ok", "Accepted", stats["accepted"]),
            ("flagged", "Needs review", stats["review"]),
        ]
    cells = "".join(
        f'<div class="stat {kind}"><span>{label}</span><b>{value}</b></div>'
        for kind, label, value in tiles
    )
    return f'<div class="stats">{cells}</div>'


def _legend() -> str:
    dots = "".join(f'<span><i class="dot {tone}"></i>{label}</span>' for tone, label in LEGEND)
    return f'<div class="legend"><b>Labels</b>{dots}</div>'


def _download(path: Path, filename: str) -> str:
    """A download link carrying the file's own bytes, base64 in a data URI.

    Not a relative link to the sibling CSV. This page is one self-contained file
    by design, and a reviewer who moves or mails `index.html` would otherwise get
    two dead links. Embedding costs about 4/3 of the CSV size -- 25 KB of output
    here -- which is the right trade at this scale and the wrong one at a million
    rows, where this should serve the file over HTTP instead.

    The original bytes are shipped verbatim rather than rebuilt from the rendered
    table: re-serialising would mean re-quoting commas and quotes in `evidence`,
    and a download that silently differs from the file on disk is worse than no
    download at all.
    """
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    kb = round(path.stat().st_size / 1024)
    return (
        f'<a class="dl" download="{html.escape(filename)}" '
        f'href="data:text/csv;base64,{encoded}">Download CSV ({kb} KB)</a>'
    )


EXAMPLES = (
    "Why is G-BK1021 flagged for review?",
    "Why was phone country code not used as a signal?",
    "Who are the two Anna Larsens and how do you know they are different people?",
)


def _chat_panel(enabled: bool) -> str:
    """The chat tab, or an honest account of why it cannot answer.

    Three states, and none of them is a blank box that silently does nothing:
    served with a key, served without one, and the standalone file written by
    `gncl ui`, which has no server to post to at all.
    """
    if not enabled:
        return (
            '<p class="answer muted">Chat needs a hosted model and a server to reach it.'
            " Run <code>gncl serve</code> with <code>ANTHROPIC_API_KEY</code> set, then open"
            " the page it serves. Every table here was produced without it.</p>"
        )
    picks = " ".join(f"<code>{html.escape(q)}</code>" for q in EXAMPLES)
    return (
        # aria-live so a screen reader hears the answer arrive; the composer sits
        # below the transcript, which is where a chat client puts it.
        '<div class="thread" aria-live="polite"></div>'
        '<p class="answer muted" id="hint">Answers cite a guest_id or a document. Counts come'
        " from the pipeline, not the model. The conversation is kept in this page, so a"
        " reload starts a new one.</p>"
        f'<p class="examples">Try: {picks}</p>'
        '<div class="ask"><input type="text" aria-label="Ask a question"'
        ' placeholder="Ask about these guests, or about the approach"'
        "><button>Ask</button></div>"
    )


def render(outdir: Path, chat: bool = False) -> str:
    """Build the page from whatever tables exist. Missing ones say so.

    `chat` is False for the standalone file: it has no server to post to.
    """
    graph_defs = _graph_defs(outdir)
    navs, panels = [], []
    for i, (title, filename, blurb) in enumerate(TABLES):
        panel = f"panel{i}"
        path = outdir / filename
        if path.exists():
            table, count, stats = _table(path, previews=bool(graph_defs))
            tools = (
                '<div class="tools"><input type="search" placeholder="Filter rows"'
                ' aria-label="Filter rows">'
                f'<button class="cols" aria-pressed="false" data-more="All {stats["columns"]}'
                f' columns" data-less="Core columns">All {stats["columns"]} columns</button>'
                '<span class="tally"></span>'
                f"{_download(path, filename)}</div>"
            )
            body = (
                f"{_stats(stats)}</div>{tools}{_legend()}{table}"
                f'<p class="fileline">{html.escape(filename)} &middot; {count} rows'
                f" &middot; {stats['columns']} columns</p>"
            )
        else:
            body = (
                "</div>"
                f'<p class="empty">{html.escape(filename)} not found.'
                " Run <code>gncl resolve</code>.</p>"
            )
        selected = "true" if i == 0 else "false"
        active = " data-active" if i == 0 else ""
        navs.append(
            f'<button data-panel="{panel}" aria-selected="{selected}">{html.escape(title)}</button>'
        )
        panels.append(
            f'<section id="{panel}"{active}><div class="bar">'
            f'<div class="intro"><div><h2>One row per resolved guest</h2>'
            f'<p class="count">{html.escape(blurb)}</p></div>'
            f"{body}</div></section>"
        )
    navs.append('<button data-panel="chat" aria-selected="false">Chat</button>')
    panels.append(
        '<section id="chat"><div class="bar">'
        "<div><h2>Ask about the output</h2>"
        '<p class="count">The data, or why a particular choice was made. Answers cite the'
        " columns and documents they came from.</p></div>" + _chat_panel(chat) + "</div></section>"
    )
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>GNCL guest identity resolution</title>"
        f"<style>{STYLE}</style></head><body>"
        + (
            f'<svg class="sprite" xmlns="http://www.w3.org/2000/svg">{graph_defs}</svg>'
            if graph_defs
            else ""
        )
        + f'<header><div class="bar">{_logo()}<div class="sep"></div>'
        "<h1>Guest identity resolution</h1>"
        '<span class="systems">BOOKIT &middot; HUBSPOT &middot; LS RETAIL</span>'
        "</div></header>"
        f'<nav><div class="bar">{"".join(navs)}</div></nav>'
        f"<main>{''.join(panels)}</main>"
        '<footer><div class="bar">'
        '<a href="https://bliiir.com/" target="_blank" rel="noopener">bliiir.com</a>'
        "</div></footer>"
        f"<script>{SCRIPT}</script></body></html>"
    )


def write(outdir: Path) -> Path:
    path = outdir / "index.html"
    path.write_text(render(outdir), encoding="utf-8")
    return path
