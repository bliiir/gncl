"""The placeholder browser view.

Design is deliberately not tested — it is a separate deliverable and will change.
What is tested is what would still be wrong after a redesign: escaping, offline
self-containment, behaviour when a table is missing, and the one behavioural
constraint the spec fixes.
"""

import re

from gncl.ui import TONE, render, write

TABLES = ("guests.csv",)


def _write_csv(path, header, rows):
    lines = [",".join(header)] + [",".join(r) for r in rows]
    path.write_text("\n".join(lines) + "\n")


def test_renders_the_table_it_finds(tmp_path):
    _write_csv(
        tmp_path / "guests.csv", ["guest_id", "review"], [["G-1", "accepted"], ["G-2", "review"]]
    )
    html = render(tmp_path)
    for guest in ("G-1", "G-2"):
        assert guest in html
    # The guests table plus the chat tab, which renders whether or not it can answer.
    assert len(re.findall(r"data-panel=", html)) == 2


def test_a_missing_table_is_reported_not_fatal(tmp_path):
    """A reviewer who runs `gncl ui` before `gncl resolve` gets told, not a stack trace."""
    html = render(tmp_path)
    assert "guests.csv not found" in html
    assert "gncl resolve" in html


def test_cell_values_are_escaped(tmp_path):
    """Evidence strings carry quotes and apostrophes, and one day real names will.

    The `title` attribute makes this sharper: an unescaped quote there ends the
    attribute and everything after it becomes markup.
    """
    _write_csv(
        tmp_path / "guests.csv",
        ["guest_id", "evidence"],
        [["G-1", "<script>alert(1)</script>"]],
    )
    html = render(tmp_path)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_page_is_self_contained(tmp_path):
    """The vessel profile assumes no egress; a page needing the network is not one.

    Checked as *fetches*, not as the letters http. Two things legitimately carry
    a URL: the wordmark's `xmlns`, which a parser compares and never
    dereferences, and the footer link, which is somewhere to go rather than
    something the page loads. What must not appear is a subresource -- a
    stylesheet, font, script or image the browser fetches to finish rendering.
    """
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    html = render(tmp_path)
    flat = html.replace('"', "").replace("'", "").replace(" ", "")
    for attr in ("src=", "srcset=", "@import", "url("):
        for scheme in ("http://", "https://", "//fonts."):
            assert attr + scheme not in flat, f"{attr} fetches {scheme}"
    assert "<link" not in html, "no stylesheet, font or icon may be linked"
    assert "fonts.googleapis" not in html and "fonts.gstatic" not in html
    assert "<style>" in html, "styles must be inline, not linked"
    # Every remaining external URL is a namespace or a link's destination.
    external = re.findall(r'(\w+)="(https?://[^"]+)"', html)
    assert {attr for attr, _ in external} <= {"xmlns", "href"}
    for attr, url in external:
        if attr == "href":
            assert f'<a href="{url}"' in html, "an external href must be a link, not a load"


def test_the_footer_carries_one_link(tmp_path):
    """Nothing else belongs down there, and it must open away from the page."""
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    html = render(tmp_path)
    footer = re.search(r"<footer>.*?</footer>", html, re.S).group(0)
    assert footer.count("<a ") == 1
    assert 'href="https://bliiir.com/"' in footer
    assert 'rel="noopener"' in footer, "target=_blank without it hands over window.opener"
    assert ">" in footer and "Copenhagen" not in footer


def test_single_source_is_not_rendered_as_an_error(tmp_path):
    """docs/SPECIFICATION.md section 7. A single-source row is a correct outcome.

    Colouring it as failure contradicts the central claim of the project, so no
    error styling may key off the value. Amber is not failure: it marks a row
    held up by one system's word, which is what the reader should notice.
    """
    _write_csv(
        tmp_path / "guests.csv",
        ["guest_id", "match_method"],
        [["G-1", "single source"], ["G-2", "deterministic"]],
    )
    html = render(tmp_path)
    row = re.search(r"<tr>(?:(?!</tr>).)*single source(?:(?!</tr>).)*</tr>", html, re.S)
    assert row, "the single-source row should render"
    markup = row.group(0)
    for signal in ("error", "danger", "fail", "warning", "red"):
        assert signal not in markup.lower(), f"single source styled as {signal}"


def test_single_source_carries_no_alarm_tone():
    """The same constraint at the tone layer, where a colour scheme would break it.

    The test above rejects the word "red"; this one rejects the tone slots, so
    `single source: strong` -- claiming evidence a lone record does not have --
    or a fourth alarm tone fails here rather than shipping.
    """
    assert TONE["single source"] == "partial"
    assert TONE["single source"] != "blocked", "a lone record is not a conflict"
    # The design's scale: clean, needs a look, blocked. The fourth tier --
    # nothing to flag -- is the unmapped default, so it is absent by design.
    assert set(TONE.values()) == {"strong", "partial", "blocked"}
    # Red is for a model contradicting a rule, and nothing else reaches it.
    assert [v for v, t in TONE.items() if t == "blocked"] == ["contradicted"]


def test_decision_values_get_their_tone(tmp_path):
    _write_csv(
        tmp_path / "guests.csv",
        ["match_method", "review", "name_source"],
        [
            ["deterministic", "accepted", "as recorded"],
            ["rule_fuzzy", "review", "repaired from email local part"],
            ["single source", "", "as recorded"],
        ],
    )
    html = render(tmp_path)
    for value, tone in [
        ("deterministic", "strong"),
        ("accepted", "strong"),
        ("as recorded", "strong"),
        ("rule_fuzzy", "partial"),
        ("review", "partial"),
        ("repaired from email local part", "partial"),
        ("single source", "partial"),
    ]:
        assert f'<span class="badge {tone}">{value}</span>' in html


def test_toned_values_match_what_the_pipeline_emits():
    """A tone keyed on a string nothing produces is dead styling.

    `name_source` in particular is prose built in output.display_name, so a
    reworded return value would silently stop matching.
    """
    from gncl.output import GuestRecord, display_name

    repaired = display_name(
        {"guest_name": "Camilla Strnad", "email_norm": "camilla.strand@gmail.com"}, "guest_name"
    )[1]
    clean = display_name({"guest_name": "Nils Berg", "email_norm": ""}, "guest_name")[1]
    assert repaired != clean, "the fixture stopped exercising the repair path"
    assert TONE.get(repaired) == "partial", f"untoned name_source: {repaired!r}"
    assert TONE.get(clean) == "strong", f"untoned name_source: {clean!r}"
    assert GuestRecord("G-1").match_method in TONE


def test_an_untoned_badge_value_still_renders(tmp_path):
    """`audit` shares the badge treatment but has no tone; "none" is a value, not a class."""
    _write_csv(tmp_path / "guests.csv", ["audit"], [["none"], ["not run"]])
    html = render(tmp_path)
    assert '<span class="badge">none</span>' in html
    assert '<span class="badge">not run</span>' in html


def test_every_column_is_sortable(tmp_path):
    """The sort hook is an attribute, so it survives a restyle; the arrows do not."""
    _write_csv(tmp_path / "guests.csv", ["guest_id", "name", "total_spend"], [["G-1", "A", "1"]])
    html = render(tmp_path)
    assert len(re.findall(r'<th aria-sort="none">', html)) == 3


def test_a_rendered_table_gets_a_filter_box_and_a_missing_one_does_not(tmp_path):
    """Filtering nothing is a dead control; the missing-table panel stays a message."""
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    html = render(tmp_path)
    assert len(re.findall(r'<input type="search"', html)) == 1
    assert render(tmp_path / "empty").count('<input type="search"') == 0


def test_sorting_reads_the_class_the_renderer_assigns(tmp_path):
    """Numeric sort keys off `td.num`, so the renderer must still emit it."""
    _write_csv(tmp_path / "guests.csv", ["name", "total_spend"], [["Berg", "1240.50"]])
    html = render(tmp_path)
    assert '<td class="num">1240.50</td>' in html
    assert "classList.contains('num')" in html


def test_write_creates_index_html(tmp_path):
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    path = write(tmp_path)
    assert path.name == "index.html"
    assert path.read_text().startswith("<!doctype html>")


def test_download_carries_the_file_bytes_verbatim(tmp_path):
    """The download must be the file, not a re-serialisation of the rendered table.

    `evidence` contains commas, quotes and pipes. Rebuilding CSV from the DOM
    means re-quoting all of it, and a download that silently differs from the
    file on disk is worse than no download.
    """
    import base64

    source = tmp_path / "guests.csv"
    _write_csv(source, ["guest_id", "evidence"], [["G-1", '"quoted, comma" | pipe']])
    html = render(tmp_path)
    encoded = re.search(r'href="data:text/csv;base64,([^"]+)"', html).group(1)
    assert base64.b64decode(encoded) == source.read_bytes()


def test_each_rendered_table_offers_a_download_and_a_missing_one_does_not(tmp_path):
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    html = render(tmp_path)
    assert html.count('class="dl"') == 1
    assert 'download="guests.csv"' in html
    assert 'class="dl"' not in render(tmp_path / "empty")


def test_download_keeps_the_page_self_contained(tmp_path):
    """A relative link to the sibling CSV would die the moment index.html moves."""
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    html = render(tmp_path)
    assert 'href="guests.csv"' not in html, "relative link, not embedded"
    assert "data:text/csv;base64," in html


def test_static_page_says_chat_needs_a_server(tmp_path):
    """`gncl ui` writes a file with nothing to post to. It must say so, not sit dead."""
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    html = render(tmp_path)
    assert 'data-panel="chat"' in html
    assert "Chat needs a hosted model" in html
    assert "gncl serve" in html
    assert '<div class="ask">' not in html, "no input box that cannot work"


def test_served_page_gets_a_live_chat_box(tmp_path):
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    html = render(tmp_path, chat=True)
    assert '<div class="ask">' in html
    assert "Chat needs a hosted model" not in html
    assert "Counts come from the" in html


def test_chat_posts_to_a_relative_path(tmp_path):
    """The page is served from / by the same app, so the URL must not be absolute.

    An absolute host would also break test_page_is_self_contained.
    """
    html = render(tmp_path, chat=True)
    assert "fetch('chat'" in html
    assert "localhost" not in html


def test_evidence_is_one_line_but_keeps_its_full_text(tmp_path):
    """Clipped in CSS, never in Python.

    The filter box matches on row textContent, so truncating the string in the
    renderer would make evidence -- the one column worth searching -- unfindable.
    Hover is the browser's `title` balloon; a hand-built one duplicated it.
    """
    reason = (
        "cabin + stay window: cabin 4021 on 2026-07-12 falls in 2026-07-11..2026-07-14"
        " | exact email anna.larsen@gmail.com | name repaired from email local part"
    )
    _write_csv(tmp_path / "guests.csv", ["guest_id", "evidence"], [["G-1", reason]])
    html = render(tmp_path)
    assert reason in html, "the full string must stay in the DOM for the filter"
    assert f'title="{reason}"' in html, "the whole reason must be on the title"
    assert re.search(r"text-overflow\s*:\s*ellipsis", html)
    # No second tooltip: the browser already draws one from `title`, and the
    # hand-rolled one showed the same string twice, a beat apart.
    assert ".balloon {" not in html and "showBalloon" not in html


def _sprite(tmp_path, guest_id="G-1"):
    """A minimal `gncl graph` output: one panel symbol inside a defs block."""
    (tmp_path / "graph.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">'
        f'<defs><symbol id="panel-{guest_id}" viewBox="0 0 250 132">'
        '<rect width="250" height="132"/></symbol></defs>'
        f'<use href="#panel-{guest_id}"/></svg>'
    )


def test_guest_ids_preview_their_graph_panel(tmp_path):
    """Hovering an id shows that guest's slice of the graph.

    `title` cannot carry a drawing, which is why this tooltip is hand-built
    where the evidence one is not.
    """
    _write_csv(tmp_path / "guests.csv", ["guest_id", "review"], [["G-1", "accepted"]])
    _sprite(tmp_path)
    html = render(tmp_path)
    assert 'data-graph="panel-G-1"' in html
    assert '<symbol id="panel-G-1"' in html, "the drawing must be inlined, not linked"
    assert 'class="sprite"' in html
    assert "use.setAttribute('href', '#' + cell.dataset.graph)" in html


def test_the_page_is_the_same_without_a_graph(tmp_path):
    """`gncl ui` before `gncl graph` is a normal state, not a broken page."""
    _write_csv(tmp_path / "guests.csv", ["guest_id", "review"], [["G-1", "accepted"]])
    html = render(tmp_path)
    # The stylesheet always carries the selector; what must be absent is a cell
    # claiming a drawing that is not in the page.
    assert 'data-graph="' not in html
    assert "<symbol id=" not in html
    assert 'class="sprite"' not in html
    assert "G-1" in html and "<table" in html


def test_the_preview_is_not_clipped_by_the_scroll_box(tmp_path):
    """The table scrolls in its own container, so an absolutely positioned
    tooltip inside it would be cut off. Fixed, on the body, or not at all."""
    _write_csv(tmp_path / "guests.csv", ["guest_id"], [["G-1"]])
    _sprite(tmp_path)
    html = render(tmp_path)
    assert re.search(r"\.peek \{[^}]*position:fixed", html)
    assert "document.body.appendChild(peek)" in html
