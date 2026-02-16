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
- `GRM_SSL_VERIFY` TLS verify policy for HTTP fetchers (`1` default, `0` to disable, or path to CA bundle)
- `GRM_HOST` bind host for server (default `127.0.0.1`; use `0.0.0.0` for external access)
- `GRM_PORT` bind port (default `8000`)
- `GRM_AUTH_USERNAME`, `GRM_AUTH_PASSWORD` enable login protection
- `GRM_SESSION_SECRET` session signing key (set a strong random value in production)
- `GRM_COOKIE_SECURE` set `1` when serving behind HTTPS
- `GRM_AUTH_MAX_ATTEMPTS` (default `5`) max login attempts per window
- `GRM_AUTH_WINDOW_SECONDS` (default `300`) attempt window
- `GRM_AUTH_BLOCK_SECONDS` (default `900`) temporary block duration

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
## Plugin extensions (optional)

Enable optional signals in UI (**Settings & Reports → Manage plugins**) or via API:

- `GET /api/plugins`
- `PUT /api/plugins` with `{ "enabled": ["vix", "brent"] }`

Built-in plugin ideas:
- `vix`: VIX volatility stress trigger (`VIXCLS`)
- `brent`: Brent crude shock trigger (`DCOILBRENTEU`)
- `dram_price`: DRAM price proxy via semiconductor PPI (`PCU334413334413`, 30D/MoM-style)
- `ai_memory`: AI memory demand proxy via Micron 14D move (`MU.US`)

## Expose GRM over external IP safely

1. Set server bind + login in `.env`:

```bash
GRM_HOST=0.0.0.0
GRM_PORT=8000
GRM_AUTH_USERNAME=admin
GRM_AUTH_PASSWORD=change-this-strong-password
GRM_SESSION_SECRET=change-this-long-random-string
```

2. Prefer reverse proxy + TLS (Nginx/Caddy) and restrict inbound firewall to only required ports.
3. Do not expose without `GRM_AUTH_*` set.


### Recommended hardening for external access

- Put GRM behind HTTPS reverse proxy (Caddy/Nginx).
- Set `GRM_COOKIE_SECURE=1` when HTTPS is enabled.
- Restrict inbound firewall to only the proxy port(s) (typically 80/443).
- Keep GRM itself bound to localhost when using a reverse proxy on same host.

Example Caddyfile:

```caddy
your.domain.com {
  reverse_proxy 127.0.0.1:8000
}
```

