from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import os
import time
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request, Body, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .analytics import compute_triggers
from .config import EQUITY_TICKERS, FRED_SERIES, GDELT_QUERY
from .db import Database
from .fetchers import fetch_fred_series, fetch_gdelt_daily_volume, fetch_stooq_daily_close
from .plugins import PLUGIN_REGISTRY, compute_plugin_triggers, get_enabled_plugins, list_plugins, set_enabled_plugins
from .settings import (
    load_thresholds,
    save_thresholds,
    reset_thresholds,
    get_last_alert_hash,
    set_last_alert_hash,
    set_last_alert_at,
    set_last_report_at,
    get_telegram_offset,
    set_telegram_offset,
    get_schedule_settings,
    save_schedule_settings,
    DEFAULT_REFRESH_CRON,
    DEFAULT_REPORT_CRON,
    DEFAULT_TIMEZONE,
)
from .notifications import send_email, send_telegram, fetch_telegram_updates, telegram_chat_id
from .reporting import generate_korean_report

APP_NAME = "global-risk-monitor"

DATA_DIR = Path(os.environ.get("GRM_DATA_DIR", str(Path.home() / ".global-risk-monitor")))
DB_PATH = Path(os.environ.get("GRM_DB_PATH", str(DATA_DIR / "risk_monitor.sqlite")))

ENV_SCHEDULE_CRON = os.environ.get("GRM_CRON", DEFAULT_REFRESH_CRON)  # minute hour day month dow
ENV_REPORT_CRON = os.environ.get("GRM_REPORT_CRON", DEFAULT_REPORT_CRON)
ENV_SCHEDULE_TIMEZONE = os.environ.get("GRM_TIMEZONE", DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE

AUTH_USER = os.environ.get("GRM_AUTH_USERNAME", "").strip()
AUTH_PASS = os.environ.get("GRM_AUTH_PASSWORD", "").strip()
AUTH_ENABLED = bool(AUTH_USER and AUTH_PASS)
SESSION_SECRET = os.environ.get("GRM_SESSION_SECRET", "change-me-grm-session-secret")
COOKIE_SECURE = os.environ.get("GRM_COOKIE_SECURE", "0").strip() in {"1", "true", "TRUE", "yes", "on"}
AUTH_MAX_ATTEMPTS = int(os.environ.get("GRM_AUTH_MAX_ATTEMPTS", "5") or "5")
AUTH_WINDOW_SECONDS = int(os.environ.get("GRM_AUTH_WINDOW_SECONDS", "300") or "300")
AUTH_BLOCK_SECONDS = int(os.environ.get("GRM_AUTH_BLOCK_SECONDS", "900") or "900")
TELEGRAM_COMMANDS_ENABLED = os.environ.get("GRM_TELEGRAM_COMMANDS", "1").strip() not in {"0", "false", "FALSE", "no", "off"}


def _err_text(e: Exception) -> str:
    s = str(e).strip()
    return s if s else e.__class__.__name__


def _parse_cron(expr: str, env_name: str = "GRM_CRON"):
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"{env_name} must be 5-part cron: 'm h dom mon dow'")
    m, h, dom, mon, dow = parts
    return dict(minute=m, hour=h, day=dom, month=mon, day_of_week=dow)


def _trigger_summary(triggers: List[dict]) -> Dict[str, int]:
    return {
        "ALERT": sum(1 for t in triggers if t.get("status") == "ALERT"),
        "WATCH": sum(1 for t in triggers if t.get("status") == "WATCH"),
        "OK": sum(1 for t in triggers if t.get("status") == "OK"),
    }


db = Database(DB_PATH)


def _current_schedule_settings() -> dict:
    return get_schedule_settings(
        db,
        env_refresh=ENV_SCHEDULE_CRON,
        env_report=ENV_REPORT_CRON,
        env_timezone=ENV_SCHEDULE_TIMEZONE,
    )


def _apply_scheduler_settings() -> None:
    global scheduler
    cfg = _current_schedule_settings()
    tz = ZoneInfo(cfg["timezone"])

    if scheduler is None:
        scheduler = BackgroundScheduler(timezone=tz)
    elif hasattr(scheduler, "configure"):
        scheduler.configure(timezone=tz)

    refresh_kwargs = _parse_cron(cfg["refresh_cron"], "refresh_cron")
    report_kwargs = _parse_cron(cfg["report_cron"], "report_cron")

    scheduler.add_job(
        _run_refresh_job,
        "cron",
        id="refresh_job",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=1800,
        max_instances=1,
        **refresh_kwargs,
    )
    scheduler.add_job(
        _run_report_job,
        "cron",
        id="report_job",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=1800,
        max_instances=1,
        **report_kwargs,
    )


templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Global Risk Monitor", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")


_login_attempts: dict[str, list[float]] = {}
_login_blocked_until: dict[str, float] = {}


def _auth_cookie_value() -> str:
    raw = f"{AUTH_USER}:{AUTH_PASS}:{SESSION_SECRET}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_ip_blocked(ip: str) -> bool:
    until = _login_blocked_until.get(ip, 0.0)
    return until > time.time()


def _register_login_failure(ip: str) -> None:
    now = time.time()
    arr = [t for t in _login_attempts.get(ip, []) if now - t <= AUTH_WINDOW_SECONDS]
    arr.append(now)
    _login_attempts[ip] = arr
    if len(arr) >= AUTH_MAX_ATTEMPTS:
        _login_blocked_until[ip] = now + AUTH_BLOCK_SECONDS
        _login_attempts[ip] = []


def _is_authenticated(request: Request) -> bool:
    if not AUTH_ENABLED:
        return True
    return request.cookies.get("grm_auth") == _auth_cookie_value()


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if not AUTH_ENABLED:
        return await call_next(request)

    path = request.url.path
    allowed_prefixes = ("/static",)
    allowed_exact = {"/login", "/api/health"}
    if path in allowed_exact or path.startswith(allowed_prefixes):
        return await call_next(request)

    if _is_authenticated(request):
        return await call_next(request)

    if path.startswith("/api"):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return RedirectResponse(url="/login", status_code=303)


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
            errors.append(f"FRED:{sid}:{_err_text(e)}")

    # Equities via Stooq
    for t in EQUITY_TICKERS.keys():
        try:
            df = await fetch_stooq_daily_close(t, start=start)
            rows = [(d.date().isoformat(), float(v)) for d, v in zip(df["date"], df["close"], strict=False)]
            db.upsert_observations(t, rows)
        except Exception as e:
            errors.append(f"STOOQ:{t}:{_err_text(e)}")

    # Optional plugins
    for pid in get_enabled_plugins(db):
        p = PLUGIN_REGISTRY.get(pid)
        if not p:
            continue
        try:
            if p.source == "fred":
                df = await fetch_fred_series(p.series_id, start=start)
                rows = [(d.date().isoformat(), float(v)) for d, v in zip(df["date"], df["value"], strict=False)]
                db.upsert_observations(p.series_id, rows)
            elif p.source == "stooq":
                df = await fetch_stooq_daily_close(p.series_id, start=start)
                rows = [(d.date().isoformat(), float(v)) for d, v in zip(df["date"], df["close"], strict=False)]
                db.upsert_observations(p.series_id, rows)
        except Exception as e:
            errors.append(f"PLUGIN:{pid}:{_err_text(e)}")

    # Geopolitics via GDELT (last 30 days for faster/more reliable API responses)
    g_start = dt.date.today() - dt.timedelta(days=30)
    g_end = dt.date.today()
    try:
        gdf = await fetch_gdelt_daily_volume(GDELT_QUERY, start=g_start, end=g_end)
        if not gdf.empty:
            rows = [(d.date().isoformat(), float(v)) for d, v in zip(gdf["date"], gdf["volume"], strict=False)]
            db.upsert_observations("GDELT", rows)
    except Exception as e:
        errors.append(f"GDELT:{_err_text(e)}")

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
    for pid in get_enabled_plugins(db):
        p = PLUGIN_REGISTRY.get(pid)
        if not p:
            continue
        if p.source == "fred":
            data[pid] = load_df(p.series_id, "value")
        elif p.source == "stooq":
            data[pid] = load_df(p.series_id, "close").rename(columns={"close": "value"})
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
    out = [t.__dict__ for t in triggers]
    out.extend(compute_plugin_triggers(data, get_enabled_plugins(db)))
    order = {"ALERT": 0, "WATCH": 1, "OK": 2}
    out.sort(key=lambda x: (order.get(x.get("status"), 9), x.get("key", "")))
    return out


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

    tz_name = _current_schedule_settings()["timezone"]
    tz = ZoneInfo(tz_name)
    now_local = dt.datetime.now(tz)
    objs = [TriggerResult(**t) for t in triggers]
    rep = generate_korean_report(objs, now=now_local.astimezone(dt.timezone.utc))

    counts = _trigger_summary(triggers)
    last_refresh = db.get_meta("last_refresh") or "(never)"
    last_errors = db.get_meta("last_refresh_errors") or "none"
    ops_block = (
        "\n\n운영 상태\n"
        f"- 최근 새로고침: {last_refresh}\n"
        f"- 트리거 집계: ALERT {counts['ALERT']} / WATCH {counts['WATCH']} / OK {counts['OK']}\n"
        f"- 최근 오류: {last_errors[:600]}"
    )

    return {
        "generated_at": now_local.isoformat(timespec="seconds"),
        "timezone": tz_name,
        "last_refresh": last_refresh,
        "last_errors": last_errors,
        "trigger_summary": counts,
        **rep,
        "text": f"{rep.get('text', '')}{ops_block}",
        "markdown": (
            f"{rep.get('markdown', '')}\n\n### 운영 상태\n"
            f"- 최근 새로고침: {last_refresh}\n"
            f"- 트리거 집계: ALERT {counts['ALERT']} / WATCH {counts['WATCH']} / OK {counts['OK']}\n"
            f"- 최근 오류: {last_errors[:600]}"
        ),
    }


async def _send_scheduled_report() -> List[str]:
    tz_name = _current_schedule_settings()["timezone"]
    now = dt.datetime.now(ZoneInfo(tz_name)).isoformat(timespec="seconds")
    triggers = _compute_triggers_from_db()
    payload = _build_report_payload(triggers)
    title = f"[GRM] 일일 리스크 리포트 ({now})"
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


def _run_refresh_job() -> None:
    asyncio.run(refresh_all())


def _run_report_job() -> None:
    asyncio.run(_send_scheduled_report())


def _run_telegram_poll_job() -> None:
    asyncio.run(_process_telegram_commands())


def _cmd_name(text: str) -> str:
    first = (text or "").strip().split()[0].lower() if text else ""
    return first.split("@")[0]


def _status_message() -> str:
    triggers = _compute_triggers_from_db()
    n_alert = sum(1 for t in triggers if t.get("status") == "ALERT")
    n_watch = sum(1 for t in triggers if t.get("status") == "WATCH")
    n_ok = sum(1 for t in triggers if t.get("status") == "OK")
    last_refresh = db.get_meta("last_refresh") or "(never)"
    last_err = db.get_meta("last_refresh_errors") or "none"
    return (
        "[GRM] status\n"
        f"- last_refresh: {last_refresh}\n"
        f"- triggers: ALERT={n_alert}, WATCH={n_watch}, OK={n_ok}\n"
        f"- last_errors: {last_err[:500]}"
    )


async def _process_telegram_commands() -> None:
    if not TELEGRAM_COMMANDS_ENABLED:
        return

    allowed_chat = telegram_chat_id().strip()
    if not allowed_chat:
        return

    offset = get_telegram_offset(db)
    updates, err = fetch_telegram_updates(offset=offset, timeout=0)
    if err:
        return

    new_offset = offset
    for upd in updates:
        try:
            uid = int(upd.get("update_id", 0))
            new_offset = max(new_offset or 0, uid + 1)
            msg = upd.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", "")).strip()
            if chat != allowed_chat:
                continue
            text = str(msg.get("text") or "").strip()
            cmd = _cmd_name(text)
            if not cmd:
                continue

            if cmd == "/help":
                send_telegram("[GRM] commands: /status, /refresh, /report, /triggers, /help")
            elif cmd == "/status":
                send_telegram(_status_message())
            elif cmd == "/triggers":
                triggers = _compute_triggers_from_db()
                top = [t for t in triggers if t.get("status") in {"ALERT", "WATCH"}][:10]
                if not top:
                    send_telegram("[GRM] 현재 ALERT/WATCH 트리거가 없습니다.")
                else:
                    lines = ["[GRM] Active triggers"]
                    for t in top:
                        lines.append(f"- {t.get('status')} {t.get('name')}: {t.get('rationale')}")
                    send_telegram("\n".join(lines)[:3500])
            elif cmd == "/refresh":
                send_telegram("[GRM] 수동 데이터 새로고침을 시작합니다...")
                await refresh_all()
                send_telegram("[GRM] 새로고침 완료. " + _status_message())
            elif cmd == "/report":
                payload = _build_report_payload(_compute_triggers_from_db())
                send_telegram(payload.get("text", "[GRM] report unavailable")[:3500])
        except Exception:
            continue

    if new_offset is not None:
        set_telegram_offset(db, int(new_offset))

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "auth_enabled": AUTH_ENABLED, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, username: str = Form(""), password: str = Form("")):
    if not AUTH_ENABLED:
        return RedirectResponse(url="/", status_code=303)

    ip = _client_ip(request)
    if _is_ip_blocked(ip):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "auth_enabled": AUTH_ENABLED, "error": "Too many attempts. Please try again later."},
            status_code=429,
        )

    if username == AUTH_USER and password == AUTH_PASS:
        _login_attempts.pop(ip, None)
        _login_blocked_until.pop(ip, None)
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("grm_auth", _auth_cookie_value(), httponly=True, samesite="lax", secure=COOKIE_SECURE)
        return resp

    _register_login_failure(ip)
    return templates.TemplateResponse("login.html", {"request": request, "auth_enabled": AUTH_ENABLED, "error": "Invalid credentials"}, status_code=401)


@app.post("/logout")
async def logout(request: Request):
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("grm_auth")
    return resp


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
            "auth_enabled": AUTH_ENABLED,
            "schedule": _current_schedule_settings(),
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


@app.get("/api/schedule")
async def api_get_schedule():
    return {"ok": True, "schedule": _current_schedule_settings()}


@app.put("/api/schedule")
async def api_put_schedule(payload: Dict = Body(...)):
    _saved, errors = save_schedule_settings(db, payload if isinstance(payload, dict) else {})
    if errors:
        return JSONResponse({"ok": False, "errors": errors}, status_code=422)

    apply_error = None
    try:
        _apply_scheduler_settings()
    except Exception as e:
        apply_error = str(e)

    result = _current_schedule_settings()
    return {
        "ok": True,
        "schedule": result,
        "applied": apply_error is None,
        "message": "Saved and applied immediately." if apply_error is None else "Saved. Apply failed now, but settings will be used after restart.",
        "apply_error": apply_error,
    }


@app.get("/api/plugins")
async def api_get_plugins():
    return list_plugins(db)


@app.put("/api/plugins")
async def api_put_plugins(payload: Dict = Body(...)):
    enabled = payload.get("enabled", []) if isinstance(payload, dict) else []
    if not isinstance(enabled, list):
        return {"ok": False, "error": "enabled must be a list"}
    ids = set_enabled_plugins(db, [str(x) for x in enabled])
    return {"ok": True, "enabled": ids, **list_plugins(db)}


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
    scheduler = BackgroundScheduler(timezone=ZoneInfo(_current_schedule_settings()["timezone"]))

    try:
        _apply_scheduler_settings()
    except Exception:
        # If schedule config is broken, skip cron scheduling (manual refresh/report still works).
        pass

    # Telegram command polling (default on)
    scheduler.add_job(
        _run_telegram_poll_job,
        "interval",
        seconds=5,
        id="telegram_commands",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )

    scheduler.start()

    # Initialize Telegram update offset once to avoid replaying old commands.
    if TELEGRAM_COMMANDS_ENABLED and get_telegram_offset(db) is None:
        updates, _err = fetch_telegram_updates(offset=None, timeout=0)
        if updates:
            try:
                last_uid = int(updates[-1].get("update_id", 0))
                set_telegram_offset(db, last_uid + 1)
            except Exception:
                pass

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

