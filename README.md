# Global Risk Monitor (Local Web UI)

A lightweight local web dashboard that monitors weekly **risk triggers** and visualizes key series.

## What it tracks (default)
- **US 10Y yield** (FRED: DGS10)
- **5y5y inflation expectations** (FRED: T5YIFR)
- **High Yield OAS** (FRED: BAMLH0A0HYM2)
- **USD broad index** (FRED: DTWEXBGS)
- **Equities** via Stooq daily close: QQQ, NVDA, MSFT
- **Geopolitics headline volume** via GDELT timeline

Each indicator is turned into a **weekly trigger** (OK / WATCH / ALERT) using configurable thresholds.

## Quick start

```bash
git clone https://github.com/blue236/global-risk-monitor
cd global-risk-monitor
./run_local.sh
```

Open: http://127.0.0.1:8000

## Configuration

Environment variables:
- `GRM_DATA_DIR` (default: `~/.global-risk-monitor`)
- `GRM_DB_PATH` (default: `$GRM_DATA_DIR/risk_monitor.sqlite`)
- `GRM_CRON` weekly refresh schedule in 5-part cron (default: `0 7 * * MON`)

## Dev / tests

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
uvicorn app.main:app --reload
```

## Notes
- Uses public endpoints (no API keys): FRED `fredgraph.csv`, Stooq daily CSV, and GDELT timeline.
- If any upstream source changes format, refresh may fail; you can still run manual refresh and inspect logs.



## Notifications (Telegram + Email)

The app can send alerts when any trigger enters **ALERT** status (and only when the set of ALERTs changes to avoid spam).

### Telegram
Set these environment variables before running:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

### Email (SMTP)
Set:
- `SMTP_HOST` (e.g., smtp.gmail.com)
- `SMTP_PORT` (default: 587)
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM` (optional; defaults to SMTP_USER)
- `SMTP_TO` (comma-separated recipient list)

You can test notifications from the UI (Settings & Reports → “Send test notification”)
or via API: `POST /api/notify/test`.

## Thresholds UI

Open “Edit thresholds” to change weekly trigger thresholds (saved in the local SQLite DB).
Reset to defaults via the UI or API: `POST /api/thresholds/reset`.

## Weekly Korean report

Generate on-demand: `GET /api/report` (returns markdown + plain text).
A scheduled weekly report is also sent via Telegram/Email when configured.

- Refresh schedule: `GRM_CRON` (default: `0 7 * * MON`)
- Report schedule: `GRM_REPORT_CRON` (default: `5 7 * * MON`)