"""Configuration loading and refusal.

`.env` is the documented way to configure this project, so the two failure modes
that matter are it being absent and it being wrong. Both used to be silent.
"""

import importlib
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from gncl import config
from gncl.cli import main


@pytest.fixture
def unconfigured(tmp_path, monkeypatch):
    """A directory with no .env and an environment with no GNCL_/OLLAMA_ vars."""
    monkeypatch.chdir(tmp_path)
    for name in config.KNOWN:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_missing_config_is_refused_with_the_command_that_fixes_it(unconfigured):
    with pytest.raises(config.ConfigError) as e:
        config.require()
    assert "cp .env-example .env" in str(e.value)


def test_every_command_refuses_not_just_the_ones_needing_secrets(unconfigured):
    """`resolve` needs no credentials, but it is the first command a reviewer runs."""
    result = CliRunner().invoke(main, ["resolve", "--out", str(unconfigured), "--no-audit"])
    assert result.exit_code != 0
    assert "cp .env-example .env" in result.output
    assert "Traceback" not in result.output


def test_a_dotenv_file_satisfies_the_check(unconfigured):
    (unconfigured / ".env").write_text("GNCL_THRESHOLD=0.80\n")
    config.require()


def test_injected_variables_satisfy_the_check_without_a_file(unconfigured, monkeypatch):
    """The container has no .env: compose reads it on the host and injects values.

    A check for the file rather than the configuration would fail the one
    deployment path the project actually ships.
    """
    assert not (unconfigured / ".env").exists()
    monkeypatch.setenv("GNCL_AUTH_USER", "injected")
    config.require()


@pytest.mark.parametrize(
    ("name", "value", "reader"),
    [
        ("GNCL_THRESHOLD", "not-a-number", "threshold"),
        ("GNCL_THRESHOLD", "0.9O", "threshold"),  # letter O, the realistic typo
        ("GNCL_RATE_LIMIT_PER_MIN", "sixty", "rate_limit_per_min"),
        ("GNCL_AUTH_FAIL_PER_MIN", "5.5", "auth_fail_per_min"),
    ],
)
def test_malformed_values_are_refused_by_name(monkeypatch, name, value, reader):
    monkeypatch.setenv(name, value)
    with pytest.raises(config.ConfigError) as e:
        getattr(config, reader)()
    assert name in str(e.value)
    assert value in str(e.value)


def test_out_of_range_threshold_is_refused(monkeypatch):
    monkeypatch.setenv("GNCL_THRESHOLD", "80")
    with pytest.raises(config.ConfigError, match="between 0 and 1"):
        config.threshold()


def test_dotenv_reaches_the_model_config(tmp_path):
    """Regression: OLLAMA_MODEL in .env was read before load_dotenv() had run.

    `gncl.llm` took its defaults from os.environ at import time while the only
    load_dotenv() call sat inside a click option default, so a .env naming a
    different model was silently ignored and the audit ran against gemma4:12b.
    Asserted in a subprocess because import order is the thing under test.
    """
    (tmp_path / ".env").write_text("OLLAMA_MODEL=sentinel:99b\nOLLAMA_HOST=http://sentinel:1234\n")
    probe = "from gncl import llm; print(llm.DEFAULT_MODEL, llm.DEFAULT_HOST)"
    # A clean environment: this pytest process already has OLLAMA_MODEL loaded
    # from the repo's own .env, the child would inherit it, and load_dotenv does
    # not override what is already set -- so the test would pass on the parent's
    # value while proving nothing about the child's load order.
    env = {k: v for k, v in os.environ.items() if k not in config.KNOWN}
    out = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.split() == ["sentinel:99b", "http://sentinel:1234"]


def test_config_is_the_only_module_reading_the_environment():
    """One load point, or the import-order bug comes back somewhere else.

    Asserts on the *reads*, not on the variable names: those legitimately appear
    in error messages telling the user which variable to set.
    """
    hits = subprocess.run(
        ["git", "grep", "-n", "-E", r"os\.(environ|getenv)", "--", "gncl/"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    outside = [h for h in hits if not h.startswith("gncl/config.py")]
    assert not outside, f"reads the environment outside gncl/config.py: {outside}"


def test_threshold_default_survives_a_reload(monkeypatch):
    """Values are functions, not constants, so a reloaded module sees the change."""
    monkeypatch.setenv("GNCL_THRESHOLD", "0.42")
    assert importlib.reload(config).threshold() == 0.42
