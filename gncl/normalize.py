"""Field normalization (L0).

The Scandinavian folding here is deliberately explicit. `unicodedata` alone is
not enough: NFKD leaves o-slash and ae undecomposed, and turns a-ring into "a"
rather than the conventional "aa". A normalizer built on NFKD passes every test
written against the GNCL dataset, because BookIT and HubSpot happen to spell
Bjorn and Sorensen identically, and then fails silently on real data.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher

# Conventional Scandinavian transliteration. Applied before NFKD, because NFKD
# does not touch these.
_CONVENTIONAL = {
    "ø": "oe",  # o-slash  (Danish/Norwegian)
    "æ": "ae",  # ae ligature
    "å": "aa",  # a-ring   (Aarhus / Arhus)
    "ö": "oe",  # o-umlaut (Swedish)
    "ä": "ae",  # a-umlaut (Swedish)
    "ü": "ue",  # u-umlaut (German names in a Nordic guest list)
}

# Diacritic-dropped forms, which is how these characters most often arrive in
# systems that lost their encoding somewhere upstream: Bjorn, Sorensen.
_STRIPPED = {
    "ø": "o",
    "æ": "a",
    "å": "a",
    "ö": "o",
    "ä": "a",
    "ü": "u",
}

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def _fold(text: str, table: dict[str, str]) -> str:
    out = "".join(table.get(ch, ch) for ch in text.casefold())
    decomposed = unicodedata.normalize("NFKD", out)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def fold(text: str) -> str:
    """Canonical fold: conventional transliteration (o-slash -> oe, a-ring -> aa)."""
    return _fold(text, _CONVENTIONAL)


def fold_stripped(text: str) -> str:
    """Alternate fold: diacritic dropped (o-slash -> o, a-ring -> a)."""
    return _fold(text, _STRIPPED)


def normalize_name(name: str) -> str:
    """Canonical match key for a personal name."""
    if not name:
        return ""
    return _WS.sub(" ", _PUNCT.sub(" ", fold(name))).strip()


def name_keys(name: str) -> frozenset[str]:
    """All plausible match keys for a name.

    Two records spelling the same name differently ("Bjorn" vs "Bjoern") collapse
    to different canonical keys, so matchers compare key *sets* and treat any
    intersection as agreement. Cheap here, and the alternative is choosing one
    transliteration and silently losing the other.
    """
    if not name:
        return frozenset()
    variants = {fold(name), fold_stripped(name)}
    return frozenset(_WS.sub(" ", _PUNCT.sub(" ", v)).strip() for v in variants if v.strip())


def normalize_email(email: str | None) -> str:
    return email.strip().casefold() if email and email.strip() else ""


def normalize_phone(phone: str | None) -> str:
    """Digits with a leading '+'. Not used for matching; see docs/DATA_ANALYSIS.md."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    return f"+{digits}" if digits else ""


def parse_date(value: str | None) -> date | None:
    """Parse a date, trying the formats present across the three sources."""
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            # Naive by design: these are calendar dates, not instants.
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise ValueError(f"unparseable date: {value!r}")


def in_stay_window(when: date, checkin: date | None, checkout: date | None) -> bool:
    """Closed interval on both ends.

    16 transactions land exactly on a boundary of the booking they attach to:
    10 on check-in, 6 on check-out. A half-open [checkin, checkout) drops the 6
    on check-out day.
    """
    if when is None or checkin is None or checkout is None:
        return False
    return checkin <= when <= checkout


@dataclass(frozen=True)
class Name:
    """A personal name, with the parts callers actually ask for.

    Splitting a name string at the call site puts an IndexError one unnamed
    booking away. Asking a value object removes the class of bug, not just the
    instance.
    """

    raw: str

    @property
    def normalized(self) -> str:
        return normalize_name(self.raw)

    @property
    def parts(self) -> list[str]:
        return self.normalized.split()

    @property
    def first(self) -> str:
        """Empty when there is no name, never an IndexError."""
        parts = self.parts
        return parts[0] if parts else ""

    @property
    def surname(self) -> str:
        parts = self.parts
        return parts[-1] if len(parts) > 1 else ""

    @property
    def keys(self) -> frozenset[str]:
        return name_keys(self.raw)

    def __bool__(self) -> bool:
        return bool(self.parts)


_LOCAL_PART_SPLIT = re.compile(r"[._\-]+")

# A repair must look like a correction of the recorded name, not a replacement
# of it. Measured on this dataset the two populations are far apart: the four
# genuine typos score 0.83 to 0.88 against their email spelling, while an
# unrelated name in a shared household address scores 0.18 to 0.25. 0.60 sits in
# the middle of that gap rather than against either edge.
#
# Without this, a booking for "Anna Berg" carrying the household email
# nils.berg@gmail.com repaired to "nils berg" and then matched HubSpot's Nils
# Berg at 0.90 -- a silent identity rewrite, in exactly the messy-data class
# this case is about.
MIN_REPAIR_RATIO = 0.60


def repaired_name(row) -> str:
    """Name with tokens corrected from the email local part where they disagree.

    The local part carries the correct spelling for all four corrupted names in
    this dataset (measured in docs/DATA_ANALYSIS.md). A token that is a single
    initial is an abbreviation, not a correction, and is left alone. So is one
    that is not close enough to be a spelling of the same name -- see
    MIN_REPAIR_RATIO, and the shared-household-email case it exists for.
    """
    raw = str(row.get("guest_name") or row.get("full_name") or "").strip()
    raw_tokens = [t for t in re.split(r"\s+", raw) if t]
    email = row.get("email_norm") or ""
    if not raw_tokens or "@" not in email:
        return normalize_name(" ".join(raw_tokens))
    tokens = [t for t in _LOCAL_PART_SPLIT.split(email.split("@")[0]) if t]
    if len(tokens) != len(raw_tokens):
        return normalize_name(" ".join(raw_tokens))
    out = []
    for token, part in zip(tokens, raw_tokens, strict=True):
        canonical = normalize_name(part)
        too_different = SequenceMatcher(None, token, canonical).ratio() < MIN_REPAIR_RATIO
        keep_recorded = len(token) == 1 or token in name_keys(part) or too_different
        out.append(canonical if keep_recorded else token)
    return " ".join(out)
