from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

META_ENABLED_PLUGINS = "enabled_plugins_json"


@dataclass(frozen=True)
class PluginDef:
    id: str
    name: str
    description: str
    source: str
    series_id: str
    trigger_pct: float
    lookback_days: int = 7


PLUGIN_REGISTRY: Dict[str, PluginDef] = {
    "vix": PluginDef(
        id="vix",
        name="VIX volatility index",
        description="Equity volatility stress (FRED: VIXCLS)",
        source="fred",
        series_id="VIXCLS",
        trigger_pct=15.0,
    ),
    "brent": PluginDef(
        id="brent",
        name="Brent crude oil",
        description="Inflation / supply shock proxy (FRED: DCOILBRENTEU)",
        source="fred",
        series_id="DCOILBRENTEU",
        trigger_pct=8.0,
    ),
    "dram_price": PluginDef(
        id="dram_price",
        name="DRAM price proxy (semiconductor PPI)",
        description="Producer price trend for semiconductor industry (FRED: PCU334413334413, MoM-style)",
        source="fred",
        series_id="PCU334413334413",
        trigger_pct=2.0,
        lookback_days=30,
    ),
    "ai_memory": PluginDef(
        id="ai_memory",
        name="AI memory demand proxy (Micron)",
        description="Micron 2-week move as AI memory demand proxy (Stooq: MU.US)",
        source="stooq",
        series_id="MU",
        trigger_pct=10.0,
        lookback_days=14,
    ),
}


def _normalize(ids: List[str]) -> List[str]:
    out = []
    for x in ids:
        k = (x or "").strip().lower()
        if k in PLUGIN_REGISTRY and k not in out:
            out.append(k)
    return out


def get_enabled_plugins(db) -> List[str]:
    raw = db.get_meta(META_ENABLED_PLUGINS)
    if not raw:
        return []
    try:
        ids = json.loads(raw)
        if isinstance(ids, list):
            return _normalize([str(x) for x in ids])
    except Exception:
        pass
    return []


def set_enabled_plugins(db, ids: List[str]) -> List[str]:
    normalized = _normalize(ids)
    db.set_meta(META_ENABLED_PLUGINS, json.dumps(normalized, ensure_ascii=False))
    return normalized


def list_plugins(db) -> dict:
    enabled = set(get_enabled_plugins(db))
    return {
        "plugins": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "source": p.source,
                "series_id": p.series_id,
                "trigger_pct": p.trigger_pct,
                "lookback_days": p.lookback_days,
                "enabled": p.id in enabled,
            }
            for p in PLUGIN_REGISTRY.values()
        ]
    }


def compute_plugin_triggers(data: Dict[str, pd.DataFrame], enabled_ids: List[str]) -> List[dict]:
    out: List[dict] = []
    for pid in enabled_ids:
        p = PLUGIN_REGISTRY.get(pid)
        if not p:
            continue
        df = data.get(pid)
        if df is None or df.empty:
            continue
        df2 = df.dropna(subset=["value"]).sort_values("date")
        if df2.empty:
            continue
        last = df2.iloc[-1]
        last_date = pd.to_datetime(last["date"]).date()
        target = last_date - dt.timedelta(days=p.lookback_days)
        prior_df = df2[df2["date"].dt.date <= target]
        wow = float("nan")
        if not prior_df.empty:
            prior = prior_df.iloc[-1]
            base = float(prior["value"])
            if base != 0:
                wow = (float(last["value"]) / base - 1.0) * 100.0

        status = "OK"
        if pd.notna(wow) and wow >= p.trigger_pct:
            status = "ALERT" if wow >= p.trigger_pct * 1.5 else "WATCH"

        out.append(
            {
                "key": p.series_id,
                "name": f"{p.name} (plugin)",
                "latest_date": str(last_date),
                "latest_value": float(last["value"]),
                "wow_change": float(wow) if pd.notna(wow) else float("nan"),
                "wow_change_unit": "%",
                "status": status,
                "rationale": f"{p.lookback_days}D change {wow:.2f}% vs plugin threshold {p.trigger_pct:.2f}%",
            }
        )
    return out
