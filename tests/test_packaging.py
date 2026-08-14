"""Packaging and deployment hygiene.

Asserts the things a reviewer hits before any of the pipeline runs: that a clean
clone carries what it needs, that the documented commands work as written, and
that the image serves without shipping a secret or running as root.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PATTERN = r"(AKIA|sk-[A-Za-z0-9]{20}|-----BEGIN [A-Z ]*PRIVATE KEY)"


def test_compose_binds_api_to_loopback_only():
    """Basic-auth credentials must not cross a public interface in plaintext."""
    compose = (REPO / "docker-compose.yml").read_text()
    assert '"127.0.0.1:8000:8000"' in compose
    assert '"8000:8000"' not in compose


def test_compose_requires_credentials():
    compose = (REPO / "docker-compose.yml").read_text()
    assert "GNCL_AUTH_USER:?" in compose
    assert "GNCL_AUTH_PASSWORD:?" in compose


def test_dockerfile_runs_as_non_root():
    """Parses the final CMD, not the first: HEALTHCHECK also contains 'CMD'."""
    lines = [ln.strip() for ln in (REPO / "Dockerfile").read_text().splitlines()]
    user_idx = max(i for i, ln in enumerate(lines) if ln.startswith("USER "))
    cmd_idx = max(i for i, ln in enumerate(lines) if ln.startswith("CMD "))
    assert lines[user_idx] == "USER gncl"
    assert user_idx < cmd_idx


def test_no_secrets_committed():
    assert (
        subprocess.run(
            ["git", "check-ignore", ".env"], cwd=REPO, capture_output=True, check=False
        ).returncode
        == 0
    ), ".env is not gitignored"
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    # Only this .env* file may be tracked. It is reviewed and contains no live
    # credential; anything else starting with .env is an accident.
    allowed = {".env-example"}
    assert not [t for t in tracked if t.startswith(".env") and t not in allowed]
    blob = subprocess.run(
        # Excludes this file: it contains the pattern literal itself.
        ["git", "grep", "-IE", PATTERN, "--", ".", ":!tests/test_packaging.py"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert not blob.strip(), f"credential pattern in tracked files:\n{blob}"


def test_readme_quickstart_dependencies_are_installable():
    """`uv sync --group dev` must be enough to run everything the README shows.

    `uv run` re-syncs to the default environment, so extras not in the dev group
    vanish and the API tests fail at collection. That broke the first command a
    reviewer runs.
    """
    pyproject = (REPO / "pyproject.toml").read_text()
    dev_block = pyproject[pyproject.index("dev = [") :]
    dev_block = dev_block[: dev_block.index("]")]
    for module in ("fastapi", "uvicorn", "ollama", "httpx2"):
        assert module in dev_block, f"{module} missing from the dev group"
    import fastapi  # noqa: F401
    import ollama  # noqa: F401


def test_env_example_works_as_shipped():
    """`cp .env-example .env` must produce a runnable config.

    A worked example that does not work is worse than none: it costs the
    reviewer time before they reach any of the actual work.
    """
    example = (REPO / ".env-example").read_text()
    values = dict(
        line.split("=", 1)
        for line in example.splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    )
    assert values["GNCL_AUTH_USER"], "demo user must be filled in, not blank"
    assert values["GNCL_AUTH_PASSWORD"], "demo password must be filled in, not blank"
    assert not values["ANTHROPIC_API_KEY"], "no live key may be committed"
    assert 0.0 <= float(values["GNCL_THRESHOLD"]) <= 1.0
    assert int(values["GNCL_AUTH_FAIL_PER_MIN"]) < int(values["GNCL_RATE_LIMIT_PER_MIN"])


def test_env_example_password_is_self_evidently_not_for_deployment():
    values = (REPO / ".env-example").read_text()
    assert "change-me" in values.lower() or "demo" in values.lower()


def test_env_itself_is_never_tracked():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    assert ".env" not in tracked
    assert ".env-example" in tracked


def test_python_version_matches_requires_python():
    pinned = (REPO / ".python-version").read_text().strip()
    pyproject = (REPO / "pyproject.toml").read_text()
    assert f'requires-python = ">={pinned}"' in pyproject, (
        f".python-version says {pinned}; pyproject must agree"
    )


def _docker_ready() -> bool:
    """A reachable daemon, not just the client.

    `docker compose config` parses the file with no daemon at all, which is why
    the config-level assertions below are not enough on their own.
    """
    try:
        return (
            subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=False)
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


requires_docker = pytest.mark.skipif(not _docker_ready(), reason="no reachable docker daemon")

# The documented path is `cp .env-example .env`, so the tests read the same file
# a reviewer copies rather than inventing their own environment.
COMPOSE = ["docker", "compose", "--env-file", ".env-example"]


def _services(*flags: str) -> list[str]:
    r = subprocess.run(
        [*COMPOSE, *flags, "config", "--services"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(r.stdout.split())


@requires_docker
def test_documented_up_starts_exactly_the_shore_service():
    """`docker compose up` must start one container, and it must be the shore one.

    Compose has no default-profile mechanism. Every service here carries a
    profile, so with nothing selecting one `up` matched no service and exited 0
    having started nothing -- silently, while README.md offered it as the way to
    serve. `COMPOSE_PROFILES` in .env-example is what selects shore.
    """
    assert _services() == ["api"]


@requires_docker
def test_each_profile_starts_only_its_own_service():
    """api-vessel inherited [shore] from api through `extends`.

    `extends` merges the parent's `profiles` list into the child, so the vessel
    service resolved to [shore, vessel] and `--profile shore up` started the 4G
    vessel container next to the 1G shore one. The services share a YAML anchor
    now, which merges only what is written.
    """
    assert _services("--profile", "shore") == ["api"]
    assert _services("--profile", "vessel") == ["api-vessel"]


@requires_docker
def test_vessel_service_gets_no_hosted_api_key():
    """No hosted-API key may be injected into a container that runs at sea.

    Read from the resolved config rather than the file text, so nothing can
    arrive through the shared base without the assertion seeing it.
    """
    r = subprocess.run(
        [*COMPOSE, "--profile", "vessel", "config", "--format", "json"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    env = json.loads(r.stdout)["services"]["api-vessel"]["environment"]
    assert "ANTHROPIC_API_KEY" not in env
    assert env["GNCL_PROFILE"] == "vessel"
    assert "OLLAMA_HOST" in env


@requires_docker
def test_compose_container_starts_and_reports_healthy(tmp_path):
    """The only test in this file that would have caught #23.

    `read_only: true` met a CMD of `uv run`, which wants a writable
    $HOME/.cache/uv. The container crash-looped behind `restart: unless-stopped`
    and `/health` returned 000. Every text assertion stayed green through it:
    the file did contain `read_only`, and USER did precede CMD.

    Published ports are dropped so this cannot collide with a stack the
    developer already has on :8000. The image's own HEALTHCHECK requests
    /health from inside the container, so a healthy report still proves it
    serves.
    """
    override = tmp_path / "no-published-ports.yml"
    override.write_text("services:\n  api:\n    ports: !override []\n")
    # The project directory comes from the first -f, so it stays the repo.
    base = [*COMPOSE, "-f", "docker-compose.yml", "-f", str(override), "-p", "gncl-selftest"]
    try:
        subprocess.run(
            [*base, "up", "-d", "--build"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
            timeout=900,
        )
        deadline = time.monotonic() + 180
        state: dict = {}
        while time.monotonic() < deadline:
            ps = subprocess.run(
                [*base, "ps", "--format", "json"],
                cwd=REPO,
                capture_output=True,
                text=True,
                check=True,
            )
            rows = [json.loads(line) for line in ps.stdout.splitlines() if line.strip()]
            state = rows[0] if rows else {}
            if state.get("Health") in {"healthy", "unhealthy"}:
                break
            assert state.get("State") != "restarting", f"crash loop: {state}"
            time.sleep(2)
        assert state.get("State") == "running", f"not running: {state}"
        assert state.get("Health") == "healthy", f"not healthy: {state}"

        # The image must carry the audit dependency, not just the API one. Built
        # with `--extra api` alone, `ollama` was absent: `available()` still
        # passed its urllib probe against a reachable server, then every verdict
        # degraded to "not run" on an ImportError. Silent, and only inside the
        # container, so no host-side test and no config assertion could see it --
        # the deployed artifact could not satisfy case requirement 3 at all.
        # `/app/.venv/bin/python`, not `python`: the latter is the base image's
        # interpreter, which never has the project's dependencies and would fail
        # this assertion even when the image is correct.
        imported = subprocess.run(
            [*base, "exec", "-T", "api", "/app/.venv/bin/python", "-c", "import ollama"],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        assert imported.returncode == 0, f"ollama missing from the venv: {imported.stderr}"
    finally:
        subprocess.run(
            [*base, "down", "-v", "--remove-orphans"], cwd=REPO, capture_output=True, check=False
        )


def test_files_a_reviewer_needs_are_actually_tracked():
    """The author's global gitignore ignores `pyproject.toml`.

    It was therefore never committed, and `uv sync --group dev` -- the first
    command in the README -- failed on a clean clone. CI caught it; nothing
    local would have, because the file exists on this machine.

    Any file the project cannot be built or run without belongs here.
    """
    tracked = set(
        subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.split()
    )
    required = {
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        "README.md",
        ".env-example",
        ".github/workflows/ci.yml",
        ".pre-commit-config.yaml",
        "case/bookit_guests.csv",
        "case/hubspot_contacts.csv",
        "case/ls_retail_transactions.csv",
    }
    missing = sorted(required - tracked)
    assert not missing, f"present locally but not committed: {missing}"


def test_no_source_file_is_silently_ignored():
    """A gitignore rule must never swallow a module or a test."""
    source = {
        p.relative_to(REPO).as_posix()
        for d in ("gncl", "tests")
        for p in (REPO / d).rglob("*.py")
        if "__pycache__" not in p.parts
    }
    tracked = set(
        subprocess.run(
            ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.split()
    )
    assert not sorted(source - tracked), f"untracked source: {sorted(source - tracked)}"
