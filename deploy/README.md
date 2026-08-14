# Deploy

Build-ready, not provisioned. No AWS account has been touched.

Every row below was measured against `docker compose up` itself, on the file in
this repo, with `cp .env-example .env` first. The previous version of this table
was measured against `docker run` on the image directly, before `read_only: true`
was added to compose, and was never re-verified against the path the README
actually documents. Two defects lived in that gap, so the rule now is that
a row here describes the documented command or it does not appear.

| Check | Result |
|---|---|
| `docker build` | succeeds |
| `docker compose up -d` with no profile flag | starts `api` alone |
| container status 15s after start | `running`, `healthy` |
| link audit inside the container | runs; `G-BK1013` returns `audit: confirmed` with the model's reason in the evidence |
| `GET /health` without credentials | 200 |
| `GET /stats` without credentials | 401 |
| `GET /stats` with a wrong password | 401 |
| `GET /stats` with correct credentials | 200, payload matches the CLI exactly |
| container user | `uid=10001(gncl)`, non-root |
| `GET /docs` with correct credentials | 404, schema routes are off |
| image run directly with no credentials, request *with* credentials | 503 |
| image run directly with no credentials, request with *no* `Authorization` header | 401 |

`tests/test_packaging.py::test_compose_container_starts_and_reports_healthy`
starts the stack and asserts the health state, so this table has a test behind
it rather than a memory of having run it once.

## Local

```
cp .env-example .env           # demo credentials, runs as-is
docker compose up --build
set -a && . ./.env && set +a          # .env is not exported by cp alone
curl -u "$GNCL_AUTH_USER:$GNCL_AUTH_PASSWORD" localhost:8000/stats

# the browser view, with the chat tab live if ANTHROPIC_API_KEY is set
open http://localhost:8000/
```

**Compose** refuses to start without both credentials, via the `:?` operator on
each variable. Neither the image nor the API enforces them: `docker run` on the
image directly starts without them and answers 401 to unauthenticated probes and
503 to authenticated ones, while `/health` stays 200 throughout. Only `gncl
serve` checks at startup. Use compose, or set the variables explicitly. The
`Dockerfile` comment claiming the API refuses to start without them was wrong
and is corrected in place.

## Two profiles

The same image runs in two places with different constraints, so the
hosted-versus-local split is a deployment choice rather than an architectural
one.

```
docker compose                  up   # shore, the default
docker compose --profile vessel up   # at sea: local model, no egress assumed
```

Shore is the default because `.env-example` sets `COMPOSE_PROFILES=shore`. Compose has no default-profile mechanism of its own,
and every service here carries a profile, so without that entry `docker compose
up` matches no service and exits 0 having started nothing. A `--profile` flag
replaces the value rather than adding to it, so the vessel line starts the
vessel container alone.

The two services share a base through a YAML anchor rather than `extends`.
`extends` merges the parent's `profiles` list into the child, which gave
`api-vessel` the profiles `[shore, vessel]` and started the 4G vessel container
next to the 1G shore one on `--profile shore up`.

| | Shore (HQ, customer-facing) | Vessel |
|---|---|---|
| Connectivity | full | intermittent, metered, must survive none |
| Chat backend | hosted frontier model | local model, or degrade to the deterministic views |
| Audit backend | local model | local model |
| Guest PII | leaves the building to a vendor | never leaves the ship |
| Memory | 1G | 4G, the model is resident |

The vessel profile assumes no egress: nothing in the pipeline path makes an
external call, and the container reads local files and writes local output. A
test asserts the resolved vessel config carries no `ANTHROPIC_API_KEY`, read
from `docker compose config` rather than the file text, so nothing can arrive
through the shared base unseen.

**Reaching the model from a container** took two fixes, both of which failed
silently rather than loudly:

- `OLLAMA_HOST` in `.env` is the CLI's, and points at `localhost`. Compose used
  to inherit it, which inside a container means the container itself. Compose
  now reads `OLLAMA_HOST_IN_CONTAINER`, defaulting to `host.docker.internal`.
- The image was built with `--extra api` only, so the `ollama` package was
  absent. `available()` probes with `urllib` and passed regardless, then every
  verdict degraded to `not run` on an `ImportError`. The image now installs
  `--extra llm` too, and a test asserts `import ollama` succeeds inside the
  running container.

`extra_hosts` maps `host.docker.internal` to the host gateway. Docker Desktop
provides that name on its own; Linux does not, and Linux is what both EC2 and
the vessel run, so without it the audit host is unresolvable precisely where it
is deployed.

**Untested against a real vessel network.** This is reasoning about a deployment
target, not a measured result, and belongs with the other unvalidated scaling
claims.

## EC2

t3.small is enough. The chat tab runs on a hosted API, so the box needs no GPU
and no large RAM.

```
# on the instance
sudo dnf install -y docker git && sudo systemctl enable --now docker
git clone git@github.com:bliiir/gncl.git && cd gncl
cp .env-example .env && $EDITOR .env    # change the demo credentials
sudo docker compose up -d --build
```

Port 8000 is bound to loopback in `docker-compose.yml`. Put a TLS terminator
(Caddy, nginx, or an ALB) in front before exposing it. Basic-auth credentials
over plaintext on a public interface would leak on first use.

Security group: 443 from the reviewer's address if known, otherwise 443 from
anywhere. Never open 8000.

Do not rely on the rate limit as the only control from an open source range.
It is in-memory and per-process, and it keys on `request.client.host`, which
behind a terminator is the terminator. Unless uvicorn is started with
`--forwarded-allow-ips` set to the terminator's address, the limit is
**global**, not per-IP: one client can exhaust it for everyone. Trusting
`X-Forwarded-For` without that setting would make the key attacker-controlled
and the limit evadable, which is worse.

## What is not here

- No Terraform or CloudFormation. Provisioning is manual and documented.
- Ship-to-shore sync is not built. A vessel resolves locally and shore has to
  receive that eventually; see the README's Future work section for the open
  questions.
- No TLS config; the terminator choice depends on whether a domain exists.
- No CI/CD. The image builds locally and is pushed by hand.
- Rate limiting is per-process and in-memory. Behind more than one replica it
  under-counts, and a real deployment needs a shared store.
- Base images are pinned by tag, not digest, so a rebuild can pick up upstream
  changes silently.
- Single build stage: `uv` and the build tooling remain in the runtime image.
