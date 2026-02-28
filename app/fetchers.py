from __future__ import annotations

import datetime as dt
import logging
import os
import time
from typing import Optional

import httpx
import pandas as pd

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
logger = logging.getLogger(__name__)


def _http_verify_setting():
    v = (os.getenv("GRM_SSL_VERIFY", "1") or "1").strip()
    if v.lower() in {"0", "false", "no", "off"}:
        return False
    if v.lower() in {"1", "true", "yes", "on"}:
        return True
    return v


def _get_with_retries_sync(
    url: str,
    *,
    params: dict,
    retries: int = 3,
    timeout: float | httpx.Timeout = 20.0,
    headers: dict | None = None,
    follow_redirects: bool = False,
) -> httpx.Response:
    last_err: Exception | None = None
    for i in range(retries):
        try:
            with httpx.Client(
                timeout=timeout,
                headers=headers,
                follow_redirects=follow_redirects,
                verify=_http_verify_setting(),
            ) as client:
                r = client.get(url, params=params)

            if r.status_code in (429, 500, 502, 503, 504) and i < retries - 1:
                logger.info("Transient upstream status %s for %s (attempt %s/%s), retrying", r.status_code, url, i + 1, retries)
                time.sleep(1.5 * (i + 1))
                continue

            if i > 0 and r.status_code < 400:
                logger.info("Recovered after retry for %s (attempt %s/%s)", url, i + 1, retries)
            return r
        except httpx.RequestError as e:
            last_err = e
            if i < retries - 1:
                logger.info("Transient request error for %s: %s (attempt %s/%s), retrying", url, e.__class__.__name__, i + 1, retries)
                time.sleep(1.5 * (i + 1))
                continue
            raise

    if last_err:
        raise last_err
    raise RuntimeError("request failed")


def fetch_fred_series(series_id: str, *, start: Optional[dt.date] = None) -> pd.DataFrame:
    params = {"id": series_id}
    if start:
        params["cosd"] = start.isoformat()

    r = _get_with_retries_sync(
        FRED_CSV_URL,
        params=params,
        retries=3,
        timeout=20.0,
        headers={"User-Agent": "global-risk-monitor/1.0"},
    )
    r.raise_for_status()

    from io import StringIO

    df = pd.read_csv(StringIO(r.text))
    if df.empty or len(df.columns) < 2:
        raise ValueError(f"Unexpected FRED CSV format for {series_id}: empty or missing columns")

    date_col = "observation_date" if "observation_date" in df.columns else ("DATE" if "DATE" in df.columns else df.columns[0])
    value_col = series_id if series_id in df.columns else df.columns[1]

    df = df.rename(columns={date_col: "date", value_col: "value"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).sort_values("date")
    return df[["date", "value"]]


def fetch_stooq_daily_close(ticker: str, *, start: Optional[dt.date] = None) -> pd.DataFrame:
    stooq_ticker = ticker if ticker.endswith(".US") else f"{ticker}.US"
    url = "https://stooq.com/q/d/l/"
    params = {"s": stooq_ticker.lower(), "i": "d"}

    r = _get_with_retries_sync(url, params=params, retries=3, timeout=20.0, follow_redirects=True)
    r.raise_for_status()

    from io import StringIO

    df = pd.read_csv(StringIO(r.text))
    if "Date" not in df.columns or "Close" not in df.columns:
        raise ValueError(f"Unexpected Stooq CSV format for {ticker}")
    df = df.rename(columns={"Date": "date", "Close": "close"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    if start:
        df = df[df["date"].dt.date >= start]
    return df[["date", "close"]]


def fetch_gdelt_daily_volume(query: str, *, start: dt.date, end: dt.date) -> pd.DataFrame:
    def fmt(d: dt.date, hhmmss: str) -> str:
        return d.strftime("%Y%m%d") + hhmmss

    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": query,
        "mode": "timelinevolraw",
        "format": "json",
        "timelinesmooth": 0,
        "startdatetime": fmt(start, "000000"),
        "enddatetime": fmt(end, "235959"),
        "timelinespan": "1d",
    }

    timeout = httpx.Timeout(60.0, connect=20.0)
    try:
        r = _get_with_retries_sync(
            url,
            params=params,
            retries=3,
            timeout=timeout,
            headers={"User-Agent": "global-risk-monitor/1.0"},
        )
    except httpx.ReadTimeout as e:
        raise ValueError("GDELT request timed out") from e

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
