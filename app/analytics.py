from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .config import TriggerConfig


@dataclass
class TriggerResult:
    key: str
    name: str
    latest_date: str
    latest_value: float
    wow_change: float
    wow_change_unit: str
    status: str  # OK | WATCH | ALERT
    rationale: str


def _weekly_change(df: pd.DataFrame, value_col: str, *, unit: str) -> Tuple[float, str]:
    """Compute 7-calendar-day change using last observation <= today and <= today-7."""
    if df.empty:
        return float("nan"), unit

    df2 = df.dropna(subset=[value_col]).sort_values("date")
    last = df2.iloc[-1]
    last_date = pd.to_datetime(last["date"]).date()
    target = last_date - dt.timedelta(days=7)

    # pick latest observation on or before target
    prior_df = df2[df2["date"].dt.date <= target]
    if prior_df.empty:
        return float("nan"), unit

    prior = prior_df.iloc[-1]
    delta = float(last[value_col]) - float(prior[value_col])
    return delta, unit


def _weekly_pct_change(df: pd.DataFrame, value_col: str) -> float:
    if df.empty:
        return float("nan")
    df2 = df.dropna(subset=[value_col]).sort_values("date")
    last = df2.iloc[-1]
    last_date = pd.to_datetime(last["date"]).date()
    target = last_date - dt.timedelta(days=7)
    prior_df = df2[df2["date"].dt.date <= target]
    if prior_df.empty:
        return float("nan")
    prior = prior_df.iloc[-1]
    base = float(prior[value_col])
    if base == 0:
        return float("nan")
    return (float(last[value_col]) / base - 1.0) * 100.0


def compute_triggers(
    *,
    dgs10: pd.DataFrame,
    t5yifr: pd.DataFrame,
    hy_oas: pd.DataFrame,
    dxy: pd.DataFrame,
    qqq: pd.DataFrame,
    nvda: pd.DataFrame,
    msft: pd.DataFrame,
    geopolitics: Optional[pd.DataFrame],
    cfg: TriggerConfig = TriggerConfig(),
) -> List[TriggerResult]:
    out: List[TriggerResult] = []

    # DGS10: % change, interpret delta in bp
    def add_rate(key: str, name: str, df: pd.DataFrame, threshold_bp: float):
        if df.empty:
            return
        latest = df.iloc[-1]
        delta, _ = _weekly_change(df, "value", unit="pp")
        bp = delta * 100.0
        status = "OK"
        if pd.notna(bp) and abs(bp) >= threshold_bp:
            status = "ALERT" if abs(bp) >= threshold_bp * 1.5 else "WATCH"
        out.append(
            TriggerResult(
                key=key,
                name=name,
                latest_date=str(pd.to_datetime(latest["date"]).date()),
                latest_value=float(latest["value"]),
                wow_change=bp,
                wow_change_unit="bp",
                status=status,
                rationale=f"7D change {bp:.1f}bp vs threshold {threshold_bp:.0f}bp",
            )
        )

    add_rate("DGS10", "US 10Y Treasury yield", dgs10, cfg.dgs10_bp)
    add_rate("T5YIFR", "5y5y inflation expectations", t5yifr, cfg.t5yifr_bp)
    add_rate("BAMLH0A0HYM2", "US High Yield OAS", hy_oas, cfg.hy_oas_bp)

    # DXY: weekly %
    def add_pct(key: str, name: str, df: pd.DataFrame, col: str, threshold_pct: float, direction: str = "abs"):
        if df.empty:
            return
        latest = df.iloc[-1]
        pct = _weekly_pct_change(df, col)
        status = "OK"
        breached = False
        if pd.notna(pct):
            if direction == "abs":
                breached = abs(pct) >= abs(threshold_pct)
            elif direction == "ge":
                breached = pct >= threshold_pct
            elif direction == "le":
                breached = pct <= threshold_pct
        if breached:
            status = "ALERT" if abs(pct) >= abs(threshold_pct) * 1.5 else "WATCH"
        out.append(
            TriggerResult(
                key=key,
                name=name,
                latest_date=str(pd.to_datetime(latest["date"]).date()),
                latest_value=float(latest[col]),
                wow_change=float(pct) if pd.notna(pct) else float("nan"),
                wow_change_unit="%",
                status=status,
                rationale=f"7D change {pct:.2f}% vs threshold {threshold_pct:.2f}%",
            )
        )

    add_pct("DTWEXBGS", "USD broad index (DTWEXBGS)", dxy, "value", cfg.dxy_pct, direction="ge")
    add_pct("QQQ", "Nasdaq-100 proxy (QQQ)", qqq, "close", cfg.qqq_pct, direction="le")
    add_pct("NVDA", "NVIDIA", nvda, "close", cfg.nvda_pct, direction="le")
    add_pct("MSFT", "Microsoft", msft, "close", cfg.msft_pct, direction="le")

    # Geopolitics: compare last 7 days volume vs prior 7 days
    if geopolitics is not None and not geopolitics.empty:
        g = geopolitics.sort_values("date")
        g["date"] = pd.to_datetime(g["date"])
        last_date = g.iloc[-1]["date"].date()
        end = last_date
        start_14 = end - dt.timedelta(days=13)
        window = g[(g["date"].dt.date >= start_14) & (g["date"].dt.date <= end)].copy()
        if not window.empty:
            window["day"] = window["date"].dt.date
            # ensure all days present
            all_days = [start_14 + dt.timedelta(days=i) for i in range(14)]
            day_map = window.groupby("day")["volume"].sum().to_dict()
            vols = [float(day_map.get(d, 0.0)) for d in all_days]
            last7 = sum(vols[7:])
            prev7 = sum(vols[:7])
            wow = ((last7 / prev7) - 1.0) * 100.0 if prev7 > 0 else float("inf")
            status = "OK"
            if wow >= cfg.geopolitics_wow_pct:
                status = "ALERT" if wow >= cfg.geopolitics_wow_pct * 2 else "WATCH"
            out.append(
                TriggerResult(
                    key="GDELT",
                    name="Geopolitics headline volume (GDELT)",
                    latest_date=str(end),
                    latest_value=float(last7),
                    wow_change=float(wow),
                    wow_change_unit="%",
                    status=status,
                    rationale=f"Last7 vs Prior7: {wow:.1f}% vs threshold {cfg.geopolitics_wow_pct:.0f}%",
                )
            )

    # Sort by severity
    order = {"ALERT": 0, "WATCH": 1, "OK": 2}
    out.sort(key=lambda x: (order.get(x.status, 9), x.key))
    return out

