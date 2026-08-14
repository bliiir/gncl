# GNCL Guest Identity Resolution - Specification

Design record. Written before the build, corrected against it afterwards. Where this document and the code disagreed, the code won and this document changed.

- **Status** - the four case requirements are met. API built, browser page carries GNCL's design, chat tab served from `GET /`. Deployment is prepared and unprovisioned: compose and EC2 notes written, nothing stood up
- **Measurements** live in `docs/DATA_ANALYSIS.md` (signals) and `docs/MODEL_EVAL.md` (models). Both regenerate from the data and fail a test if they go stale. This document does not restate their numbers
- Case brief - `case/GNCL_AI_Engineer_Case.md`

## 1. Goal

One guest identity across three systems that share no key - BookIT (bookings), LS Retail (onboard POS), HubSpot (CRM). One row per real guest with source IDs, a confidence score, the method that produced the match, and a readable reason.

The case asks for calibrated confidence and honest gaps. A system that leaves a record unjoined when there is no evidence scores higher than one that guesses.

| Source | Rows | Gaps |
|---|---|---|
| `bookit_guests.csv` | 32 | 6 missing email, 1 missing cabin |
| `hubspot_contacts.csv` | 31 | 7 missing email; no cabin or stay dates at all |
| `ls_retail_transactions.csv` | 57 | 4 missing cabin; first names only |

Expected output: 35 guests (32 bookings + 3 HubSpot-only contacts).

## 2. The traps that shaped the design

### 2.1 Cabin is not a key

Cabin 6208 holds Nils Berg (2026-06-28 to 07-05) and Marcus Berg (07-19 to 07-24). Join on cabin alone and two people merge.

Cabin **and** date-in-window is required, and the window must be closed at both ends:

- 14 transactions fall on a boundary of the booking they attach to
- 8 on check-in, 6 on check-out, none on both
- Half-open `[checkin, checkout)` silently drops the 6

### 2.2 Duplicate names require one-to-one assignment

BookIT holds two distinct Anna Larsens. HubSpot holds two Anna Larsen rows. Name similarity scores 1.00 against both candidates, so all four cross-edges exist in the candidate set.

Email locks BK1001 to HS201. That leaves BK1021 to HS221 by elimination with no positive evidence of its own. It carries 0.55 and lands in the review queue.

Load-bearing assumption: no guest booked twice. The two bookings differ in nationality (DK, SE), have non-overlapping dates, and HubSpot independently holds two rows, so two people is the parsimonious reading. Called out in the README because if it is wrong, two output rows are wrong.

### 2.3 String similarity cannot separate variants from different people

An earlier version of this section proved this with a table that mixed two metrics - first-name ratios for the positives, full-string ratios for the negatives. The conclusion did not survive the correction.

Under the metric the pipeline actually uses (first name against first name, `gncl/match.py`), a threshold at 0.55 **does** separate every pair in this dataset:

| Pair | First-name ratio | Truth |
|---|---|---|
| `beth` / `elisabeth` | 0.62 | same person |
| `andy` / `anders` | 0.60 | same person |
| `marcus` / `thomas` | 0.50 | different people |
| `nils` / `marcus` | 0.20 | different people |

So the local argument fails. The real argument is generalisation:

- Common diminutives sit far below that closest negative - `peggy`/`margaret` 0.15, `jack`/`john` 0.25, `dick`/`richard` 0.36
- Swedish short forms interleave outright - `lasse`/`lars`, `kalle`/`karl` and `nisse`/`nils` at 0.67 and the same person, `stina`/`stig` at 0.67 and not

No cut survives that. This is the justification for a model, and it is a claim about the population GNCL is heading for, not about this sample. `gncl/eval.py` scores both sets.

### 2.4 Name substring matching

`TX5036` ("Fredrik", no cabin) substring-matches both **Fredrik** Lund and Tone **Fredriksen**. Only the date window eliminates the false candidate.

### 2.5 Negative controls

These must come out `single source`. Forcing them is the failure mode the case tests for.

- HubSpot-only leads who never booked - Oskar Lindberg, Pernille Rasmussen, Aleksander Hoff
- BookIT-only with no CRM record - Signe Mortensen, Kjell Bakken, Ida Truelsen, Ragnhild Vik

## 3. Matching architecture

A cascade on a graph. Records are nodes, evidence links are weighted edges, a resolved guest is a connected component. Each layer sees only the residual above it, so cheap logic runs first and the model is never asked what a rule knows.

```
L0  normalize        casefold, explicit Scandinavian folding, closed date windows
L1  deterministic    exact email; cabin + stay window
L2  rule / fuzzy     repaired-name candidates, resolved by max-weight matching
L3  audit            model checks links whose name evidence contradicts them
L4  single source    no forced pairing
```

L0 note. NFKD does not decompose `ø` or `æ`, so `Bjorn` and `Sorensen` never normalize, and `å` decomposes to `a` instead of the conventional `aa`. Scandinavian folding needs an explicit map. `unicodedata` alone is not enough. It happens not to bite here because both systems spell the names identically. At scale it is a silent failure.

The BookIT-to-HubSpot layer is one max-weight bipartite matching (`nx.max_weight_matching`, `gncl/match.py:138`). Greedy takes the strongest link first and never reconsiders. `maxcardinality=False` is deliberate - forcing maximum cardinality would pair the 4 residual BookIT records with the 3 residual HubSpot ones despite no shared evidence.

### 3.1 Confidence

| Method | Band | Source of score |
|---|---|---|
| `deterministic` | 0.95 - 0.98 | fixed per rule: email 0.98, cabin+window 0.95 |
| `rule_fuzzy` | 0.55 - 0.90 | fixed per rule: unique name 0.90, name+window 0.80, ambiguous name 0.55 |
| `single source` | 0.0 | n/a |

Weights are fixed per evidence type. They are not similarity ratios.

There is no `llm_audited` band and no `llm_audited` method. An earlier draft specified one and the code deliberately does not implement it. This is a statement about the `match_method` column and says nothing about the audit - the audit runs, against a local model through Ollama, and reports in its own `audit` column (section 5).

A rule makes every link. A model agreeing does not make it a model's match. Folding the audit into the method relabelled email-locked guests as LLM-matched and made the headline figures depend on whether a model happened to be reachable. Only a contradiction moves confidence, capping it at 0.50.

A guest scores the **weakest** edge in its component. That is what makes the review queue surface components held together by one weak edge.

A `single source` guest is accepted whatever the threshold. Its 0.0 is the absence of a link rather than doubt about one, and the queue is for adjudicating joins. A lone HubSpot lead gives a reviewer nothing to compare against, so the task would be second-guessing HubSpot's own record.

Banding on confidence alone put 5 of the 6 flagged rows in the queue and turned the review count into a measure of how many guests appear in one system. `match_method` records that instead. `review_band` in `gncl/join.py` is the single definition - `/guests`, `/review-queue` and the CLI summary each used to re-derive it from `match_confidence` and now call it.

Scores are computed once and stored. A threshold is a filter over stored scores, never a pipeline re-run.

## 4. Output

`gncl resolve` writes one file, `guests.csv`, holding all 35 guests and 37 columns. The accept decision is a `review` column rather than a choice of file. Encoding it in a filename means a threshold change redistributes rows between files instead of rewriting one column, and it cannot be grouped on.

It was two files, minimal and full, until the same argument was applied one level up. Two files holding the same 35 rows made every consumer choose, and a column added to one did not appear in the other. The narrow table is now the first 13 columns of the wide one. A test pins that prefix so it cannot drift.

Column order is the argument, left to right:

| Columns | Block | Why there |
|---|---|---|
| 1-13 | identity and decision | read alone, this is the whole answer |
| 14 | `evidence` | the readable reason, beside the decision it explains |
| 15-18 | `name_source`, `audit`, `audit_confidence`, `audit_model` | why that decision reads as it does |
| 19-22 | `crm_link_*`, `txn_weight_min/max` | `match_confidence` is the minimum of these |
| 23-24 | `transaction_count`, `total_spend` | the guest, not the match |
| 25 | `sources` | names which blocks below carry values |
| 26-37 | `bookit_*`, `hubspot_*`, `ls_retail_*` | raw, in the order `sources` names them |

```
match_method   deterministic | rule_fuzzy | single source
review         accepted | review
audit          confirmed | contradicted | not run | none (no conflict raised)
name_source    as recorded | repaired from email local part
```

Two kinds of source column are dropped:

- `DERIVED` - the pipeline's own, not the source system's. A second `cabin` beside `cabin_number` invites reading the wrong one
- `COPIES` - the seven columns a resolved column reproduces exactly. The three join keys, plus BookIT's cabin, stay dates and nationality, which no other system supplies

Both were verified against all 35 rows and a test re-checks it. A dataset where BookIT's nationality starts disagreeing with the resolved one fails loudly instead of quietly serving one of two answers.

## 5. LLM usage

The pipeline role is audit, not adjudication. Rules do the matching. The model checks links whose name evidence contradicts them and appends its reason to that row's evidence.

Measured volume on this dataset is 2 guests across 4 transactions. First estimated at 10 to 15. It is also not the residual, which has maximum cross-similarity 0.47 and correctly resolves to `single source` by rule.

### 5.1 Why hosted in the UI and local in the pipeline

The deciding axis is call volume, cost per call, PII exposure and task width. Capability does not enter into it.

| | Chat tab | Link audit |
|---|---|---|
| Calls | tens, human-paced | a fixed fraction of links; hundreds of thousands at millions of records |
| Cost sensitivity | negligible | dominates the run |
| Task width | open-ended | one narrow judgement against a fixed schema |
| Data per call | already-resolved rows | two real guest names, every call |
| Latency | interactive | batchable, offline |

Chat cost is flat in dataset size. Audit cost is linear in it. Put the expensive model where the cost is flat.

This is a deployment choice. The same image runs shore and vessel, and the vessel profile assumes no egress.

### 5.2 Model selection, and the mechanism that nearly wasn't there

The shortlist came from EuroEval (formerly ScandEval), snapshot 2026-04-17, whose `dansk` task is NER micro-F1. That is a proxy for name handling, not a person-name score. It selected for Scandinavian morphology while the judgements the pipeline actually raises are English diminutives. It picked which models to try and decides nothing now - `docs/MODEL_EVAL.md` scores them on the task.

Result: `gemma4:12b` and `gemma4:26b` both score 14/15 on the traps and 14/17 on the Swedish set. The 2.2x download buys nothing measurable, so the smaller model is the default on size alone.

**The `-mlx` advice was wrong, and wrong silently.** Ollama's MLX runtime accepts the `format` parameter and ignores it. `gemma4:12b-mlx` returns free prose to a call carrying a JSON schema, byte-identical to the same call with `format="json"` and to one with no `format` at all. No error, no status code.

Constrained decoding is the mechanism this section rests on, so on MLX the layer had no mechanism. It scores 7/15 and 8/17, exactly the `always no` baseline, because every malformed response is scored as a `False` verdict. The GGUF build of the same weights scores 14/15. Quantisation format is a correctness property here.

Call convention: `format: <schema>`, `think: false`, `temperature: 0`, long `keep_alive`, one call per distinct name pair.

Degradation is visible. An unavailable verdict is `same_person=False` at confidence 0.0 and is marked unusable, so a transport failure cannot read as a confirmation. `Verdict.malformed` is what stood between the MLX failure and eight silent decode errors being read as eight confident denials.

## 6. API

FastAPI. Every endpoint except `/health` is behind basic auth. `/health` is open by design so a container healthcheck needs no credentials. All endpoints, including `/health`, are rate limited. Failed logins burn a separate and much tighter budget than successful requests. Schema routes are disabled.

```
GET  /health                        open
GET  /guests?threshold=              authenticated
GET  /guests/{guest_id}              with its evidence chain
GET  /review-queue
GET  /raw/{source}                   bookit | hubspot | ls_retail
GET  /stats
GET  /                               the browser view, chat enabled
POST /chat                           grounded answer over the output and docs
```

`threshold` filters stored scores and does not re-run matching. The endpoints serialise the whole frame per request and have no pagination, which is fine at 35 rows and wrong at scale.

`POST /chat` sends the output CSV, the three raw sources, the case brief and `docs/` to a hosted model on every call. About 63 KB, no retrieval step, because a vector store over 63 KB is theatre. Aggregates are computed in pandas and passed in as authoritative facts, so the model quotes counts instead of deriving them from 35 CSV rows, which it will do confidently and wrongly.

`usable: false` means no answer was produced and the payload says why. That is a 200, because "no model configured" is a state of the system rather than a failure of the request.

Conversations are multi-turn. The browser holds the transcript and posts it back with each question, so the server stays stateless and a second worker answers identically. History is validated as complete alternating exchanges and capped at 20 turns. The evidence pack rides on the first message of a conversation, so ten turns cost about what one costs.

The key stays server-side. The browser posts a question to `/chat` and nothing renders a credential into the page. That is why the chat tab is served from `GET /` rather than built into the standalone file `gncl ui` writes.

**Not built:** `POST /adjudicate`. It appeared in an earlier draft of this section as though it existed.

## 7. UI

`gncl ui` writes one self-contained page. `GET /` serves the same document with the chat tab live.

The visual design is GNCL's, ported from the brand bundle - palette, the four-tier badge scale and its legend, stat tiles carrying the headline split, a sticky identifier column, the core/all column toggle, header wordmark and footer.

What the bundle assumed and this page cannot have was replaced:

- Bundle ships a component runtime, fetches the CSV over HTTP, links Google Fonts
- Page inlines its own markup, embeds the CSV in the download link, holds the design's three type roles (serif for headings and figures, sans for the interface, mono for identifiers) with system stacks

It renders with no network, because the vessel profile assumes no egress. A test checks for subresources rather than for the letters http, so the inlined wordmark's `xmlns` and the footer link both pass while a linked font would not.

The default view is 17 of the 37 columns. The design's core set left out the source ids. They are back in, because "one row per real guest with the corresponding IDs from each source" is the first case requirement, and a page that hides them makes a reviewer press a button to see the deliverable.

Still absent: adjudication. Rows are flagged and nothing happens to them, which is section 10's first entry.

One behavioural constraint, enforced by a test. `single source` must not render as an error state. A single-source row is a correct, honest outcome, and colouring it as failure contradicts the central claim of the project. It is amber rather than neutral, because a guest resting on one system's word is worth noticing.

## 8. Deployment

Build deploy-ready, provision later. Nothing has been provisioned and no AWS account has been touched.

Security:

- Basic auth with a single shared credential, sent out of band
- Secrets in a gitignored `.env`, with `.env-example` committed as a worked example
- A spend-capped hosted key
- Port 8000 bound to loopback, TLS terminator expected in front

The rate limit keys on the connecting address, which behind a terminator is the terminator. So it is effectively global unless uvicorn is given `--forwarded-allow-ips`, and it is in-memory per-process. See `deploy/README.md`.

## 9. Scaling to millions of records

- **Block before comparing** - all-pairs is O(n^2). Repaired-name plus email blocking gives 100% recall at 97% reduction on the known pairs. Measured, and not yet wired into the matcher: `bookit_hubspot_candidates` still compares every BookIT row to every HubSpot row. 992 pairs at this size, 10^12 at a million a side
- **Match inside blocks** - `nx.max_weight_matching` is O(n^3) over the whole graph. Once blocking is in, run it per block instead
- **Deterministic layers become SQL or Spark** - the cascade shape does not change, only L1 and L2 execution
- **Audit cost scales with ambiguity** - verdicts cache on the normalized name pair; 4 transactions already collapse to 2 calls here
- **Score once, threshold at read time** - already the design

### 9.1 What the graph formulation buys

Adopted already, at 35 records, because it is a better implementation of the assignment now. It is not a future migration.

- Incremental resolution becomes local - a new record blocks to candidates, adds edges, recomputes one component, no full re-run
- The threshold slider becomes "traverse edges with weight >= t, take components", so the UI control and the data model are the same operation
- Evidence becomes a queryable edge property, making "every component held together by a single weak edge" a query instead of a report
- Cabin 6208 is an edge valid only within an interval, which is first-class in a graph and an implicit join condition everywhere else

**The failure mode to design against is transitive closure.** If A links B and B links C, the component merges A and C on no shared evidence and every Berg becomes one guest.

I specified three guards. Only one earns its keep.

**No guest holds two bookings.** Valid, and the one actually catching bad merges. A test asserts it.

**A minimum confidence score.** Debunked. `MIN_EDGE_WEIGHT` was 0.50, but the lowest weight the candidate builder can emit is `W_NAME_AMBIGUOUS` at 0.55, so the floor never rejected an edge. Removed rather than left in place - a guard that cannot fire reads as protection the graph does not have, and it would have been cited as one. Introducing a weight below 0.55 means choosing a floor then, against that case, instead of inheriting a number picked for a different one.

**Two people in different cabins at the same time are not one person.** The must-not-link predicate, and the reason it stays quiet was wrong in an earlier draft. That draft said it never fires because HubSpot carries neither cabin nor stay dates. Measuring the graph says otherwise: every component holds at most one BookIT record (32 hold one, the 3 CRM-only leads hold none), because the BookIT-to-HubSpot layer is a 1:1 matching and transactions attach as leaves. No component holds two records that both carry a cabin and a stay, so the guard is unreachable through the shape of the graph rather than through a missing column. It becomes reachable the moment a source can put two stays under one guest.

It is a property of a finished component, not of a candidate edge, so `closure_violations` runs it after closure and reports contradictions rather than choosing which edge to cut. `gncl resolve` prints nothing when the invariant holds. `tests/test_match.py` exercises it against a component contaminated by hand, because the cascade never builds one.

Database choice is a later question and not automatically a graph database. If the workload stays bipartite and blocking works, Spark GraphFrames over a columnar store may win on cost. This dataset cannot settle it - nothing here needs more than two hops.

### 9.2 Tiered model routing - considered, not built

Embeddings first, then a small local generative model, then a frontier model for the hard tail plus a periodic drift audit on a sample.

The precondition is that each tier can **abstain**. A tier that always answers filters nothing and adds a hop, and calibrating those bands is an eval problem instead of a constant to pick. A tier earns its place only if it cuts the volume reaching the next by roughly an order of magnitude.

**Explicitly unvalidated.** None of this is measurable at 35 records. It is recorded as the shape of the answer, labelled as reasoning rather than result.

## 10. Out of scope, and what is unverified

- Not a production system - no audit log, no GDPR erasure path, no observability, batch re-resolve only
- **The rule layer has no accuracy figure.** There is no ground-truth file, so the 28 cross-source links, 35 components and 57 attachments have no measured precision or recall. Tests pin them against the author's reading of the data
- Match quality for the model is measured on 32 labelled pairs across two sets (15 traps, 17 Swedish short forms). Too small for strong claims - one case is 7 and 6 percentage points
- The Swedish gold labels are the author's own and are not independently adjudicated, so "the model over-merges near-miss surnames" is the author's reading of a disagreement
- Nothing is deployed. The vessel profile is untested against a real vessel network. Scaling figures are projections
