from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Dict, List

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .analytics import compute_triggers
from .config import EQUITY_TICKERS, FRED_SERIES, GDELT_QUERY
from .db import Database
from .fetchers import fetch_fred_series, fetch_gdelt_daily_volume, fetch_stooq_daily_close
from .settings import load_thresholds, save_thresholds, reset_thresholds, get_last_alert_hash, set_last_alert_hash, set_last_alert_at, set_last_report_at
from .notifications import send_email, send_telegram
from .reporting import generate_korean_report

APP_NAME = "global-risk-monitor"

DATA_DIR = Path(os.environ.get("GRM_DATA_DIR", str(Path.home() / ".global-risk-monitor")))
DB_PATH = Path(os.environ.get("GRM_DB_PATH", str(DATA_DIR / "risk_monitor.sqlite")))

SCHEDULE_CRON = os.environ.get("GRM_CRON", "0 7 * * MON")  # minute hour day month dow
REPORT_CRON = os.environ.get("GRM_REPORT_CRON", "5 7 * * MON")



def _parse_cron(expr: str):
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError("GRM_CRON must be 5-part cron: 'm h dom mon dow'")
    m, h, dom, mon, dow = parts
    return dict(minute=m, hour=h, day=dom, month=mon, day_of_week=dow)


db = Database(DB_PATH)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Global Risk Monitor", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


async def refresh_all() -> None:
    # 2 years of data is enough for charts and weekly diffs
    start = dt.date.today() - dt.timedelta(days=365 * 2)

    errors: list[str] = []

    # FRED
    for sid in FRED_SERIES.keys():
        try:
            df = await fetch_fred_series(sid, start=start)
            rows = [(d.date().isoformat(), float(v)) for d, v in zip(df["date"], df["value"], strict=False)]
            db.upsert_observations(sid, rows)
        except Exception as e:
            errors.append(f"FRED:{sid}:{e}")

    # Equities via Stooq
    for t in EQUITY_TICKERS.keys():
        try:
            df = await fetch_stooq_daily_close(t, start=start)
            rows = [(d.date().isoformat(), float(v)) for d, v in zip(df["date"], df["close"], strict=False)]
            db.upsert_observations(t, rows)
        except Exception as e:
            errors.append(f"STOOQ:{t}:{e}")

    # Geopolitics via GDELT (last 60 days)
    g_start = dt.date.today() - dt.timedelta(days=60)
    g_end = dt.date.today()
    try:
        gdf = await fetch_gdelt_daily_volume(GDELT_QUERY, start=g_start, end=g_end)
        if not gdf.empty:
            rows = [(d.date().isoformat(), float(v)) for d, v in zip(gdf["date"], gdf["volume"], strict=False)]
            db.upsert_observations("GDELT", rows)
    except Exception as e:
        errors.append(f"GDELT:{e}")

    db.set_meta("last_refresh", dt.datetime.now().isoformat(timespec="seconds"))
    db.set_meta("last_refresh_errors", " | ".join(errors)[:2000] if errors else "")
    
    # Notifications (only when ALERT set changes)
    try:
        triggers = _compute_triggers_from_db()
        n_errors = await _notify_if_needed(triggers)
        if n_errors:
            # keep last errors visible (append)
            prev = db.get_meta("last_refresh_errors") or ""
            joined = " | ".join([prev] + n_errors) if prev else " | ".join(n_errors)
            db.set_meta("last_refresh_errors", joined[:2000])
    except Exception as e:
        prev = db.get_meta("last_refresh_errors") or ""
        joined = " | ".join([prev, f"NOTIFY:{e}"]) if prev else f"NOTIFY:{e}"
        db.set_meta("last_refresh_errors", joined[:2000])

    
def _series_to_json(series_id: str, limit: int = 365 * 2) -> Dict:
    rows = db.fetch_series(series_id, limit=5000)
    if not rows:
        return {"id": series_id, "labels": [], "values": []}

    df = pd.DataFrame(rows, columns=["date", "value"])
    df["date"] = pd.to_datetime(df["date"])

    # For nicer charts, keep last N days
    df = df.sort_values("date")
    if series_id in EQUITY_TICKERS:
        # equity stored in value column already (close)
        y = df["value"].astype(float)
    else:
        y = df["value"].astype(float)

    labels = [d.date().isoformat() for d in df["date"].tolist()[-limit:]]
    values = [float(x) for x in y.tolist()[-limit:]]
    return {"id": series_id, "labels": labels, "values": values}


def _load_for_triggers() -> Dict[str, pd.DataFrame]:
    def load_df(sid: str, col: str = "value") -> pd.DataFrame:
        rows = db.fetch_series(sid, limit=5000)
        if not rows:
            return pd.DataFrame(columns=["date", col])
        df = pd.DataFrame(rows, columns=["date", col])
        df["date"] = pd.to_datetime(df["date"])
        df = df.dropna().sort_values("date")
        return df

    data = {
        "dgs10": load_df("DGS10", "value"),
        "t5yifr": load_df("T5YIFR", "value"),
        "hy_oas": load_df("BAMLH0A0HYM2", "value"),
        "dxy": load_df("DTWEXBGS", "value"),
        "qqq": load_df("QQQ", "close").rename(columns={"close": "close"}),
        "nvda": load_df("NVDA", "close").rename(columns={"close": "close"}),
        "msft": load_df("MSFT", "close").rename(columns={"close": "close"}),
        "geopolitics": load_df("GDELT", "volume").rename(columns={"volume": "volume"}),
    }
    return data



def _compute_triggers_from_db() -> List[dict]:
    data = _load_for_triggers()
    cfg, merged = load_thresholds(db)
    triggers = compute_triggers(
        dgs10=data["dgs10"],
        t5yifr=data["t5yifr"],
        hy_oas=data["hy_oas"],
        dxy=data["dxy"],
        qqq=data["qqq"],
        nvda=data["nvda"],
        msft=data["msft"],
        geopolitics=data["geopolitics"],
        cfg=cfg,
    )
    # triggers are dataclasses; convert to plain dicts for JSON and templates
    return [t.__dict__ for t in triggers]


async def _notify_if_needed(triggers: List[dict]) -> List[str]:
    """Send Telegram/Email if there are ALERTs and the alert set changed."""
    import hashlib
    now = dt.datetime.now().isoformat(timespec="seconds")
    alerts = [t for t in triggers if t.get("status") == "ALERT"]
    if not alerts:
        return []

    # stable hash based on keys + rationale (avoid spamming on refresh)
    key = "|".join([f"{a.get('key')}:{a.get('rationale')}" for a in alerts])
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()

    last = get_last_alert_hash(db)
    if last == h:
        return []

    title = f"[GRM] ALERT {len(alerts)}개 발생 ({now})"
    body_lines = [title, ""]
    for a in alerts:
        body_lines.append(f"- {a.get('name')}: WoW {a.get('wow_change'):+.2f}{a.get('wow_change_unit')} ({a.get('rationale')})")
    body = "\n".join(body_lines)

    errors: List[str] = []
    err_tg = send_telegram(body)
    if err_tg:
        errors.append(err_tg)

    err_mail = send_email(subject=title, body=body)
    if err_mail:
        errors.append(err_mail)

    set_last_alert_hash(db, h)
    set_last_alert_at(db, now)
    return errors


def _build_report_payload(triggers: List[dict]) -> dict:
    # rebuild TriggerResult-like objects shape for reporting
    from .analytics import TriggerResult
    import datetime as _dt

    objs = [TriggerResult(**t) for t in triggers]
    rep = generate_korean_report(objs, now=_dt.datetime.now(_dt.timezone.utc))
    return {"generated_at": _dt.datetime.now().isoformat(timespec="seconds"), **rep}


async def _send_weekly_report() -> List[str]:
    import datetime as _dt
    now = _dt.datetime.now().isoformat(timespec="seconds")
    triggers = _compute_triggers_from_db()
    payload = _build_report_payload(triggers)
    title = f"[GRM] 주간 리스크 리포트 ({now})"
    body = payload.get("text") if isinstance(payload, dict) else str(payload)

    errors: List[str] = []
    e1 = send_telegram(body)
    if e1:
        errors.append(e1)
    e2 = send_email(subject=title, body=body)
    if e2:
        errors.append(e2)

    set_last_report_at(db, now)
    return errors

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    last_refresh = db.get_meta("last_refresh")
    last_errors = db.get_meta("last_refresh_errors")

    triggers = _compute_triggers_from_db()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "triggers": triggers,
            "last_refresh": last_refresh,
            "last_errors": last_errors,
        },
    )


@app.post("/refresh")
async def refresh():
    await refresh_all()
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/series/{series_id}")
async def api_series(series_id: str):
    # Allow FRED IDs, tickers, and GDELT
    payload = _series_to_json(series_id)
    return JSONResponse(payload)


@app.get("/api/triggers")
async def api_triggers():
    triggers = _compute_triggers_from_db()
    return {"triggers": triggers, "last_refresh": db.get_meta("last_refresh"), "last_errors": db.get_meta("last_refresh_errors")}


@app.get("/api/thresholds")
async def api_get_thresholds():
    _cfg, merged = load_thresholds(db)
    return {"thresholds": merged}


@app.put("/api/thresholds")
async def api_put_thresholds(payload: Dict = Body(...)):
    merged = save_thresholds(db, payload)
    return {"ok": True, "thresholds": merged}



@app.post("/api/thresholds/reset")
async def api_reset_thresholds():
    th = reset_thresholds(db)
    return {"ok": True, "thresholds": th}

@app.get("/api/report")
async def api_report():
    triggers = _compute_triggers_from_db()
    return _build_report_payload(triggers)


@app.post("/api/notify/test")
async def api_test_notify():
    # Sends a test message (requires env configuration)
    msg = "[GRM] Test notification: 설정이 정상 동작합니다."
    errs = []
    e1 = send_telegram(msg)
    if e1:
        errs.append(e1)
    e2 = send_email(subject="[GRM] Test notification", body=msg)
    if e2:
        errs.append(e2)
    return {"ok": len(errs) == 0, "errors": errs}


@app.get("/api/health")
async def health():
    return {"ok": True, "db": str(DB_PATH)}


scheduler: BackgroundScheduler | None = None


@app.on_event("startup")
async def on_startup():
    global scheduler
    scheduler = BackgroundScheduler()

    try:
        cron = _parse_cron(SCHEDULE_CRON)
        scheduler.add_job(lambda: __import__("asyncio").run(refresh_all()), "cron", **cron)
        try:
            rcron = _parse_cron(REPORT_CRON)
            scheduler.add_job(lambda: __import__("asyncio").run(_send_weekly_report()), "cron", **rcron)
        except Exception:
            pass
    except Exception:
        # If cron misconfigured, skip scheduling (manual refresh still works)
        pass

    scheduler.start()

    # If never refreshed, do a first refresh automatically (can be disabled)
    auto = os.environ.get("GRM_AUTO_REFRESH", "1")
    if auto != "0" and db.get_meta("last_refresh") is None:
        try:
            await refresh_all()
        except Exception:
            # Startup should not fail if upstream is unreachable.
            pass


@app.on_event("shutdown")
def on_shutdown():
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        scheduler = None

