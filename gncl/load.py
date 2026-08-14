"""Source loaders (L0).

Each loader keeps the raw column alongside its normalized form. Matching runs on
the normalized values; the evidence string quotes the raw ones, because "linked
Beth to Elisabeth Holm" is only checkable if the original spelling survives.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from gncl.normalize import (
    name_keys,
    normalize_email,
    normalize_name,
    normalize_phone,
    parse_date,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "case"

EXPECTED_ROWS = {"bookit": 32, "hubspot": 31, "ls_retail": 57}


def _read(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"source file missing: {path}")
    # keep_default_na=False so an empty cell is "" rather than NaN; every gap in
    # this dataset is a missing string, and NaN turns string ops into float ops.
    return pd.read_csv(path, dtype=str, keep_default_na=False).rename(columns=str.strip)


def load_bookit(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    df = _read(data_dir / "bookit_guests.csv")
    df["source"] = "bookit"
    df["source_id"] = df["booking_ref"]
    df["name_norm"] = df["guest_name"].map(normalize_name)
    df["name_keys"] = df["guest_name"].map(name_keys)
    df["email_norm"] = df["email"].map(normalize_email)
    df["cabin"] = df["cabin_number"].str.strip()
    df["checkin"] = df["checkin_date"].map(parse_date)
    df["checkout"] = df["checkout_date"].map(parse_date)
    _check_stay_windows(df)
    return df


def load_hubspot(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    df = _read(data_dir / "hubspot_contacts.csv")
    df["source"] = "hubspot"
    df["source_id"] = df["contact_id"]
    df["name_norm"] = df["full_name"].map(normalize_name)
    df["name_keys"] = df["full_name"].map(name_keys)
    df["email_norm"] = df["email"].map(normalize_email)
    df["phone_norm"] = df["phone"].map(normalize_phone)
    df["last_contacted"] = df["last_contacted_date"].map(parse_date)
    return df


def load_ls_retail(data_dir: Path = DATA_DIR) -> pd.DataFrame:
    df = _read(data_dir / "ls_retail_transactions.csv")
    df["source"] = "ls_retail"
    df["source_id"] = df["transaction_id"]
    # First names only, and sometimes a diminutive: "Beth" for Elisabeth Holm.
    df["name_norm"] = df["guest_name_captured"].map(normalize_name)
    df["name_keys"] = df["guest_name_captured"].map(name_keys)
    df["cabin"] = df["cabin_number"].str.strip()
    df["txn_date"] = df["transaction_date"].map(parse_date)
    df["amount_num"] = pd.to_numeric(df["amount"], errors="coerce")
    # `coerce` turns anything unparseable into NaN, which then propagates into
    # that guest's total_spend without a word. "1,234.50" is enough to do it.
    unparseable = df[df["amount_num"].isna() & df["amount"].astype(str).str.strip().ne("")]
    if not unparseable.empty:
        raise ValueError(
            "unparseable amounts would silently become NaN in total_spend: "
            f"{unparseable[['transaction_id', 'amount']].to_dict('records')}"
        )
    return df


def _check_stay_windows(df: pd.DataFrame) -> None:
    """Check-out before check-in means the row cannot be reasoned about."""
    bad = df[
        df["checkin"].notna()
        & df["checkout"].notna()
        & df.apply(lambda r: r["checkout"] < r["checkin"], axis=1)
    ]
    if not bad.empty:
        raise ValueError(f"checkout precedes checkin: {bad['booking_ref'].tolist()}")


def load_all(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    """Load all three sources.

    `EXPECTED_ROWS` is a regression guard on the bundled sample, not a schema
    constraint, so it is enforced only when loading that sample. Applying it to
    a user-supplied directory would make `--data` reject every dataset but this
    one.
    """
    frames = {
        "bookit": load_bookit(data_dir),
        "hubspot": load_hubspot(data_dir),
        "ls_retail": load_ls_retail(data_dir),
    }
    if Path(data_dir).resolve() == DATA_DIR.resolve():
        for name, df in frames.items():
            expected = EXPECTED_ROWS[name]
            if len(df) != expected:
                raise ValueError(f"{name}: expected {expected} rows, loaded {len(df)}")
    return frames
