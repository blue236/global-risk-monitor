import datetime as dt
from pathlib import Path

import pandas as pd

from app.analytics import compute_triggers
from app.config import TriggerConfig
from app.db import Database


def test_db_upsert_and_fetch(tmp_path: Path):
    db = Database(tmp_path / "t.sqlite")
    n = db.upsert_observations("X", [("2026-01-01", 1.0), ("2026-01-02", 2.0), ("2026-01-03", 3.0)])
    assert n >= 3
    rows = db.fetch_series("X")
    assert rows == [("2026-01-01", 1.0), ("2026-01-02", 2.0), ("2026-01-03", 3.0)]
    # limit should return latest rows (still ascending)
    rows_limited = db.fetch_series("X", limit=2)
    assert rows_limited == [("2026-01-02", 2.0), ("2026-01-03", 3.0)]
    # update
    db.upsert_observations("X", [("2026-01-03", 4.0)])
    rows2 = db.fetch_series("X")
    assert rows2[-1] == ("2026-01-03", 4.0)


def _mk_df(start: dt.date, values):
    dates = [pd.to_datetime(start + dt.timedelta(days=i)) for i in range(len(values))]
    return pd.DataFrame({"date": dates, "value": values})


def test_compute_triggers_basic():
    # Build 8 days so that we have a 7-day prior point
    dgs10 = _mk_df(dt.date(2026, 1, 1), [4.00, 4.01, 4.02, 4.03, 4.05, 4.05, 4.06, 4.40])
    t5y = _mk_df(dt.date(2026, 1, 1), [2.20, 2.20, 2.21, 2.22, 2.21, 2.21, 2.21, 2.45])
    hy = _mk_df(dt.date(2026, 1, 1), [3.50, 3.55, 3.60, 3.55, 3.50, 3.52, 3.51, 4.20])
    dxy = _mk_df(dt.date(2026, 1, 1), [100, 100, 100, 100, 100, 100, 100, 103])

    def mk_px(vals):
        return pd.DataFrame({"date": dgs10["date"], "close": vals})

    qqq = mk_px([100, 100, 100, 100, 100, 100, 100, 94])
    nvda = mk_px([100, 100, 100, 100, 100, 100, 100, 90])
    msft = mk_px([100, 100, 100, 100, 100, 100, 100, 96])

    geo = pd.DataFrame({"date": [pd.to_datetime(dt.date(2026, 1, 1) + dt.timedelta(days=i)) for i in range(14)],
                        "volume": [10]*7 + [20]*7})

    res = compute_triggers(
        dgs10=dgs10,
        t5yifr=t5y,
        hy_oas=hy,
        dxy=dxy,
        qqq=qqq,
        nvda=nvda,
        msft=msft,
        geopolitics=geo,
        cfg=TriggerConfig(dgs10_bp=30, t5yifr_bp=20, hy_oas_bp=50, dxy_pct=2, qqq_pct=-5, nvda_pct=-7, msft_pct=-5, geopolitics_wow_pct=50),
    )

    # Expect at least one alert
    assert any(r.status in ("WATCH", "ALERT") for r in res)
    # 10Y change: 4.40 - 4.00 = 0.40 => 40bp
    dgs = next(r for r in res if r.key == "DGS10")
    assert abs(dgs.wow_change - 40.0) < 0.01

