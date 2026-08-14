# Overview

Answers the four questions in the case brief. Numbers here come from the code and are pinned by tests.

## 1. Matching approach

Records are nodes. Evidence is a weighted edge. A resolved guest is a connected component. Cheap logic runs first so the model is never asked what a rule already knows.

```
L0  normalize      casefold, Scandinavian folding, closed date windows
L1  deterministic  exact email; cabin + stay window
L2  rule / fuzzy   repaired-name candidates, resolved by max-weight matching
L3  audit          model checks links whose name evidence contradicts them
L4  single source  no forced pairing
```

### Why cabin needs a date

- Cabin 6208 - Nils Berg Jun 28 to Jul 5, Marcus Berg Jul 19 to 24
- Join on cabin alone = two people merged
- Cabin + closed stay window resolves 53 of 57 transactions
- Other 4 have no cabin - fall back to first name + window at 0.80
- Window is closed at both ends. 14 transactions land on a check-in or check-out date, and 6 of those are lost if you write it half-open

### Why max-weight and not greedy

Two Anna Larsens in BookIT, two Anna Larsen rows in HubSpot. All four cross-edges are real, because the names really do match.

- Greedy takes the strongest link first and never reconsiders
- It gets Anna right only because email happens to run first
- Change the order or add a third Anna and it breaks
- Max-weight scores every combination and takes the best total

`maxcardinality=False` on purpose. Forcing every record to have a partner would marry the 4 leftover BookIT records to the 3 leftover HubSpot ones on no shared evidence. Best cross-similarity between them is 0.47.

### Confidence

Weakest link in a component, not the strongest. So the `review` flag surfaces guests held together by one weak edge.

That weakest rule (first name + window, 0.80) sets the confidence of 3 guests. The 34/1 split is knife-edge - score it 0.79 and it becomes 31/4.

### Signals were scored against a baseline

| Signal | True pairs | Base rate | Lift |
|---|---|---|---|
| marketing contact before check-in | 100% | 92.8% | 1.08x |
| phone country code vs nationality | 19% | 32.7% | 0.58x |

- 100% looks decisive. Then measure the base rate - nearly every marketing date precedes nearly every stay
- It was the only untested signal that could have split the two Anna Larsens
- Phone country code carries no measurable signal - 4 of 21 true pairs agree against a 32.7% base, binomial p=0.13. The reason to distrust it here is concrete rather than statistical: HS201 is +47 on a DK guest, and that pair is proven by email
- One signal gained: the email local part spells all four corrupted names correctly, so repairing before blocking lifts recall from 90% to 100% at the same 97% reduction

Detail in `docs/DATA_ANALYSIS.md`.

## 2. Where the LLM earns its place

No similarity threshold generalises:

- `peggy`/`margaret` 0.15, `jack`/`john` 0.25 - both the same person
- `marcus`/`thomas` 0.50 - not the same person
- Swedish short forms are sharper. `lasse`/`lars`, `kalle`/`karl`, `nisse`/`nils` all 0.67 and match. `stina`/`stig` 0.67 and does not

So the threshold cannot be set. That is the gap a model fills.

### The model does not do the matching

That was my assumption going in. Measuring it killed it.

- Residual after L1 and L2 has max cross-similarity 0.47. It belongs in `single source`
- What actually needs a model is 2 guests across 4 transactions
- Not residual pairs. Name disagreements on links the rules already made - `Andy`/Anders Moe, `Beth`/Elisabeth Holm
- My estimate was 10 to 15

The model is an auditor. With no model the link is kept, the evidence records why, and an unavailable verdict never reads as a confirmation.

## 3. At millions of records

A few million rows on one machine is par-for-the-course. Pandas, Polars or DuckDB do it no problem. No need for Spark or similar.

Already fine:

- Audit cost tracks ambiguity instead of volume. Verdicts cache on the normalized name pair, so the 4 transactions here collapse to 2 calls
- Scores are computed once and stored. A threshold filters them, it never re-runs the pipeline

Two things in this implementation need fixing before scaling up:

- `bookit_hubspot_candidates` compares every BookIT row to every HubSpot row. 992 pairs here, 10^12 at a million a side. Blocking is measured below and recommended, and it is not wired into the matcher. Repaired-name plus email blocking gives 100% recall at 97% reduction when it is
- `nx.max_weight_matching` is O(n^3) over the whole graph. Once blocking is in, run the matching inside each block. Blocks stay small, so cost goes linear in block count

The graph is what productizes. Resolution becomes incremental and local, and a confidence threshold becomes "traverse edges with weight >= t".

### The failure mode it introduces

Transitive closure. If A links B and B links C, every Berg becomes one guest.

I specified three guards against it. Only one earns its keep:

- **No guest holds two bookings** - valid. A test asserts it, and it is the one actually catching bad merges
- **A minimum confidence score** - debunked. Floor was 0.50, but the lowest score the matcher can emit is 0.55, so it never rejected anything. Removed rather than left in looking like protection
- **Two people in different cabins at the same time are not one person** - valid, and I had the reason for its silence wrong. I first wrote that it never fires because HubSpot records no cabin and no dates. Measuring the graph says otherwise: every component holds at most one BookIT record (32 hold one, the 3 CRM-only leads hold none), because the BookIT-to-HubSpot layer is a 1:1 matching and transactions attach as leaves. No component ever holds two records that both carry a cabin and a stay, so the guard is unreachable through the shape of the graph rather than through a missing column - and it becomes reachable the moment a source can put two stays under one guest. It is also a property of a finished component, not of a candidate edge, so it now runs after closure and reports contradictions instead of silently choosing which edge to cut


## Extrapolating the challenge

I read 120 rows and a container on a box as standing in for something wider: bi-directional integration, and an architecture the organisation can build on without every team solving identity, security and PII again.

A cook has already built a React app to upload the day's dinner bookings and plan seating. That is real demand showing up, and supporting it would be the good part of the job.

## Other docs

- `docs/DATA_ANALYSIS.md` - measured signal inventory, regenerates from the data
- `docs/ANNA_LARSEN.md` - the one link I cannot prove, and everything I checked
- `docs/TRAPS.md` - what was planted in the case data
- `docs/COMMANDS.md` - what each command does, drawn
- `docs/MODEL_EVAL.md` - model comparison
- `docs/SPECIFICATION.md` - design record
- `deploy/README.md` - API, compose, EC2
