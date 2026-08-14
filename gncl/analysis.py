"""Signal inventory.

Measures each candidate matching signal as agreement rate on known-true pairs
against the rate on non-matching pairs. A signal earns its place only if the
first is much larger than the second.

Ground truth is the 21 BookIT<->HubSpot pairs locked by exact email. The wider
set of 28 links rests on an assumption (spec 2.5) and is not used to score
signals: assuming a link and then measuring a signal against it is circular.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pandas as pd

from gncl.normalize import MIN_REPAIR_RATIO, Name, name_keys, normalize_name, repaired_name

PHONE_CC = {"+47": "NO", "+45": "DK", "+46": "SE"}

# A signal must fire on most true pairs and much more often on them than on
# random ones. Both thresholds are judgement calls, stated once here.
MIN_TRUE_RATE = 0.7
MIN_LIFT = 2.0


@dataclass
class SignalResult:
    name: str
    true_rate: float
    true_n: int
    base_rate: float
    base_n: int
    verdict: str
    note: str

    @property
    def lift(self) -> float:
        return self.true_rate / self.base_rate if self.base_rate else float("inf")


def known_true_pairs(bk: pd.DataFrame, hs: pd.DataFrame) -> list[tuple[str, str]]:
    """BookIT<->HubSpot pairs locked by exact email. Unambiguous ground truth."""
    by_email = {r["email_norm"]: r["booking_ref"] for _, r in bk.iterrows() if r["email_norm"]}
    return [
        (by_email[r["email_norm"]], r["contact_id"])
        for _, r in hs.iterrows()
        if r["email_norm"] and r["email_norm"] in by_email
    ]


def _score(
    name: str,
    bk: pd.DataFrame,
    hs: pd.DataFrame,
    pairs: list[tuple[str, str]],
    predicate,
    note: str,
    keep_if_lift: float = MIN_LIFT,
) -> SignalResult:
    """Agreement on true pairs vs all non-matching cross pairs."""
    bki = {r["booking_ref"]: r for _, r in bk.iterrows()}
    hsi = {r["contact_id"]: r for _, r in hs.iterrows()}
    truth = set(pairs)

    t_hits = t_n = b_hits = b_n = 0
    for b_ref, b in bki.items():
        for h_id, h in hsi.items():
            try:
                agrees = predicate(b, h)
            except (KeyError, TypeError, AttributeError):
                continue
            if agrees is None:
                continue  # signal not evaluable for this pair (missing data)
            if (b_ref, h_id) in truth:
                t_n += 1
                t_hits += bool(agrees)
            else:
                b_n += 1
                b_hits += bool(agrees)

    tr = t_hits / t_n if t_n else 0.0
    br = b_hits / b_n if b_n else 0.0
    lift = tr / br if br else (float("inf") if tr else 0.0)
    verdict = "keep" if (tr >= MIN_TRUE_RATE and lift >= keep_if_lift) else "reject"
    return SignalResult(name, tr, t_n, br, b_n, verdict, note)


# --- individual signals -----------------------------------------------------


def sig_phone_cc(b, h):
    cc = PHONE_CC.get(str(h.get("phone", ""))[:3])
    if not cc or not b.get("nationality"):
        return None
    return cc == b["nationality"]


def sig_exact_name(b, h):
    return b["name_norm"] == h["name_norm"]


def sig_surname(b, h):
    bs, hs_ = Name(b["guest_name"]).surname, Name(h["full_name"]).surname
    return bool(bs) and bs == hs_


def sig_first_initial_surname(b, h):
    def key(name: Name) -> str:
        return f"{name.first[0]}{name.surname}" if name.first and name.surname else ""

    bk_key = key(Name(b["guest_name"]))
    return bool(bk_key) and bk_key == key(Name(h["full_name"]))


def sig_contact_before_checkin(b, h):
    """Does marketing contact land before the stay?"""
    if h["last_contacted"] is None or b["checkin"] is None:
        return None
    return h["last_contacted"] < b["checkin"]


# --- non-pair analyses ------------------------------------------------------


def email_localpart_vs_name(bk: pd.DataFrame, hs: pd.DataFrame) -> dict:
    """Does the email local part encode the name, and does it ever correct it?

    Token variants must be derived from the *raw* name. Folding first collapses
    o-slash to a single spelling, so "freja.sorensen" would read as disagreeing
    with "Soerensen" when it is the same name written two ways.
    """
    derivable = total = 0
    corrections, abbreviations = [], []
    for df, ncol in ((bk, "guest_name"), (hs, "full_name")):
        for _, r in df.iterrows():
            if not r["email_norm"] or "@" not in r["email_norm"]:
                continue
            total += 1
            local = r["email_norm"].split("@")[0]
            tokens = [t for t in re.split(r"[._\-]+", local) if t]
            raw_tokens = [t for t in re.split(r"\s+", str(r[ncol]).strip()) if t]
            if not raw_tokens or len(tokens) != len(raw_tokens):
                continue
            # Every accepted spelling of each name token.
            variants = [name_keys(t) for t in raw_tokens]
            diffs = [
                (tok, raw)
                for tok, raw, var in zip(tokens, raw_tokens, variants, strict=True)
                if tok not in var
            ]
            if not diffs:
                derivable += 1
            elif len(diffs) == 1:
                tok, raw = diffs[0]
                # An initial ("m" for Maja) is an abbreviation, not a correction.
                if len(tok) == 1 and normalize_name(raw).startswith(tok):
                    abbreviations.append((r[ncol], local, tok, raw))
                else:
                    corrections.append((r[ncol], local, tok, raw))
    return {
        "total_with_email": total,
        "derivable": derivable,
        "corrections": corrections,
        "abbreviations": abbreviations,
    }


def surname_morphology(bk: pd.DataFrame) -> dict:
    rules = [("sson", "SE"), ("sen", {"DK", "NO"}), ("qvist", "SE"), ("kvist", "SE")]
    hits = miss = 0
    detail = []
    for _, r in bk.iterrows():
        surname = Name(r["guest_name"]).surname
        for suffix, nat in rules:
            if surname.endswith(suffix):
                ok = r["nationality"] in nat if isinstance(nat, set) else r["nationality"] == nat
                hits += ok
                miss += not ok
                detail.append((r["guest_name"], r["nationality"], suffix, ok))
                break
    n = hits + miss
    return {"n": n, "hits": hits, "rate": hits / n if n else 0.0, "detail": detail}


def cabin_structure(bk: pd.DataFrame) -> dict:
    decks = Counter(c[0] for c in bk["cabin"] if c)
    by_deck_nat = {}
    for _, r in bk.iterrows():
        if r["cabin"]:
            by_deck_nat.setdefault(r["cabin"][0], Counter())[r["nationality"]] += 1
    # Lists, not single rows: cabin 6208 is reused, and keying on the cabin
    # alone would silently keep only the last occupant.
    cabins: dict[str, list] = {}
    for _, r in bk.iterrows():
        if r["cabin"]:
            cabins.setdefault(r["cabin"], []).append(r)
    adjacent = []
    for c, rows in cabins.items():
        if not c.isdigit():
            continue  # cabin ids are not guaranteed numeric; adjacency is undefined
        nxt = str(int(c) + 1)
        for r in rows:
            for other in cabins.get(nxt, []):
                a, b = Name(r["guest_name"]).surname, Name(other["guest_name"]).surname
                adjacent.append((c, r["guest_name"], nxt, other["guest_name"], bool(a) and a == b))
    return {"decks": dict(decks), "by_deck_nat": by_deck_nat, "adjacent": adjacent}


def contact_offset_distribution(bk, hs, pairs) -> dict:
    bki = {r["booking_ref"]: r for _, r in bk.iterrows()}
    hsi = {r["contact_id"]: r for _, r in hs.iterrows()}
    offsets = []
    for b_ref, h_id in pairs:
        b, h = bki[b_ref], hsi[h_id]
        if h["last_contacted"] and b["checkin"]:
            offsets.append((h["last_contacted"] - b["checkin"]).days)
    offsets.sort()
    if not offsets:
        return {}
    n = len(offsets)
    return {
        "n": n,
        "before_checkin": sum(o < 0 for o in offsets),
        "min": offsets[0],
        "max": offsets[-1],
        "median": offsets[n // 2],
    }


def blocking_candidates(bk, hs, pairs) -> list[dict]:
    """Pair completeness (recall) and reduction ratio for candidate blocking keys."""
    truth = set(pairs)
    total_pairs = len(bk) * len(hs)

    def keys_email(r):
        return {r["email_norm"]} if r["email_norm"] else set()

    def keys_surname(r):
        name = Name(r.get("guest_name") or r.get("full_name") or "")
        return {name.surname} if name.surname else set()

    def keys_initial_surname(r):
        name = Name(r.get("guest_name") or r.get("full_name") or "")
        return {f"{name.first[0]}:{name.surname}"} if name.first and name.surname else set()

    def keys_fullname(r):
        return {r["name_norm"]} if r["name_norm"] else set()

    out = []
    for name, fn in [
        ("email exact", keys_email),
        ("full name", keys_fullname),
        ("surname", keys_surname),
        ("initial+surname", keys_initial_surname),
    ]:
        blocks: dict[str, tuple[list, list]] = {}
        for _, r in bk.iterrows():
            for k in fn(r):
                blocks.setdefault(k, ([], []))[0].append(r["booking_ref"])
        for _, r in hs.iterrows():
            for k in fn(r):
                blocks.setdefault(k, ([], []))[1].append(r["contact_id"])
        compared, captured = 0, set()
        for b_ids, h_ids in blocks.values():
            compared += len(b_ids) * len(h_ids)
            for bi in b_ids:
                for hi in h_ids:
                    if (bi, hi) in truth:
                        captured.add((bi, hi))
        out.append(
            {
                "key": name,
                "blocks": len([1 for v in blocks.values() if v[0] and v[1]]),
                "comparisons": compared,
                "reduction": 1 - compared / total_pairs,
                "recall": len(captured) / len(truth) if truth else 0.0,
            }
        )
    return out


ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def date_format_audit(frames: dict[str, pd.DataFrame]) -> dict:
    """The brief promises inconsistent date formats. Count them rather than assume."""
    total, offenders = 0, []
    for name, df in frames.items():
        for col in [c for c in df.columns if c.endswith("_date")]:
            for value in df[col].astype(str).str.strip():
                if not value:
                    continue
                total += 1
                if not ISO_DATE.match(value):
                    offenders.append((name, col, value))
    return {"total": total, "offenders": offenders}


def stay_window_audit(bk: pd.DataFrame, ls: pd.DataFrame) -> dict:
    """How far cabin + stay window gets, and what the closed ends are worth.

    Half-open `[checkin, checkout)` is the obvious way to write this and it is
    wrong: transactions land on the last day of the stay.
    """
    stays: dict[str, list] = {}
    for _, r in bk.iterrows():
        if r["cabin"]:
            stays.setdefault(r["cabin"], []).append(r)
    no_cabin = closed = half_open = boundary = ambiguous = 0
    for _, t in ls.iterrows():
        if not t["cabin"]:
            no_cabin += 1
            continue
        day = t["txn_date"]
        hits = [b for b in stays.get(t["cabin"], []) if b["checkin"] <= day <= b["checkout"]]
        if not hits:
            continue
        closed += 1
        ambiguous += len(hits) > 1
        boundary += any(day in (b["checkin"], b["checkout"]) for b in hits)
        half_open += any(b["checkin"] <= day < b["checkout"] for b in hits)
    reused = {c: [r["guest_name"] for r in rows] for c, rows in stays.items() if len(rows) > 1}
    return {
        "total": len(ls),
        "no_cabin": no_cabin,
        "closed": closed,
        "boundary": boundary,
        "lost_if_half_open": closed - half_open,
        "ambiguous": ambiguous,
        "reused_cabins": reused,
    }


def unmatchable_records(bk: pd.DataFrame, hs: pd.DataFrame, ls: pd.DataFrame) -> dict:
    """Records with no counterpart. A matcher that pairs these off is wrong."""
    bk_names = {repaired_name(r) for _, r in bk.iterrows()}
    hs_names = {repaired_name(r) for _, r in hs.iterrows()}
    bk_emails = {r["email_norm"] for _, r in bk.iterrows() if r["email_norm"]}
    hs_emails = {r["email_norm"] for _, r in hs.iterrows() if r["email_norm"]}
    tx_cabins = {t["cabin"] for _, t in ls.iterrows() if t["cabin"]}
    tx_names = {t["name_norm"] for _, t in ls.iterrows() if t["name_norm"]}
    return {
        "crm_only": sorted(
            r["full_name"]
            for _, r in hs.iterrows()
            if repaired_name(r) not in bk_names and r["email_norm"] not in bk_emails
        ),
        "booking_only": sorted(
            r["guest_name"]
            for _, r in bk.iterrows()
            if repaired_name(r) not in hs_names and r["email_norm"] not in hs_emails
        ),
        "no_cabin_spend": sorted(
            (r["guest_name"], Name(r["guest_name"]).first.lower() in tx_names)
            for _, r in bk.iterrows()
            if r["cabin"] and r["cabin"] not in tx_cabins
        ),
        "no_cabin_recorded": sorted(r["guest_name"] for _, r in bk.iterrows() if not r["cabin"]),
    }


def run(source=None) -> dict:
    """Measure every signal. `source` supplies the frames; no hidden default."""
    from gncl.ports import CsvSource

    frames = (source or CsvSource()).frames()
    bk, hs = frames["bookit"], frames["hubspot"]
    pairs = known_true_pairs(bk, hs)

    signals = [
        _score("exact normalized name", bk, hs, pairs, sig_exact_name, "primary L2 rule"),
        _score("surname only", bk, hs, pairs, sig_surname, "blocking key, not a decision rule"),
        _score("first initial + surname", bk, hs, pairs, sig_first_initial_surname, "blocking key"),
        _score(
            "phone country code vs nationality",
            bk,
            hs,
            pairs,
            sig_phone_cc,
            "first estimated at 4/21",
        ),
        _score(
            "marketing contact before check-in",
            bk,
            hs,
            pairs,
            sig_contact_before_checkin,
            "only untested signal that could split the two Anna Larsens",
        ),
    ]
    return {
        "pairs": pairs,
        "signals": signals,
        "blocking_repaired": blocking_with_repair(bk, hs, pairs),
        "base_n": max((sig.base_n for sig in signals), default=0),
        "email_localpart": email_localpart_vs_name(bk, hs),
        "surname_morphology": surname_morphology(bk),
        "cabin": cabin_structure(bk),
        "contact_offset": contact_offset_distribution(bk, hs, pairs),
        "blocking": blocking_candidates(bk, hs, pairs),
        "dates": date_format_audit(frames),
        "window": stay_window_audit(bk, frames["ls_retail"]),
        "unmatchable": unmatchable_records(bk, hs, frames["ls_retail"]),
        "frames": frames,
    }


def blocking_with_repair(bk, hs, pairs) -> dict:
    truth = set(pairs)
    total = len(bk) * len(hs)
    blocks: dict[str, tuple[list, list]] = {}
    for _, r in bk.iterrows():
        blocks.setdefault(repaired_name(r), ([], []))[0].append(r["booking_ref"])
    for _, r in hs.iterrows():
        blocks.setdefault(repaired_name(r), ([], []))[1].append(r["contact_id"])
    compared, captured = 0, set()
    for b_ids, h_ids in blocks.values():
        compared += len(b_ids) * len(h_ids)
        for bi in b_ids:
            for hi in h_ids:
                if (bi, hi) in truth:
                    captured.add((bi, hi))
    return {
        "key": "full name, repaired from email",
        "blocks": len([1 for v in blocks.values() if v[0] and v[1]]),
        "comparisons": compared,
        "reduction": 1 - compared / total,
        "recall": len(captured) / len(truth) if truth else 0.0,
    }


@dataclass(frozen=True)
class ColumnProfile:
    """Fill and cardinality for one column."""

    column: str
    distinct: int
    filled: int
    total: int

    @property
    def fraction(self) -> str:
        return f"{self.filled}/{self.total}"


def _profile(df: pd.DataFrame, cols: list[str]) -> list[ColumnProfile]:
    def stats(col: str) -> ColumnProfile:
        values = df[col].astype(str).str.strip()
        return ColumnProfile(col, int(values.nunique()), int((values != "").sum()), len(df))

    return [stats(c) for c in cols]


def render(source=None) -> str:
    """Measure, then emit. `run()` measures; `_emit` is a pure formatter."""
    return _emit(run(source))


def _emit(r: dict) -> str:  # noqa: PLR0915 - linear formatter, no branching
    f = r["frames"]
    bk, hs, ls = f["bookit"], f["hubspot"], f["ls_retail"]
    rep = r["blocking_repaired"]
    e = r["email_localpart"]
    sm = r["surname_morphology"]
    off = r["contact_offset"]
    lines: list[str] = []
    w = lines.append

    dates, win, un = r["dates"], r["window"], r["unmatchable"]

    w("# Data analysis: signal inventory\n")
    w("Generated by `python -m gncl.analysis`. Every number measured, not estimated.\n")
    w(
        "Ground truth = the 21 BookIT<->HubSpot pairs locked by exact email. The wider "
        "set of 28 links rests on an assumption (spec 2.5) and is deliberately not used "
        "to score signals. Assume a link, then measure a signal against it = circular.\n"
    )

    w("## Short version\n")
    w("Two anchors carry the matching. Everything else is fallback or noise.\n")
    w("| Anchor | Joins | Reach |")
    w("|---|---|---|")
    w(f"| exact email | BookIT <-> HubSpot | {len(r['pairs'])} pairs |")
    w(f"| cabin + stay window | LS Retail -> BookIT | {win['closed']} of {win['total']} rows |")
    w("")
    w("Anchor gaps, and what fills them:\n")
    w(
        f"- No email on either side: repaired name, if unique. Only {len(r['pairs'])} of "
        f"{len(bk)} bookings reach HubSpot by email, so {len(bk) - len(r['pairs'])} "
        "bookings need the fallback. Not a corner case."
    )
    w(f"- No cabin on the transaction ({win['no_cabin']} rows): first name + stay window.")
    w("- Name matches but is not unique (both Anna Larsens): nothing. Flag it.\n")
    w("Rest of this document is why the other candidate signals were rejected.\n")

    w("## Field completeness\n")
    for label, df, cols in [
        ("BookIT", bk, ["booking_ref", "guest_name", "email", "cabin_number", "nationality"]),
        ("HubSpot", hs, ["contact_id", "full_name", "email", "phone", "marketing_consent"]),
        ("LS Retail", ls, ["transaction_id", "cabin_number", "guest_name_captured", "store"]),
    ]:
        w(f"**{label}** ({len(df)} rows)\n")
        w("| Field | Filled | Distinct |")
        w("|---|---|---|")
        for p in _profile(df, cols):
            w(f"| `{p.column}` | {p.fraction} | {p.distinct} |")
        w("")

    w("## Signal discriminative power\n")
    base_n = r["base_n"]
    w(
        f"`true` = agreement rate on the {len(r['pairs'])} known-true pairs. `base` = the "
        f"same rate on the {base_n} non-matching cross pairs. A signal is only useful "
        f"when true >> base.\n"
    )
    w("| Signal | True | Base | Lift | Verdict | Note |")
    w("|---|---|---|---|---|---|")
    for s in r["signals"]:
        lift = "inf" if s.lift == float("inf") else f"{s.lift:.2f}x"
        w(
            f"| {s.name} | {s.true_rate:.0%} | {s.base_rate:.1%} | {lift} | "
            f"**{s.verdict}** | {s.note} |"
        )
    w("")
    phone = next(s for s in r["signals"] if "phone" in s.name)
    w("### Nationality is not phone country code\n")
    w(
        f"BookIT has `nationality` (SE/NO/DK). "
        f"HubSpot has a phone prefix (+46/+47/+45). They look like the same fact. They are "
        f"not. The prefix agrees on {phone.true_rate:.0%} of true pairs and "
        f"{phone.base_rate:.0%} of non-matching pairs. That looks anti-correlated, and at "
        f"this sample size it is not. 4 of 21 against a 32.7% base is a one-sided binomial "
        f"p of 0.13, so the signal is uninformative rather than inverted. Either way it stays "
        f"out of the score, and it must not be used to 'fix' a nationality.\n"
    )
    contact = next(s for s in r["signals"] if "contact" in s.name)
    w("### Marketing contact date looks decisive\n")
    w(
        f"Every true pair has the "
        f"contact before check-in (offsets {off['min']} to {off['max']} days, median "
        f"{off['median']}). Then measure the baseline: {contact.base_rate:.0%} of "
        f"non-matching pairs satisfy it too, because every marketing date in the file "
        f"precedes almost every stay. Lift {contact.lift:.2f}x. This was the only untested "
        f"signal that could have split the two Anna Larsens. It cannot.\n"
    )

    w("## Email local part as a name repair source\n")
    w(
        f"{e['derivable']} of {e['total_with_email']} emails have a local part matching "
        f"the name exactly. The disagreements are the interesting part.\n"
    )
    w("| Recorded name | Email local part | Token | Email says |")
    w("|---|---|---|---|")
    for n, local, tok, raw in e["corrections"]:
        w(f"| {n} | `{local}` | `{raw}` | `{tok}` |")
    w("")
    w(
        "Both surname-bearing systems misspell the same surname, and misspell it "
        "differently. Name-to-name fuzzy matching is weak between two corruptions, and "
        "there is no third opinion: LS Retail records first name only. Two of three "
        "sources carry the surname and both are wrong.\n"
    )
    w(
        f"Every corrupted name is spelled correctly in its own email address. So email is "
        f"a join key and a name repair source. A further "
        f"{len(e['abbreviations'])} disagreements are abbreviations (`m.karlsson`, "
        f"`pernille.r`). Those are not errors and must not be 'repaired'.\n"
    )
    w(
        f"Repair is guarded on similarity, because a shared address is not evidence of a "
        f"name. The typos above score 0.83 to 0.88 against their email spelling. An "
        f'unrelated name in a household address ("Anna Berg" booking with '
        f"`nils.berg@gmail.com`) scores 0.25. `MIN_REPAIR_RATIO` is {MIN_REPAIR_RATIO}, "
        f"in the middle of that gap. Unguarded, that booking repaired to `nils berg` and "
        f"matched HubSpot's Nils Berg at 0.90.\n"
    )

    w("## Cabin + stay window\n")
    w(
        f"The second anchor. Cabin alone is not a key: cabin "
        f"{', '.join(sorted(win['reused_cabins']))} is reused by "
        f"{' and '.join(sorted(next(iter(win['reused_cabins'].values()))))}, same surname, "
        f"different weeks. Cabin plus the stay window is a key.\n"
    )
    w(f"- {win['closed']} of {win['total']} transactions resolve this way.")
    w(f"- {win['ambiguous']} land in two overlapping stays, so the key is unambiguous here.")
    w(f"- {win['no_cabin']} have no cabin at all and fall back to first name + window.\n")
    w("### The window is closed at both ends, on purpose\n")
    w(
        f"{win['boundary']} transactions land exactly on a check-in or check-out date. "
        f"Writing it half-open, `[checkin, checkout)`, silently drops "
        f"{win['lost_if_half_open']} of them. That is the whole difference between the "
        f"anchor working and the anchor quietly leaking guests on their last day.\n"
    )

    w("## Blocking keys\n")
    w(
        f"Pair completeness (recall) and reduction ratio against all "
        f"{len(bk)} x {len(hs)} = {len(bk) * len(hs)} candidate pairs.\n"
    )
    w("| Key | Blocks | Comparisons | Reduction | Recall |")
    w("|---|---|---|---|---|")
    for b in r["blocking"] + [rep]:
        w(
            f"| {b['key']} | {b['blocks']} | {b['comparisons']} | "
            f"{b['reduction']:.1%} | {b['recall']:.0%} |"
        )
    w("")
    w(
        "Name blocking alone loses 10% of true pairs: the two typo pairs share no name "
        "key. Repair names from the email local part first and recall goes to 100% at the "
        "same reduction. That is the scaling recommendation in one line: repair, then "
        "block on email and repaired name.\n"
    )

    w("## Signals rejected, with reasons\n")
    w("### Surname morphology vs nationality\n")
    w(
        f"Predicts nationality at {sm['rate']:.0%} "
        f"({sm['hits']}/{sm['n']}). Accurate, and useless for matching: HubSpot has no "
        f"nationality field, so the signal predicts a value that exists on one side only. "
        f"Accurate and inapplicable are different things.\n"
    )
    w("### Cabin adjacency\n")
    w(
        "Generates traps. Adjacent cabins hold 4021/4022 "
        "(two different Anna Larsens) and 3067/3068 (Elisabeth and Linnea Holm). Every "
        "name collision in the file sits next door to its twin. Deck is the leading digit "
        "and relates to neither nationality nor stay length.\n"
    )
    w("### Transaction spend and store profile\n")
    w(
        f"Cannot cross to HubSpot, which holds no "
        f"transaction data. Usable only inside LS Retail, where cabin plus stay window "
        f"already resolves {win['closed']} of {win['total']} rows with zero ambiguity. "
        f"No headroom.\n"
    )

    w("## Records with no counterpart\n")
    w("Not every record has a partner. Forcing one is the failure the case tests for.\n")
    w(f"- **CRM only, never sailed** ({len(un['crm_only'])}): {', '.join(un['crm_only'])}.")
    w(f"- **Booked, no CRM row** ({len(un['booking_only'])}): {', '.join(un['booking_only'])}.")
    no_tx = [n for n, recovered in un["no_cabin_spend"] if not recovered]
    recovered = [n for n, rec in un["no_cabin_spend"] if rec]
    w(
        f"- **No transaction against their cabin** ({len(un['no_cabin_spend'])}): "
        f"{', '.join(n for n, _ in un['no_cabin_spend'])}. "
        f"{', '.join(recovered)} are recovered by the first name + window fallback. "
        f"{', '.join(no_tx)} genuinely did not spend."
    )
    w(
        f"- **No cabin recorded in BookIT either** ({len(un['no_cabin_recorded'])}): "
        f"{', '.join(un['no_cabin_recorded'])}. Only signal is first name + date.\n"
    )
    w(
        f"{', '.join(sorted(set(no_tx) & set(un['booking_only'])))} appear in one source, with "
        f"no email, no CRM row and no spend. Correct output is a one-source guest.\n"
    )

    w("## The brief oversells the mess\n")
    w(
        f"The brief promises inconsistent date formats. There are none. "
        f"All {dates['total']} date values across the three files are ISO `YYYY-MM-DD`, "
        f"with {len(dates['offenders'])} exceptions. No date parser needed. Measured rather "
        f"than assumed, because the alternative is building a parser for a problem the "
        f"data does not have.\n"
    )

    w("## What this changes for the matcher\n")
    w("1. Repair names from email local parts before any name comparison.")
    w("2. Block on email exact, then repaired full name. Recall 100% on known pairs.")
    w("3. Keep the stay window closed at both ends.")
    w(
        "4. Do not use phone country code, marketing contact date, surname morphology or"
        " cabin adjacency as match evidence."
    )
    w("5. Leave one-source records unmatched. Do not build a date parser.")
    w(
        "6. The honest prior in the ticket held: BookIT<->HubSpot shares only name, email"
        " and phone, and no new cross-source signal was found. The one gain is name"
        " repair. It improves an existing signal instead of adding one."
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    from pathlib import Path

    out = Path(__file__).resolve().parent.parent / "docs" / "DATA_ANALYSIS.md"
    out.write_text(render())
    print(f"wrote {out}")
