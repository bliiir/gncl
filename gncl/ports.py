"""Boundaries between the pipeline and the things it talks to.

Adapters are constructed at the entrypoints and passed down; nothing in the core
imports `load_all` or `ollama`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class GuestSource(Protocol):
    """Where the three record sets come from.

    A second implementation is the point: today it is CSVs in a directory, at
    scale it is a query. The pipeline should not have to change for that.
    """

    def frames(self) -> dict[str, pd.DataFrame]:
        """Return frames keyed `bookit`, `hubspot`, `ls_retail`."""
        ...


@runtime_checkable
class NameJudge(Protocol):
    """Decides whether two personal names refer to the same person.

    Implementations: a local model over Ollama, a hosted model, a stub in
    tests, or a nickname gazetteer if one ever beats the model on the eval.
    """

    def judge(self, captured: str, booking: str, context: str = "") -> object:
        """Return a `gncl.llm.Verdict`."""
        ...

    @property
    def name(self) -> str:
        """Identifier for the evidence string and the eval table."""
        ...


class CsvSource:
    """The default `GuestSource`: three named CSVs in a directory."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir
        self._cached: dict[str, pd.DataFrame] | None = None

    def frames(self) -> dict[str, pd.DataFrame]:
        if self._cached is None:
            from gncl.load import load_all

            self._cached = load_all(self._data_dir) if self._data_dir else load_all()
        return self._cached


class NullJudge:
    """The default when no model is configured.

    Distinct from a judge that says "different people": callers must see that no
    judgement was made, which is why `Verdict.usable` exists.
    """

    name = "none"

    def judge(self, captured: str, booking: str, context: str = "") -> object:
        from gncl.llm import Verdict

        return Verdict.unavailable("no judge configured")
