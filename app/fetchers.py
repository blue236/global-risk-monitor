from __future__ import annotations

import asyncio
import datetime as dt
import os
from typing import Iterable, Optional, Tuple

import httpx
import pandas as pd


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def _http_verify_setting():
    """TLS verify policy from env.

    - GRM_SSL_VERIFY=0/false/no/off -> disable verification
    - GRM_SSL_VERIFY=/path/to/ca-bundle.pem -> use custom CA bundle
    - default -> True
    """
    v = (os.getenv("GRM_SSL_VERIFY", "1") or "1").strip()
    if v.lower() in {"0", "false", "no", "off"}:
        return False
    if v.lower() in {"1", "true", "yes", "on"}:
        return True
    return v


def _as_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


async def _get_with_retries(client: httpx.AsyncClient, url: str, *, params: dict, retries: int = 3) -> httpx.Response:
    last_err: Exception | None = None
    for i in range(retries):
        try:
            r = await client.get(url, params=params)
            # Retry transient upstream errors.
            if r.status_code in (429, 500, 502, 503, 504) and i < retries - 1:
                await asyncio.sleep(1.5 * (i + 1))
                continue
            return r
        except httpx.RequestError as e:
            last_err = e
            if i < retries - 1:
                await asyncio.sleep(1.5 * (i + 1))
                continue
            raise
    if last_err:
        raise last_err
    raise RuntimeError("request failed")


async def fetch_fred_series(series_id: str, *, start: Optional[dt.date] = None) -> pd.DataFrame:
    """Fetch a FRED series via the public fredgraph CSV endpoint (no API key required).

    Returns a DataFrame with columns: date (datetime64[ns]), value (float).
    Missing values are dropped.
    """
    params = {"id": series_id}
    if start:
        params["cosd"] = start.isoformat()

    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "global-risk-monitor/1.0"}, verify=_http_verify_setting()) as client:
        r = await _get_with_retries(client, FRED_CSV_URL, params=params)
        r.raise_for_status()

    from io import StringIO

    df = pd.read_csv(StringIO(r.text))
    if df.empty or len(df.columns) < 2:
        raise ValueError(f"Unexpected FRED CSV format for {series_id}: empty or missing columns")

    # FRED currently uses observation_date,<SERIES>, but older examples often use DATE.
    date_col = "observation_date" if "observation_date" in df.columns else ("DATE" if "DATE" in df.columns else df.columns[0])
    value_col = series_id if series_id in df.columns else df.columns[1]

    df = df.rename(columns={date_col: "date", value_col: "value"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).sort_values("date")
    return df[["date", "value"]]


async def fetch_stooq_daily_close(ticker: str, *, start: Optional[dt.date] = None) -> pd.DataFrame:
    """Fetch daily close from Stooq (free CSV, no key). Ticker examples: QQQ.US, NVDA.US

    Returns DataFrame: date, close
    """
    stooq_ticker = ticker if ticker.endswith(".US") else f"{ticker}.US"
    url = f"https://stooq.com/q/d/l/"
    params = {"s": stooq_ticker.lower(), "i": "d"}

    async with httpx.AsyncClient(timeout=20, follow_redirects=True, verify=_http_verify_setting()) as client:
        r = await _get_with_retries(client, url, params=params)
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
        # GDELT DOC API timeline mode needs a specific metric mode.
        # timelinevolraw returns daily Article Count with a stable JSON shape.
        "mode": "timelinevolraw",
        "format": "json",
        "timelinesmooth": 0,
        "startdatetime": fmt(start, "000000"),
        "enddatetime": fmt(end, "235959"),
        "timelinespan": "1d",
    }

    timeout = httpx.Timeout(60.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "global-risk-monitor/1.0"}, verify=_http_verify_setting()) as client:
        try:
            r = await _get_with_retries(client, url, params=params)
        except httpx.ReadTimeout as e:
            raise ValueError("GDELT request timed out") from e

    # GDELT may throttle with plain-text 429; treat as temporary empty data instead of hard failure.
    if r.status_code == 429:
        return pd.DataFrame(columns=["date", "volume"])

    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    if "json" not in ctype and not r.text.lstrip().startswith("{"):
        txt = (r.text or "").strip()
        if txt.startswith("Please limit requests"):
            return pd.DataFrame(columns=["date", "volume"])
        raise ValueError(f"Unexpected GDELT response: {txt[:180]}")

    try:
        data = r.json()
    except Exception as e:
        raise ValueError(f"GDELT invalid JSON: {e}")

    timeline = data.get("timeline")
    if not timeline:
        return pd.DataFrame(columns=["date", "volume"])

    rows = []

    # Known shape for timelinevolraw:
    # {"timeline": [{"series":"Article Count", "data":[{"date":"20260101T000000Z", "value":123}, ...]}]}
    if isinstance(timeline, list) and timeline and isinstance(timeline[0], dict) and "data" in timeline[0]:
        points = timeline[0].get("data") or []
        for point in points:
            raw = point.get("date")
            val = point.get("value")
            if not raw:
                continue
            d = dt.datetime.strptime(str(raw)[:8], "%Y%m%d").date()
            rows.append((pd.to_datetime(d), float(val or 0.0)))
    else:
        # Backward-compatible fallback for flat timeline points.
        for point in timeline:
            raw = point.get("date")
            val = point.get("value")
            if not raw:
                continue
            d = dt.datetime.strptime(str(raw)[:8], "%Y%m%d").date()
            rows.append((pd.to_datetime(d), float(val or 0.0)))

    if not rows:
        return pd.DataFrame(columns=["date", "volume"])

    df = pd.DataFrame(rows, columns=["date", "volume"]).sort_values("date")
    return df

