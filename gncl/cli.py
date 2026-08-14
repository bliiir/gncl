"""Command line interface."""

from __future__ import annotations

from pathlib import Path

import click

from gncl import config
from gncl.join import GUESTS_FILE, write_outputs
from gncl.match import MatchResult, build
from gncl.output import build_guests
from gncl.ports import CsvSource

DEFAULT_THRESHOLD = config.DEFAULT_THRESHOLD


def _checked(fn, *args):
    """Config problems are the user's to fix, so report them, not a traceback.

    `gncl.config` raises rather than importing click, which would put a CLI
    dependency in the layer the API imports too.
    """
    try:
        return fn(*args)
    except config.ConfigError as e:
        raise click.ClickException(str(e)) from e


def _threshold_default() -> float:
    """Read at call time, not import time. Nothing needs it until a command runs."""
    return _checked(config.threshold)


@click.group()
def main() -> None:
    """Cross-source guest identity resolution for Go Nordic Cruiseline."""
    # Every command, not just the ones needing credentials: a missing .env is
    # worth saying out loud on the first command a reviewer runs, not the fifth.
    _checked(config.require)


def _pipeline(data: Path | None, audit: bool) -> tuple[dict, MatchResult, list]:
    """Sources -> graph -> records. Shared by `resolve` and `join`.

    The composition root: the only place that decides where data comes from and
    whether a model is consulted.
    """
    source = CsvSource(data)
    frames = source.frames()
    result = build(frames)
    verdicts = None
    if audit and result.name_conflicts:
        from gncl.llm import DEFAULT_MODEL, OllamaJudge, audit_conflicts, available

        if available():
            verdicts = audit_conflicts(result.name_conflicts, judge=OllamaJudge())
        else:
            click.echo(f"note: {DEFAULT_MODEL} is not available locally; skipping the audit\n")
    return frames, result, build_guests(result, frames, verdicts)


def _write_graph(
    out: Path, result: MatchResult, guests: list, threshold: float, guest=None
) -> Path:
    """Render the resolution graph. One place decides the filename and layout."""
    from gncl import svg

    out.mkdir(parents=True, exist_ok=True)
    path = out / (f"graph-{guest}.svg" if guest else "graph.svg")
    path.write_text(
        svg.render(result, guests, threshold, columns=1 if guest else 5), encoding="utf-8"
    )
    return path


@main.command()
@click.option(
    "--threshold",
    default=None,
    type=float,
    help="Accept band. Filters stored scores; does not re-run matching.",
)
@click.option("--out", default="out", type=click.Path(path_type=Path), show_default=True)
@click.option("--data", default=None, type=click.Path(path_type=Path), help="Source CSV directory.")
@click.option(
    "--audit/--no-audit", default=True, help="Audit conflicting names with the local model."
)
@click.option(
    "--graph/--no-graph",
    "draw",
    default=True,
    help="Also draw graph.svg. The UI uses it for the per-guest previews.",
)
def resolve(threshold: float | None, out: Path, data: Path | None, audit: bool, draw: bool) -> None:
    """Resolve guests across the three sources, write the table and the graph."""
    threshold = _threshold_default() if threshold is None else threshold
    frames, result, guests = _pipeline(data, audit)
    path, df = write_outputs(guests, result, frames, threshold, out)

    # Counted from the column the files carry, not re-derived from confidence: a
    # single-source guest scores 0.0 and is still accepted, so the comparison
    # this used to do reported 5 guests as flagged that no one is being asked to
    # look at.
    accepted = int((df["review"] == "accepted").sum())
    click.echo(f"{len(df)} guests resolved from {sum(len(f) for f in frames.values())} records")
    click.echo("")
    for method, group in sorted(df.groupby("match_method"), key=lambda kv: -len(kv[1])):
        click.echo(f"  {method:16} {len(group):3}")
    click.echo("")
    click.echo(f"  accepted (>= {threshold})  {accepted:3}")
    click.echo(f"  flagged for review    {len(df) - accepted:3}")
    if result.name_conflicts:
        n = len({c["booking_ref"] for c in result.name_conflicts})
        # "none" is the full frame's spelling of "no audit applies to this row";
        # counting it would report 33 guests as an audit outcome.
        audited = df[~df["audit"].isin(["", "none"])]["audit"]
        outcomes = audited.value_counts().to_dict()
        state = ", ".join(f"{v} {k}" for k, v in sorted(outcomes.items())) or "not audited"
        click.echo(
            f"  name conflicts        {len(result.name_conflicts):3} "
            f"transactions across {n} guests: {state}"
        )
    # Say this on stdout rather than burying it in a CSV column.
    unresolved_audits = int((df["audit"] == "not run").sum())
    if audit and unresolved_audits:
        click.echo(
            f"\n  warning: {unresolved_audits} name conflict(s) reached no usable verdict. "
            "See the evidence column for why."
        )
    if result.unresolved_transactions:
        click.echo(f"  unattached txns       {len(result.unresolved_transactions):3}")
    # Silent on this dataset. A line that only prints when the invariant breaks
    # says nothing when it holds, rather than reporting a zero as an achievement.
    for violation in result.closure_violations:
        click.echo(
            f"\n  warning: {violation['left']} and {violation['right']} resolved to one "
            f"guest but cannot be one person - {violation['reason']}"
        )
    written = [path]
    if draw:
        written.append(_write_graph(out, result, guests, threshold))
    click.echo("")
    for written_path in written:
        click.echo(f"wrote {written_path}")


@main.command()
@click.option("--out", default="out", type=click.Path(path_type=Path), show_default=True)
@click.option("--open/--no-open", "open_", default=True, help="Open in the default browser.")
def ui(out: Path, open_: bool) -> None:
    """Render the output tables as a browser page."""
    import webbrowser

    from gncl.ui import TABLES, write

    if not out.exists():
        raise click.ClickException(f"{out} not found; run `gncl resolve` first")
    path = write(out)
    for title, filename, _ in TABLES:
        state = "ok" if (out / filename).exists() else "missing"
        click.echo(f"  {title:14} {state:8} {filename}")
    click.echo(f"\nwrote {path}")
    if open_:
        webbrowser.open(path.resolve().as_uri())


@main.command()
@click.option("--out", default="out", type=click.Path(path_type=Path), show_default=True)
@click.option("--data", default=None, type=click.Path(path_type=Path), help="Source CSV directory.")
@click.option("--guest", default=None, help="Draw one guest instead of all of them.")
@click.option(
    "--audit/--no-audit",
    default=False,
    help="Audit conflicting names first. Off by default: the drawing shows the links rules made.",
)
def graph(out: Path, data: Path | None, guest: str | None, audit: bool) -> None:
    """Draw the resolution graph as an SVG: one panel per resolved guest.

    `resolve` already writes the full drawing. This command exists for `--guest`
    and for redrawing without re-writing the table.
    """
    threshold = _threshold_default()
    _, result, guests = _pipeline(data, audit)
    if guest:
        guests = [g for g in guests if g.guest_id == guest]
        if not guests:
            raise click.ClickException(f"no guest {guest}")
    click.echo(f"wrote {_write_graph(out, result, guests, threshold, guest)}")


@main.command()
@click.argument("guest_id")
@click.option("--out", default="out", type=click.Path(path_type=Path), show_default=True)
def show(guest_id: str, out: Path) -> None:
    """Print one guest with its full evidence chain."""
    import pandas as pd

    path = out / GUESTS_FILE
    if not path.exists():
        raise click.ClickException(f"{path} not found; run `gncl resolve` first")
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    rows = df[df["guest_id"] == guest_id]
    if rows.empty:
        raise click.ClickException(f"no guest {guest_id}")
    row = rows.iloc[0]
    for col in df.columns:
        if col == "evidence":
            continue
        click.echo(f"{col:20} {row[col]}")
    click.echo("evidence")
    for line in str(row["evidence"]).split(" | "):
        click.echo(f"  - {line}")


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True)
def serve(host: str, port: int) -> None:
    """Run the API. Requires GNCL_AUTH_USER and GNCL_AUTH_PASSWORD."""
    import uvicorn

    from gncl.api import AUTH_PASSWORD, AUTH_USER

    if not (AUTH_USER and AUTH_PASSWORD):
        raise click.ClickException(
            "set GNCL_AUTH_USER and GNCL_AUTH_PASSWORD (see .env-example); "
            "the API refuses to serve unauthenticated"
        )
    uvicorn.run("gncl.api:app", host=host, port=port)


if __name__ == "__main__":
    main()
