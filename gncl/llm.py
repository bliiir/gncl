"""Local model audit (L3).

The model does not do the matching. Rules resolve every link in this dataset
(spec 5); the model only audits links whose name evidence contradicts them. It
is here because no similarity threshold generalises -- see spec 2.3 for the
measured scores.

`format` constrains decoding to a JSON schema only where the runtime honours it,
and the failure is silent: `gemma4:12b-mlx` accepts the parameter and returns
free prose anyway, byte-identical to a call with no `format` at all. The GGUF
build of the same model conforms, hence the default. `Verdict.malformed` keeps
that difference from surfacing as a contradicted link the model never judged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from gncl import config

# Via gncl.config, never the environment directly: importing it is what
# guarantees `.env` has been read before these are evaluated. Reading it here
# was the bug that made a `.env` naming another model silently ineffective.
DEFAULT_MODEL = config.ollama_model()
DEFAULT_HOST = config.ollama_host()

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "same_person": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["same_person", "confidence", "reason"],
}

SYSTEM = (
    "You judge whether two personal names refer to the same person. "
    "Nicknames, diminutives and anglicised short forms count as the same person "
    "(Beth for Elisabeth, Andy for Anders, Nils for Nikolaus). "
    "Different given names with a shared surname are different people. "
    "Answer only with the JSON object described. Be conservative: if the names "
    "are unrelated given names, say false."
)


@dataclass
class Verdict:
    same_person: bool
    confidence: float
    reason: str
    model: str
    raw: str = ""
    usable: bool = True
    """False when the model gave no answer we can act on.

    `same_person` is False on those only because a dataclass needs a value.
    Check this field, not the model name, or you will report a contradiction the
    model never made.
    """

    @classmethod
    def unavailable(cls, why: str) -> Verdict:
        """Transport failure: the model never answered. Not a wrong answer."""
        return cls(False, 0.0, f"model unavailable: {why}", "none", usable=False)

    @classmethod
    def malformed(cls, model: str, why: str) -> Verdict:
        """The model answered and the answer was unusable.

        Distinct from `unavailable` so a model emitting garbage is scored
        against rather than excused as a network problem.
        """
        return cls(False, 0.0, f"unusable response: {why}", model, usable=False)


def _prompt(captured: str, booking: str, context: str) -> str:
    return (
        f"A point-of-sale system recorded the name {captured!r}.\n"
        f"The booking for the same cabin and date is in the name {booking!r}.\n"
        f"{context}\n\n"
        "Is this the same person? Respond with JSON matching this schema:\n"
        f"{json.dumps(VERDICT_SCHEMA)}"
    )


PROBE_TIMEOUT_SECONDS = 2.0
HTTP_OK = 200


def _tagged(model: str) -> str:
    """Ollama resolves a bare name to its `:latest` tag; `/api/tags` reports the tag."""
    return model if ":" in model else f"{model}:latest"


def available(host: str = DEFAULT_HOST, model: str | None = DEFAULT_MODEL) -> bool:
    """Can `model` actually be asked a question on the server at `host`?

    Probing the server alone is not enough. A reachable server with the model
    absent answers every chat call with a 404, so a server-only check reports a
    capability the pipeline does not have: callers skip their fallback, run the
    audit, and collect `not run` verdicts. Pass `model=None` for a liveness
    check with no claim about what is loaded.
    """
    import json
    import urllib.request

    endpoint = "api/version" if model is None else "api/tags"
    try:
        # Localhost only by default; the host is operator-configured, not
        # user-supplied.
        with urllib.request.urlopen(  # noqa: S310
            f"{host}/{endpoint}", timeout=PROBE_TIMEOUT_SECONDS
        ) as r:
            if r.status != HTTP_OK:
                return False
            if model is None:
                return True
            installed = {_tagged(m["name"]) for m in json.load(r).get("models", [])}
    except Exception:  # noqa: BLE001 - unreachable is unreachable, however it failed
        return False
    return _tagged(model) in installed


def adjudicate(
    captured: str,
    booking: str,
    context: str = "",
    model: str = DEFAULT_MODEL,
    host: str = DEFAULT_HOST,
) -> Verdict:
    """Ask the local model whether two names are the same person."""
    try:
        from ollama import Client
    except ImportError:
        return Verdict.unavailable("ollama package not installed (uv sync --extra llm)")

    try:
        response = Client(host=host).chat(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": _prompt(captured, booking, context)},
            ],
            format=VERDICT_SCHEMA,
            think=False,
            options={"temperature": 0, "num_predict": 200},
            keep_alive="10m",
        )
    except Exception as exc:  # noqa: BLE001 - any transport failure is the same outcome
        return Verdict.unavailable(f"{type(exc).__name__}: {exc}")

    content = response["message"]["content"]
    try:
        data = json.loads(content)
        return Verdict(
            same_person=bool(data["same_person"]),
            confidence=float(data.get("confidence", 0.0)),
            reason=str(data.get("reason", "")).strip(),
            model=model,
            raw=content,
        )
    except json.JSONDecodeError:
        return Verdict.malformed(model, f"non-JSON: {content[:120]}")
    except (KeyError, TypeError, ValueError) as exc:
        # Valid JSON, wrong shape. A model that ignores `format` must not take
        # the pipeline down with it.
        return Verdict.malformed(model, f"{type(exc).__name__}: {content[:120]}")


class OllamaJudge:
    """`NameJudge` backed by a local Ollama server."""

    def __init__(self, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST) -> None:
        self._model, self._host = model, host

    @property
    def name(self) -> str:
        return self._model

    def judge(self, captured: str, booking: str, context: str = "") -> Verdict:
        return adjudicate(captured, booking, context, self._model, self._host)

    def reachable(self) -> bool:
        return available(self._host, self._model)


def audit_conflicts(
    conflicts: list[dict], judge=None, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST
) -> dict[str, Verdict]:
    """Audit each distinct name disagreement once, keyed by the name pair.

    Four transactions raise only two distinct questions here. The same cache is
    what keeps cost flat at scale, where variants repeat across a guest
    population.
    """
    judge = judge if judge is not None else OllamaJudge(model, host)
    cache: dict[tuple[str, str], Verdict] = {}
    out: dict[str, Verdict] = {}
    for c in conflicts:
        key = (c["captured"].casefold(), c["booking_name"].casefold())
        if key not in cache:
            cache[key] = judge.judge(
                c["captured"],
                c["booking_name"],
                context=(
                    f"Both records share cabin and stay dates "
                    f"(booking {c['booking_ref']}, transaction {c['transaction_id']}). "
                    f"String similarity is {c['ratio']}, which is too low to decide on."
                ),
            )
        out[c["transaction_id"]] = cache[key]
    return out
