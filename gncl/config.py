"""The one place `.env` is read.

Importing this module loads `.env`. Every other module reads configuration
through the accessors here rather than touching `os.environ`, because the
alternative already shipped a bug: `gncl/llm.py` read `OLLAMA_MODEL` at import
time while `load_dotenv()` ran later inside a click default, so a `.env` naming
a different model was silently ignored on the CLI path and the audit ran against
`gemma4:12b` regardless.

The accessors are functions, not constants, so a caller that reloads its own
module after changing the environment sees the change. Tests rely on that.

Values are parsed strictly. A malformed number raises rather than falling back
to a default, because the failure is invisible otherwise: `GNCL_THRESHOLD=0.9O`
(letter O) silently accepts 29 guests instead of the 20 that were asked for.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def dotenv_path() -> Path:
    """`.env` in the working directory, and nowhere else.

    Bare `load_dotenv()` resolves relative to the *calling file*, so an
    installed copy of this package reads the `.env` next to its own source
    rather than the one where the command was run. It also walks parent
    directories, which makes "which file configured this run" unanswerable.
    A reviewer runs these commands in the directory they cloned; that is the
    file they mean, and it is the same file `require` reports on.
    """
    return Path.cwd() / ".env"


load_dotenv(dotenv_path())

DEFAULT_THRESHOLD = 0.80
DEFAULT_OLLAMA_MODEL = "gemma4:12b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_RATE_LIMIT_PER_MIN = 60
DEFAULT_AUTH_FAIL_PER_MIN = 5

# Every variable `.env-example` documents. Presence of any one of them means
# configuration arrived some other way -- compose injects them directly, and
# there is no `.env` inside the container.
KNOWN = (
    "GNCL_AUTH_USER",
    "GNCL_AUTH_PASSWORD",
    "GNCL_THRESHOLD",
    "GNCL_RATE_LIMIT_PER_MIN",
    "GNCL_AUTH_FAIL_PER_MIN",
    "COMPOSE_PROFILES",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
)


class ConfigError(Exception):
    """Configuration is missing or unusable. Always actionable, never silent."""


def require() -> None:
    """Refuse to run on configuration nobody supplied.

    Satisfied by a `.env` file or by the variables already being in the
    environment. The second case is the container: compose reads `.env` on the
    host and injects the values, so the file itself never exists there.
    """
    if dotenv_path().is_file():
        return
    if any(os.environ.get(name) for name in KNOWN):
        return
    raise ConfigError(
        "no configuration found.\n\n"
        "    cp .env-example .env\n\n"
        f"Looked for {dotenv_path()}, then for the variables in the environment. "
        "The committed .env-example runs as-is; change the credentials before "
        "exposing an instance to a network."
    )


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise ConfigError(
            f"{name} is not a number: {raw!r}. Expected a value like {default}."
        ) from e


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(
            f"{name} is not a whole number: {raw!r}. Expected a value like {default}."
        ) from e


def threshold() -> float:
    value = _float("GNCL_THRESHOLD", DEFAULT_THRESHOLD)
    if not 0.0 <= value <= 1.0:
        raise ConfigError(f"GNCL_THRESHOLD must be between 0 and 1, got {value}.")
    return value


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL


def ollama_host() -> str:
    return os.environ.get("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST


def anthropic_key() -> str:
    """Hosted key for the chat tab. Empty means chat is off, which is a state."""
    return os.environ.get("ANTHROPIC_API_KEY", "")


def anthropic_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL


def auth_user() -> str:
    return os.environ.get("GNCL_AUTH_USER", "")


def auth_password() -> str:
    return os.environ.get("GNCL_AUTH_PASSWORD", "")


def rate_limit_per_min() -> int:
    return _int("GNCL_RATE_LIMIT_PER_MIN", DEFAULT_RATE_LIMIT_PER_MIN)


def auth_fail_per_min() -> int:
    return _int("GNCL_AUTH_FAIL_PER_MIN", DEFAULT_AUTH_FAIL_PER_MIN)
