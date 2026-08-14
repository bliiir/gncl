"""Model evaluation.

The gold set is built from the traps in this dataset, not invented: every
positive is a pair the pipeline has to judge, every negative one it would be
wrong to merge.

The set is small, so one case is several percentage points. Treat the table as a
filter for obvious failures, not a ranking. The always-yes and always-no
baseline rows show what a model that is not reading the question scores.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from gncl.llm import DEFAULT_HOST, Verdict


@dataclass(frozen=True)
class GoldCase:
    captured: str
    booking: str
    same_person: bool
    kind: str
    note: str


# Ratios quoted below are difflib.SequenceMatcher on FIRST NAMES, the metric
# gncl/match.py uses when it raises a conflict. Mixing in full-string ratios
# makes positives and negatives look like one band when they are not.
GOLD: list[GoldCase] = [
    # The two the pipeline actually needs. Rules cannot reach these.
    GoldCase("Beth", "Elisabeth Holm", True, "diminutive", "0.62, live case (BK1013)"),
    GoldCase("Andy", "Anders Moe", True, "diminutive", "0.60, live case (BK1018)"),
    # Same band, opposite truth. If a model says yes here it is guessing.
    GoldCase("Nils Berg", "Marcus Berg", False, "shared surname", "0.20 on first names"),
    GoldCase("Nils Berg", "Thomas Berg", False, "shared surname", "0.20 on first names"),
    GoldCase("Marcus Berg", "Thomas Berg", False, "shared surname", "0.50, closest negative"),
    GoldCase("Elisabeth Holm", "Linnea Holm", False, "shared surname", "0.40, cabins 3067/3068"),
    # Typos. Rules already handle these at 0.93; included to catch a model that
    # is so conservative it rejects obvious matches.
    GoldCase("Mikael Svnesson", "Mikael Svensson", True, "typo", "0.93, transposition"),
    GoldCase("Camilla Strnad", "Camilla Strand", True, "typo", "0.93, transposition"),
    # The substring trap the date window catches. A model must not merge them.
    GoldCase("Fredrik", "Tone Fredriksen", False, "substring", "TX5036 trap"),
    GoldCase("Fredrik", "Fredrik Lund", True, "first name", "TX5036 true target"),
    # Scandinavian transliteration.
    GoldCase("Bjorn Kristiansen", "Bjørn Kristiansen", True, "transliteration", "o-slash folding"),
    GoldCase("Freja Sorensen", "Freja Sørensen", True, "transliteration", "o-slash folding"),
    # Unrelated. A model that says yes to this is unusable.
    GoldCase("Karin Lindqvist", "Ole Johansen", False, "unrelated", "control"),
    GoldCase("Sigrid Dahl", "Signe Mortensen", False, "unrelated", "control, similar initials"),
    GoldCase("Kristine", "Kristine Andersen", True, "first name", "BK1011, TX5019 pattern"),
]


# Swedish short forms, for the roadmap rather than the sample. GNCL runs
# two vessels today and wants to show its Swedish parent the approach applies to
# their operations, so Swedish name handling is the axis that decides the model.
#
# This set is where the "no threshold works" claim actually holds. Ratios are
# first names, the same metric match.py uses. lasse/lars, kalle/karl and
# nisse/nils are all 0.67 and all the same person; stina/stig is also 0.67 and is
# not. pelle/per is 0.50 and is the same person, scoring below that true
# negative. No cut separates them.
SWEDISH: list[GoldCase] = [
    GoldCase("Lasse", "Lars Eriksson", True, "sv diminutive", "0.67, collides with stina/stig"),
    GoldCase("Kalle", "Karl Nyberg", True, "sv diminutive", "0.67"),
    GoldCase("Nisse", "Nils Berg", True, "sv diminutive", "0.67"),
    GoldCase("Stina", "Kristina Lund", True, "sv diminutive", "0.77"),
    GoldCase("Micke", "Mikael Svensson", True, "sv diminutive", "0.73"),
    GoldCase("Pelle", "Per Sjoberg", True, "sv diminutive", "0.50, below a true negative"),
    GoldCase("Bettan", "Elisabeth Holm", True, "sv diminutive", "0.40"),
    GoldCase("Stina", "Stig Lund", False, "sv near-miss", "0.67, same score as lasse/lars"),
    GoldCase("Kalle", "Kajsa Nyberg", False, "sv near-miss", "0.40"),
    GoldCase("Nisse", "Nina Berg", False, "sv near-miss", "0.44"),
    GoldCase("Malin Ohlsson", "Malin Olsson", False, "sv near-miss", "one letter, two surnames"),
    GoldCase("Bjorn Aker", "Bjorn Akerlund", False, "sv near-miss", "prefix, different surname"),
    GoldCase("Sussie", "Susanne Ahlberg", True, "sv diminutive", "0.62"),
    GoldCase("Goran Sjoberg", "Goeran Sjoeberg", True, "sv transliteration", "o-umlaut folding"),
    GoldCase("Lasse Eriksson", "Lasse Ericsson", False, "sv near-miss", "ks/cs, two surnames"),
    GoldCase("Anders Ohlsson", "Anders Ohlin", False, "sv near-miss", "shared prefix"),
    GoldCase(
        "Karin Lindqvist", "Karin Lindberg", False, "sv near-miss", "shared given, different family"
    ),
]

SETS: dict[str, list[GoldCase]] = {"traps": GOLD, "swedish": SWEDISH}


@dataclass
class ModelResult:
    model: str
    correct: int = 0
    total: int = 0
    latencies: list[float] = field(default_factory=list)
    failures: list[tuple[GoldCase, Verdict]] = field(default_factory=list)
    unavailable: str = ""

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def p50(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[len(s) // 2]

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[min(len(s) - 1, int(len(s) * 0.95))]


def run_model(
    model: str,
    host: str = DEFAULT_HOST,
    gold: list[GoldCase] | None = None,
    judge=None,
) -> ModelResult:
    """Score one judge against a gold set. `judge` is the seam tests stub."""
    from gncl.llm import OllamaJudge

    judge = judge if judge is not None else OllamaJudge(model, host)
    cases = gold if gold is not None else GOLD
    out = ModelResult(model=model, total=len(cases))
    for case in cases:
        started = time.monotonic()
        verdict = judge.judge(case.captured, case.booking)
        out.latencies.append(time.monotonic() - started)
        if verdict.model == "none":
            # Transport failure: the model never answered, so nothing is scored.
            out.unavailable = verdict.reason
            out.latencies.pop()
            out.total = 0
            return out
        if verdict.same_person == case.same_person:
            out.correct += 1
        else:
            out.failures.append((case, verdict))
    return out


def render_all(sections: dict[str, list[ModelResult]]) -> str:
    """One table per gold set.

    A model can pass the traps and fail the Swedish set. Reporting only the
    first would settle the model choice on the wrong population.
    """
    out = ["# Model comparison", "", "Regenerate with `uv run python -m gncl.eval`.", ""]
    titles = {
        "traps": (
            "This dataset's traps",
            "Decides whether a model handles the conflicts the pipeline raises today.",
        ),
        "swedish": (
            "Swedish short forms",
            (
                "Decides whether it survives the intended population. This is the set"
                " where no threshold works: lasse/lars, kalle/karl and nisse/nils are 0.67"
                " and the same person; stina/stig is 0.67 and is not; pelle/per is 0.50 and"
                " is."
            ),
        ),
    }
    for name, results in sections.items():
        title, why = titles.get(name, (name, ""))
        out += [f"## {title}", "", why, ""]
        out += render(results, SETS[name]).split("\n")
    return "\n".join(out).rstrip() + "\n"


def render(results: list[ModelResult], gold: list[GoldCase] | None = None) -> str:
    cases = gold if gold is not None else GOLD
    lines = []
    lines += [
        f"Gold set: {len(cases)} pairs.",
        "",
        (
            f"**Calibration.** {len(cases)} items means one case is "
            f"{100 / len(cases):.0f} percentage points. A model ahead by a single case has "
            "not been shown to be better. This table filters obvious failures; it does "
            "not rank close finishers. The baseline rows are what a model that ignores "
            "the question scores."
        ),
        "",
        "| Model | Accuracy | Correct | p50 | p95 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        if r.unavailable:
            lines.append(f"| {r.model} | not run | - | - | - |")
            continue
        lines.append(
            f"| {r.model} | {r.accuracy:.0%} | {r.correct}/{r.total} | "
            f"{r.p50:.2f}s | {r.p95:.2f}s |"
        )
    yes = sum(1 for c in cases if c.same_person)
    for label, correct in (
        ("baseline: always yes", yes),
        ("baseline: always no", len(cases) - yes),
    ):
        lines.append(f"| _{label}_ | {correct / len(cases):.0%} | {correct}/{len(cases)} | - | - |")
    lines.append("")
    for r in results:
        if r.unavailable:
            lines += [f"**{r.model}** did not run: {r.unavailable}", ""]
            continue
        if r.failures:
            lines += [f"**{r.model}** got these wrong:", ""]
            for case, verdict in r.failures:
                lines.append(
                    f"- `{case.captured}` vs `{case.booking}` ({case.kind}): "
                    f"expected {case.same_person}, said {verdict.same_person}. {verdict.reason}"
                )
            lines.append("")
    return "\n".join(lines) + "\n"


# gemma4:12b-mlx is kept deliberately. It is not a candidate any more; it is the
# evidence for why the default is the GGUF build, and the row it produces shows
# what an unconstrained decode scores against a set it understands perfectly.
CANDIDATES = ["gemma4:12b", "gemma4:26b", "gemma4:12b-mlx"]


if __name__ == "__main__":
    from pathlib import Path

    sections = {
        name: [run_model(m, gold=cases) for m in CANDIDATES] for name, cases in SETS.items()
    }
    out = Path(__file__).resolve().parent.parent / "docs" / "MODEL_EVAL.md"
    out.write_text(render_all(sections))
    print(f"wrote {out}")
    for name, results in sections.items():
        for r in results:
            print(f"  {name:8} {r.model:20} {'NOT RUN' if r.unavailable else f'{r.accuracy:.0%}'}")
