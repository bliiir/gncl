# Anna Larsen

```
BK1001 = TX5001 = HS201          locked by email
BK1021 = TX5037 = TX5038 = ?     cabin 4022, no email
```

HS221 is the only record left over. No email. Swedish phone, and BK1021 is SE, which weakly points that way, but it is not enough to be sure > **review**

So either HS221 is BK1021, or she is a third Anna Larsen who never travelled. Nothing in the data tells those apart. That is why it sits in review at 0.55.

---

## How the matcher gets there

Not from the phone. All four cross-edges exist because the name is `anna larsen` on all four rows.

```
BK1001 -> HS201   0.98   exact email
BK1001 -> HS221   0.55   name, not unique
BK1021 -> HS201   0.55   name, not unique
BK1021 -> HS221   0.55   name, not unique
```

Email takes BK1001-HS201 at 0.98. BK1021 and HS221 are then the only two left free, so the 0.55 edge between them is taken. Elimination.

Elimination is real evidence, just not conclusive. 3 of 31 HubSpot rows never sailed, so roughly 90% of contacts do correspond to a guest. If HS221 sailed and is called Anna Larsen, BK1021 is the only candidate. What it cannot do is rule out the other 10% - a fourth never-sailed lead named Anna Larsen.

## Why the phone stays out of the score

It agrees on 19% of email-proven pairs and 32.7% of everything else. Lift 0.58x.

| edge | nationality | phone | agree |
|---|---|---|---|
| BK1001 - HS201 | DK | +47 NO | no, and this one is proven true |
| BK1001 - HS221 | DK | +46 SE | no |
| BK1021 - HS201 | SE | +47 NO | no |
| BK1021 - HS221 | SE | +46 SE | yes |

It misses the known pair and fires on the one nobody can check. That is suggestive and it is not significant - 4 of 21 against a 32.7% base is a one-sided binomial p of 0.13. At this sample size the phone carries no information in either direction, which is reason enough to keep it out of the score.

## Is there a third Anna in the data

No. Searched every source by substring, by surname similarity down to 0.70, by first name, by email. Two, every way.

Ruling one out is a different question, and I cannot. BookIT holds one name per booking and no party size, so a second person in a cabin leaves no trace. Cabin 4022 has two purchases on Jun 12, Spa and Boutique - one busy guest, or two people. Same-day two-store buying happens on 7 bookings, so it says nothing.

## If the link were dropped

| | now | dropped |
|---|---|---|
| guests | 35 | 36 |
| deterministic | 20 | 21 |
| rule_fuzzy | 10 | 9 |
| single source | 5 | 6 |
| accepted / review | 34 / 1 | 36 / 0 |

BK1021 keeps its two transactions at 0.95 and stays a two-source guest. Only HS221 goes single source. The review queue empties, because this is the only thing in it.

Kept. The output says these sources record two Anna Larsens. It does not say two sailed.
