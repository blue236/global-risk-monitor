import math

from app.main import _find_7d_prior_value, _threshold_overlays_for_series


def test_find_7d_prior_value_uses_calendar_days_cutoff():
    labels = ["2026-01-01", "2026-01-03", "2026-01-08", "2026-01-10"]
    values = [100.0, 102.0, 104.0, 106.0]
    # last=2026-01-10, target=2026-01-03 => should pick 2026-01-03
    assert _find_7d_prior_value(labels, values) == 102.0


def test_threshold_overlays_for_rate_and_pct_series():
    thresholds = {
        "dgs10_bp": 30.0,
        "hy_oas_bp": 50.0,
        "dxy_pct": 2.0,
        "qqq_pct": -5.0,
    }
    labels = [
        "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04",
        "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
    ]
    values = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 103.0]

    dgs10_lines = _threshold_overlays_for_series("DGS10", labels, values, thresholds)
    assert len(dgs10_lines) == 4
    assert any("WATCH +30bp" in x["label"] for x in dgs10_lines)

    dxy_lines = _threshold_overlays_for_series("DTWEXBGS", labels, values, thresholds)
    assert len(dxy_lines) == 2
    assert any("WATCH" in x["label"] for x in dxy_lines)
    watch = next(x for x in dxy_lines if x["severity"] == "WATCH")
    assert math.isclose(watch["value"], 102.0, rel_tol=1e-9)


def test_threshold_overlays_missing_mapping_or_threshold_returns_empty():
    labels = ["2026-01-01", "2026-01-08"]
    values = [100.0, 101.0]
    assert _threshold_overlays_for_series("GDELT", labels, values, {"geopolitics_wow_pct": 35.0}) == []
    assert _threshold_overlays_for_series("QQQ", labels, values, {}) == []
