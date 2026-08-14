# GNCL guest identity resolution

> Who IS Anna Larsen? 🤔

Resolve unique Go-Nordic guests across three IT systems that do not share a key:

- BookIT (bookings)
- LS Retail (POS)
- HubSpot (CRM).

See detailed analysis and specification in the docs folder. All case data is confirmed synthetic.

The `resolve` command produces a table in the `out` folder that can be read independently or via the `ui` and `serve` commands.

Each row in the output table carries the source IDs found, a confidence score, the method that produced it, and a readable reason.

---

Before I started I wanted to understand the traps and the anchors in the data:

### Observations
- Cabin+dates+other can be used as key. Cabin is not a key in itself - same cabin can be occupied at different times
- Surname clusters sit in adjacent cabins. I assumed families travelling together and checked - no two same-surname bookings overlap in time, and no two adjacent cabins overlap at all
- POS records first name only, sometimes a nickname (Beth, Andy)

### Traps
- Nationality does not match phone country code - only 7 of 28 guests would pass. Measured across the known pairs it carries no signal either way (p=0.13 at n=21), so it stays out of the matching
- Two Anna Larsen - cabin differs by one digit - seems like deliberate trap and a main talking point
- Same cabin, same surname, two guests, different time window (Berg)
- Emails go blank on the ambiguous rows
- Hubspot and BookIT misspell the same name, differently
- Every planted collision is also in adjacent cabins - Berg, Holm, Larsen. Kills adjacency as a way to disambiguate

### Matching Anchors

#### Deterministic
- Exact emails used
- Cabin + closed stay window

#### non-deterministic
- Unique repaired name
- Repaired first name + stay window
- Elimination - One of the Anna Larsen can be inferred just because all other stronger pairs are taken

### Matching approach
- Repair names before blocking; Camilla Strnad > Camilla Strand
- Blocking - only compare records that share a cheap key - ie email, full name, surname etc
- Strongest link != correct ( > Bipartite max-weight matching, not greedy) - fixes Anna Larsen

See `docs/DATA_ANALYSIS.md` for more detail

---

It took me 15 mins with Excel and an llm to produce the output csv, but my core assumption is that these findings reflect the data reality at GNCL, so it is not about these 35 individuals, but about how to approach the general problem with this tiny example, so, I chose to focus on the fastest, cheapest and most robust and scalable way of doing the joins:

#### Matching progression
```
L0  normalize      casefold, explicit Scandinavian folding, closed date windows
L1  deterministic  exact email; cabin + stay window
L2  rule / fuzzy   repaired-name candidates, resolved by max-weight matching
L3  audit          model checks links whose name evidence contradicts them. Local model first then manual review
L4  single source  no forced pairing
```

**Result**: 35 guests resolved from 120 records:


| match method | guests |
|---|---|
| deterministic | 20 |
| rule_fuzzy | 10 |
| single source | 5 |

| confidence | guests |
|---|---|
| accepted (>= 0.8) | 34 |
| flagged for review | 1 |


If there is only one record for a guest, I do not second-guess it. 5 guests appear in one system only so there is no join to judge. Example - reviewing G-HS229 would mean second-guessing HubSpot with nothing to compare against. They are accepted and labelled `single source`;

The one flagged guest is a join that is genuinely uncertain: *Anna Larsen*, International woman of mystery. Perhaps a spy?

Name conflicts: 4 transactions across 2 guests, 2 confirmed. That is the only line needing a model, and it is optional. Without one it reads `not run`, prints a warning, and nothing above it changes.

## Extrapolating the challenge
I read 120 rows and a container on a box as standing in for something wider: bi-directional integration, and an architecture the organisation can build on without every team solving identity, security and PII again.

A cook has already built a React app to upload the day's dinner bookings and plan seating. That is real demand showing up, and supporting it would be amazing.

## At millions of records
A few million rows on one machine is par-for-the-course. Pandas, Polars or DuckDB do it no problem. No need for Spark or similar.

- The audit caches on the normalized name pair, so cost tracks ambiguity instead of volume. 4 transactions collapse to 2 calls at this size
- Scores are computed once and stored. A threshold filters them, it never re-runs the pipeline

Two things in this implementation should be addressed before scaling up:

- `bookit_hubspot_candidates` compares every BookIT row to every HubSpot row. 992 pairs here, 10^12 at a million a side. Blocking is measured in `docs/DATA_ANALYSIS.md` and recommended there, and it is not wired into the matcher.
- `nx.max_weight_matching` is O(n^3) over the whole graph. Once blocking is in, run the matching inside each block instead. Blocks stay small, so cost goes linear in block count

## Where the LLM earns its place
- Why a model at all - no similarity threshold generalises. `lasse`/`lars` scores 0.67 and is the same person, `stina`/`stig` scores 0.67 and is not. No cut survives that
- Local model for the audit - a fixed fraction of links, millions of calls at scale, nobody waiting on it
- Hosted model for chat - tens of calls, human-paced, open-ended questions
- Rules make every link. The model only audits the ones whose name evidence contradicts them
- Built with an LLM harness. Not a single line of code was hand-written. This is an AI engineer role. It is about solving problems, not typing.

## Next / What I did not get to
- Blocking and weight matching
- Better data-science. This was naive data-science. Bigger datasets would allow us to slice it more reliably
- Not deployed - would be trivial to do so, but I thought it was overkill so decided not to
- Review process not implemented - would be cool to be able to update source systems from review
- Graph datalake - save the graph and make it queriable - feed back into Hubspot etc
- Bi directional data-integration - closed loop, resolve centrally and feed back to leaf providers
- Not a production system - No loggin, no GDPR consideration, security etc

See more in `docs/OVERVIEW.md`

---

## Setup

### Prerequisites

**uv** [installation instructions](https://docs.astral.sh/uv/getting-started/installation/)
```shell
uv sync --group dev         # Installs the python client and dependencies
uv sync --extra llm         # adds the anthropic client; only the chat tab needs it
```

### Ollama
Optional local model used only for the name audit. Everything else runs without it:

```shell
brew install ollama
```

or via [ollama.com](https://ollama.com/), then:

```shell
ollama serve                # skip if the desktop app is already running
ollama pull gemma4:12b      # 7.0 GB
```

The Ollama server (serve) and the weights (pull) are separate. Pull is a large download and takes a while.

## Running it

```
uv run gncl resolve         # out/guests.csv + out/graph.svg
uv run gncl show G-BK1021   # one guest with its evidence chain
uv run gncl graph --guest G-BK1021   # redraw one guest on its own
uv run gncl ui              # the tables in a browser
uv run gncl serve           # API + browser view on :8000, with the chat tab. get login info from the .env
uv run pytest               # ~40s; ~5 min with a local model reachable
```

**`gncl serve`** will serve a webpage on `http://localhost:8000/` and ask you to log in. Everything except `/health` sits behind basic auth, because an unauthenticated endpoint that reaches a hosted model is an open proxy and a cost risk. The credentials are `GNCL_AUTH_USER` and `GNCL_AUTH_PASSWORD` in your `.env` file.

If you did not get a .env file from me, you can use:

```
username: 'reviewer'
password: 'demo-only-change-me'
```

and run:
```
cp .env-example .env        # .env required. The example does not have ANTHROPIC_API_KEY so the chat tab renders empty
```

`gncl ui` writes a file instead of serving one, so it has no login at all.

---

