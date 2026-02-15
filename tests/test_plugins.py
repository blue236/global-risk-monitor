import datetime as dt
from pathlib import Path

import pandas as pd

from app.db import Database
from app.plugins import compute_plugin_triggers, get_enabled_plugins, set_enabled_plugins


def test_plugin_settings_roundtrip(tmp_path: Path):
    db = Database(tmp_path / "t.sqlite")
    saved = set_enabled_plugins(db, ["vix", "unknown", "VIX", "brent"])
    assert saved == ["vix", "brent"]
    assert get_enabled_plugins(db) == ["vix", "brent"]


def test_compute_plugin_triggers_vix_watch():
    start = dt.date(2026, 1, 1)
    dates = [pd.to_datetime(start + dt.timedelta(days=i)) for i in range(8)]
    # +20% in 7 days should exceed 15% -> WATCH (but below 22.5% ALERT)
    df = pd.DataFrame({"date": dates, "value": [20, 20, 20, 20, 20, 20, 20, 24]})
    out = compute_plugin_triggers({"vix": df}, ["vix"])
    assert len(out) == 1
    assert out[0]["key"] == "VIXCLS"
    assert out[0]["status"] == "WATCH"
