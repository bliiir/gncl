"""Talk-to-the-data chat (#41, #10).

A hosted model here, a local one in the pipeline audit. Spec 5.1 argues the
split: chat is tens of human-paced calls whose cost is flat in dataset size,
while the audit is a fixed fraction of every link and linear in it. Put the
expensive model where the cost does not scale.

Everything is sent every call -- the output CSV, the three raw sources, the
case brief, and the docs. That is ~63 KB, comfortably inside the context window,
so there is no retrieval step and no vector store to be wrong. At a million
guests this stops being true and the design changes; at 35 it would be theatre.

Conversations are multi-turn, so "why?" and "which of those two?" mean what they
appear to. The browser keeps the transcript and posts it back; nothing is stored
here. The evidence pack rides on the first message of the conversation rather
than every turn, so a ten-turn exchange costs about what one turn costs.

Two failure modes are designed against.

*Answering from nothing.* With no key the answerer says so and returns no
answer, exactly as the pipeline audit reports `not run` rather than inventing a
verdict. `Answer.usable` distinguishes "no answer" from "the answer is no".

*Arithmetic.* Asked "how many are in review", a model reading 35 CSV rows will
produce a confident plausible number. It is not asked to count: `facts()`
computes the aggregates in pandas and puts them in the prompt, so the model
quotes a figure the pipeline produced instead of deriving one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from gncl import config

# A prior exchange: ("user" | "assistant", text). The browser holds the
# transcript and posts it back, so the server stays stateless -- no session to
# expire, and two workers behind a proxy behave the same as one.
Turn = tuple[str, str]

REPO = Path(__file__).resolve().parent.parent

# Read as evidence, in this order. The brief first because it is what the
# reader is being measured against; the analysis before the spec because "why
# was this signal rejected" is the question a reviewer actually asks.
GROUNDING = (
    "case/GNCL_AI_Engineer_Case.md",
    "docs/DATA_ANALYSIS.md",
    "docs/SPECIFICATION.md",
    "docs/MODEL_EVAL.md",
)

# The three raw inputs, as delivered. Sent as well as the output because "why
# were these two merged?" is better answered against what the source actually
# recorded than against the resolved view of it -- the output carries source IDs
# and an evidence string, not the row they came from. 7 KB for all three.
SOURCES = (
    "case/bookit_guests.csv",
    "case/ls_retail_transactions.csv",
    "case/hubspot_contacts.csv",
)

SYSTEM = """You answer questions about a guest identity resolution project, using only
the material given to you below.

Rules, in order of importance:

1. Ground every claim. Cite the `guest_id` for anything about a specific guest,
   and the file name for anything from the documents. A claim you cannot cite is
   one you must not make.
2. Do not calculate. Counts, sums and totals are given to you under MEASURED
   FACTS and in the CSV columns. Quote those. If a number is not given to you,
   say it is not available rather than deriving it.
3. Say when you do not know. The material is complete for what it covers; if a
   question falls outside it, say so plainly instead of inferring.
4. Be brief. Two or three sentences unless asked to expand.

The `case/*.csv` files are the raw inputs exactly as delivered, corruption
included. A name misspelt there is evidence about what a source system recorded,
not a fact about the guest; the resolved view of that guest is in
`guests.csv`. Row counts for the sources are under MEASURED FACTS -- do not
count the rows yourself.

`single source` and `review` are correct outcomes, not errors. A `single source`
guest appears in one system only, so there was no join to make -- it is accepted,
because there is nothing about it for a person to adjudicate. A guest in the
review band is one whose *join* is uncertain and does need a person; neither
indicates a failure of the pipeline."""


@dataclass(frozen=True)
class Answer:
    """A reply, or a stated reason there is none. Never a silent empty string."""

    text: str
    model: str = ""
    usable: bool = True

    @classmethod
    def unavailable(cls, reason: str) -> Answer:
        return cls(text=reason, model="", usable=False)


def facts(df: pd.DataFrame, threshold: float) -> str:
    """Aggregates computed here so the model never has to count rows itself."""
    lines = [
        f"Guests resolved: {len(df)}",
        f"Accept threshold: {threshold}",
    ]
    for column in ("match_method", "review", "audit", "name_source"):
        if column in df.columns:
            counts = df[column].value_counts().to_dict()
            lines.append(f"{column}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if "total_spend" in df.columns:
        lines.append(f"Total spend across all guests: {df['total_spend'].sum():.2f}")
    return "\n".join(lines)


def source_facts() -> str:
    """Row counts for the raw inputs, counted here for the same reason as `facts`.

    Sending the sources invites "how many BookIT records are there?", which a
    model will answer by counting lines and get wrong. Counted once, here.
    """
    lines = []
    for rel in SOURCES:
        path = REPO / rel
        if path.exists():
            rows = max(len(path.read_text().strip().splitlines()) - 1, 0)
            lines.append(f"{Path(rel).name}: {rows} rows")
    return "\n".join(lines)


def _section(path: Path, label: str) -> str:
    return f"=== {label} ===\n{path.read_text()}"


def context(df: pd.DataFrame, threshold: float, outdir: Path | None = None) -> str:
    """The evidence pack: measured facts, the output CSV, the raw sources, the docs.

    Output before sources before docs -- most questions are about the result,
    and a model reading in order meets the resolved view first.
    """
    measured = "\n".join(part for part in (facts(df, threshold), source_facts()) if part)
    parts = [f"=== MEASURED FACTS (authoritative; quote these) ===\n{measured}"]
    outdir = outdir or Path("out")
    path = outdir / "guests.csv"
    if path.exists():
        parts.append(_section(path, "guests.csv"))
    for rel in SOURCES:
        path = REPO / rel
        if path.exists():
            parts.append(_section(path, f"{rel} (raw source, as delivered)"))
    for rel in GROUNDING:
        path = REPO / rel
        if path.exists():
            parts.append(_section(path, rel))
    return "\n\n".join(parts)


def messages(question: str, grounding: str, history: Sequence[Turn] = ()) -> list[dict[str, str]]:
    """The conversation as the API wants it, oldest first.

    The evidence pack rides on the first user message rather than being repeated
    per turn: it is identical every time, so sending it once per call keeps a
    ten-turn conversation the same size as a one-turn one.

    Roles must alternate from `user`; the API layer validates that before
    anything reaches here.
    """
    turns = [*history, ("user", question)]
    return [
        {"role": role, "content": f"{grounding}\n\nQUESTION: {text}" if i == 0 else text}
        for i, (role, text) in enumerate(turns)
    ]


class NullAnswerer:
    """The default when no key is configured. Says so; never guesses."""

    name = "none"

    def answer(
        self,
        question: str,  # noqa: ARG002 - satisfies the port
        grounding: str,  # noqa: ARG002
        history: Sequence[Turn] = (),  # noqa: ARG002
    ) -> Answer:
        return Answer.unavailable(
            "No hosted model is configured, so this question was not answered. "
            "Set ANTHROPIC_API_KEY and restart `gncl serve`. Every table on this "
            "page was produced without it."
        )


class AnthropicAnswerer:
    """Hosted model. Constructed only when a key exists; see `build`."""

    def __init__(self, key: str, model: str | None = None) -> None:
        self._key = key
        self.name = model or config.anthropic_model()

    def answer(self, question: str, grounding: str, history: Sequence[Turn] = ()) -> Answer:
        try:
            import anthropic
        except ImportError:
            return Answer.unavailable("the anthropic client is not installed (uv sync --extra llm)")
        try:
            client = anthropic.Anthropic(api_key=self._key)
            response = client.messages.create(
                model=self.name,
                max_tokens=1024,
                system=SYSTEM,
                messages=messages(question, grounding, history),
            )
            text = "".join(block.text for block in response.content if block.type == "text")
        except Exception as e:  # noqa: BLE001 - any failure is "no answer", reported as such
            return Answer.unavailable(f"the hosted model could not be reached: {type(e).__name__}")
        return (
            Answer(text=text.strip(), model=self.name)
            if text.strip()
            else Answer.unavailable("the model returned nothing")
        )


def build():
    """Pick an answerer from configuration. No key is a state, not an error."""
    key = config.anthropic_key()
    return AnthropicAnswerer(key) if key else NullAnswerer()


def available() -> bool:
    """Whether chat can answer at all. Drives what the UI renders."""
    return bool(config.anthropic_key())
