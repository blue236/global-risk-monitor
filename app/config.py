from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerConfig:
    # Weekly absolute change thresholds
    dgs10_bp: float = 30.0  # 10Y yield change in basis points
    t5yifr_bp: float = 20.0  # 5y5y inflation expectation change in bp
    hy_oas_bp: float = 50.0  # High-yield OAS widening in bp
    dxy_pct: float = 2.0  # USD broad index % change
    qqq_pct: float = -5.0  # Nasdaq proxy weekly % change (<=)
    nvda_pct: float = -7.0
    msft_pct: float = -5.0
    geopolitics_wow_pct: float = 35.0  # WoW increase in mention volume


FRED_SERIES = {
    # 10-Year Treasury Constant Maturity Rate
    "DGS10": {
        "name": "US 10Y Treasury yield",
        "unit": "%",
    },
    # 5-Year, 5-Year Forward Inflation Expectation Rate
    "T5YIFR": {
        "name": "5y5y inflation expectations",
        "unit": "%",
    },
    # ICE BofA US High Yield Index Option-Adjusted Spread
    "BAMLH0A0HYM2": {
        "name": "US High Yield OAS",
        "unit": "%",
    },
    # Trade Weighted U.S. Dollar Index: Broad, Goods and Services
    "DTWEXBGS": {
        "name": "USD broad index (DTWEXBGS)",
        "unit": "index",
    },
}


EQUITY_TICKERS = {
    "QQQ": {"name": "Nasdaq-100 proxy (QQQ)", "unit": "USD"},
    "NVDA": {"name": "NVIDIA", "unit": "USD"},
    "MSFT": {"name": "Microsoft", "unit": "USD"},
}


GDELT_QUERY = '(tariff OR sanctions OR "export controls" OR "military conflict" OR blockade)'

