from __future__ import annotations

import datetime as dt
from typing import Iterable, Optional, Tuple

import httpx
import pandas as pd


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def _as_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


async def fetch_fred_series(series_id: str, *, start: Optional[dt.date] = None) -> pd.DataFrame:
    """Fetch a FRED series via the public fredgraph CSV endpoint (no API key required).

    Returns a DataFrame with columns: date (datetime64[ns]), value (float).
    Missing values are dropped.
    """
    params = {"id": series_id}
    if start:
        params["cosd"] = start.isoformat()

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(FRED_CSV_URL, params=params)
        r.raise_for_status()

    # fredgraph CSV has columns: DATE, <SERIES_ID>
    from io import StringIO

    df = pd.read_csv(StringIO(r.text))
    if "DATE" not in df.columns or series_id not in df.columns:
        raise ValueError(f"Unexpected FRED CSV format for {series_id}")

    df = df.rename(columns={"DATE": "date", series_id: "value"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).sort_values("date")
    return df


async def fetch_stooq_daily_close(ticker: str, *, start: Optional[dt.date] = None) -> pd.DataFrame:
    """Fetch daily close from Stooq (free CSV, no key). Ticker examples: QQQ.US, NVDA.US

    Returns DataFrame: date, close
    """
    stooq_ticker = ticker if ticker.endswith(".US") else f"{ticker}.US"
    url = f"https://stooq.com/q/d/l/"
    params = {"s": stooq_ticker.lower(), "i": "d"}

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()

    from io import StringIO

    df = pd.read_csv(StringIO(r.text))
    # columns: Date, Open, High, Low, Close, Volume
    if "Date" not in df.columns or "Close" not in df.columns:
        raise ValueError(f"Unexpected Stooq CSV format for {ticker}")
    df = df.rename(columns={"Date": "date", "Close": "close"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    if start:
        df = df[df["date"].dt.date >= start]
    return df[["date", "close"]]


async def fetch_gdelt_daily_volume(
    query: str,
    *,
    start: dt.date,
    end: dt.date,
) -> pd.DataFrame:
    """Fetch daily mention volume from GDELT 2.1 DOC API timeline.

    Returns DataFrame with columns: date, volume
    """
    # GDELT expects datetimes: YYYYMMDDhhmmss
    def fmt(d: dt.date, hhmmss: str) -> str:
        return d.strftime("%Y%m%d") + hhmmss

    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": query,
        "mode": "timeline",
        "format": "json",
        "timelinesmooth": 0,
        "startdatetime": fmt(start, "000000"),
        "enddatetime": fmt(end, "235959"),
        "timelinespan": "1d",
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()

    timeline = data.get("timeline")
    if not timeline:
        return pd.DataFrame(columns=["date", "volume"])

    rows = []
    for point in timeline:
        # point has: date (YYYYMMDDHHMMSS), value
        raw = point.get("date")
        val = point.get("value")
        if not raw:
            continue
        d = dt.datetime.strptime(raw[:8], "%Y%m%d").date()
        rows.append((pd.to_datetime(d), float(val or 0.0)))

    df = pd.DataFrame(rows, columns=["date", "volume"]).sort_values("date")
    return df

