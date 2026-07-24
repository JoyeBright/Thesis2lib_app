"""Helpers for writing Excel-safe tabular data."""

from __future__ import annotations

from typing import Any

import pandas as pd

from thesis2lib.utils import excel_safe_records, sanitize_for_excel


def records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(excel_safe_records(records))


def sanitize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.map(sanitize_for_excel)
