"""The resolution graph, drawn.

The drawing is not asserted pixel by pixel -- it will change. What is asserted
is that it cannot lie: every record and every link in the components it claims
to show is on the page, the counts in the legend come from what was drawn, and
the file needs nothing from the network.
"""

import re

import pytest
from click.testing import CliRunner

from gncl import svg
from gncl.cli import main
from gncl.match import build
from gncl.output import build_guests
from gncl.ports import CsvSource


@pytest.fixture(scope="module")
def pipeline():
    frames = CsvSource().frames()
    result = build(frames)
    return result, build_guests(result, frames)


@pytest.fixture(scope="module")
def drawing(pipeline):
    result, guests = pipeline
    return svg.render(result, guests)


def test_every_guest_gets_a_panel(drawing, pipeline):
    """Panels are symbols placed by reference, so the file is also a sprite the
    browser page can point one row at."""
    _, guests = pipeline
    assert drawing.count("<symbol id=") == len(guests) == 35
    assert drawing.count("<use href=") == 35, "every symbol is placed in the poster"
    for rec in guests:
        assert f'<symbol id="{svg.panel_id(rec.guest_id)}"' in drawing
        assert f">{rec.guest_id}</text>" in drawing


def test_every_record_and_link_is_drawn(drawing, pipeline):
    """120 records and 85 links: a drawing that quietly omits one is worse than none."""
    result, _ = pipeline
    for node in result.graph.nodes:
        assert f">{node.split(':', 1)[1]}</text>" in drawing, f"{node} missing"
    # Three legend rules share the <line> element with the edges.
    assert drawing.count("<line x1") == result.graph.number_of_edges() + 3


def test_the_legend_counts_what_was_drawn_not_the_corpus(pipeline):
    """A filtered drawing must not claim the whole dataset."""
    result, guests = pipeline
    one = [g for g in guests if g.guest_id == "G-BK1021"]
    assert "4 records" in svg.render(result, one, columns=1)
    assert "1 resolved guest" in svg.render(result, one, columns=1)
    assert "35 resolved guests" in svg.render(result, guests)


def test_the_review_flag_is_visible(drawing):
    """One guest is flagged, so exactly one panel carries the marker."""
    assert drawing.count(f'fill="{svg.RED}"') == 1


def test_weights_are_written_on_the_edges(drawing):
    """The number is the point: a link drawn without its weight says nothing."""
    for weight in ("0.98", "0.95", "0.90", "0.80", "0.55"):
        assert f">{weight}</text>" in drawing


def test_drawing_is_deterministic(pipeline):
    """Same input, same bytes. A random layout would churn the file every run."""
    result, guests = pipeline
    assert svg.render(result, guests) == svg.render(result, guests)


def test_drawing_needs_nothing_from_the_network(drawing):
    assert "<image" not in drawing
    assert not re.search(r'(src|href)="https?://', drawing)
    assert drawing.count("http") == 1, "only the SVG namespace"


def test_cli_writes_the_file(tmp_path):
    res = CliRunner().invoke(main, ["graph", "--out", str(tmp_path), "--no-audit"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "graph.svg").read_text().startswith("<svg")


def test_resolve_draws_the_graph_too(tmp_path):
    """`resolve` writes both files, so `resolve` + `serve` is the whole path.

    The UI reads `graph.svg` for its per-guest previews. Leaving it to a second
    command meant the previews were silently absent on a first run.
    """
    res = CliRunner().invoke(main, ["resolve", "--out", str(tmp_path), "--no-audit"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "guests.csv").exists()
    assert (tmp_path / "graph.svg").read_text().startswith("<svg")


def test_resolve_can_skip_the_graph(tmp_path):
    res = CliRunner().invoke(main, ["resolve", "--out", str(tmp_path), "--no-audit", "--no-graph"])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "guests.csv").exists()
    assert not (tmp_path / "graph.svg").exists()


def test_cli_can_draw_one_guest(tmp_path):
    runner = CliRunner()
    res = runner.invoke(main, ["graph", "--out", str(tmp_path), "--guest", "G-BK1021"])
    assert res.exit_code == 0, res.output
    assert "1 resolved guest" in (tmp_path / "graph-G-BK1021.svg").read_text()
    missing = runner.invoke(main, ["graph", "--out", str(tmp_path), "--guest", "G-NOPE"])
    assert missing.exit_code != 0
    assert "no guest G-NOPE" in missing.output
