from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from .config import TriggerConfig


META_THRESHOLDS_KEY = "thresholds_json"
META_LAST_ALERT_HASH = "last_alert_hash"
META_LAST_ALERT_AT = "last_alert_at"
META_LAST_REPORT_AT = "last_report_at"
META_TELEGRAM_OFFSET = "telegram_offset"
META_SCHEDULE_CRON = "schedule_refresh_cron"
META_SCHEDULE_REPORT_CRON = "schedule_report_cron"
META_SCHEDULE_TIMEZONE = "schedule_timezone"

DEFAULT_REFRESH_CRON = "0 7 * * *"
DEFAULT_REPORT_CRON = "5 7 * * *"
DEFAULT_TIMEZONE = "Europe/Berlin"


def default_thresholds() -> Dict[str, float]:
    return asdict(TriggerConfig())


def load_thresholds(db) -> Tuple[TriggerConfig, Dict[str, float]]:
    """Return (TriggerConfig, raw_dict) using stored overrides if present."""
    defaults = default_thresholds()
    raw = {}
    try:
        s = db.get_meta(META_THRESHOLDS_KEY)
        if s:
            raw = json.loads(s)
    except Exception:
        raw = {}

    merged = dict(defaults)
    for k, v in (raw or {}).items():
        if k in merged:
            try:
                merged[k] = float(v)
            except Exception:
                pass
    return TriggerConfig(**merged), merged


def save_thresholds(db, payload: Dict[str, Any]) -> Dict[str, float]:
    defaults = default_thresholds()
    cleaned: Dict[str, float] = {}
    for k, v in payload.items():
        if k not in defaults:
            continue
        cleaned[k] = float(v)
    db.set_meta(META_THRESHOLDS_KEY, json.dumps(cleaned, ensure_ascii=False))
    merged = dict(defaults)
    merged.update(cleaned)
    return merged


def get_last_alert_hash(db) -> str | None:
    return db.get_meta(META_LAST_ALERT_HASH)


def set_last_alert_hash(db, h: str) -> None:
    db.set_meta(META_LAST_ALERT_HASH, h)


def set_last_alert_at(db, iso_ts: str) -> None:
    db.set_meta(META_LAST_ALERT_AT, iso_ts)


def get_last_alert_at(db) -> str | None:
    return db.get_meta(META_LAST_ALERT_AT)


def set_last_report_at(db, iso_ts: str) -> None:
    db.set_meta(META_LAST_REPORT_AT, iso_ts)


def get_last_report_at(db) -> str | None:
    return db.get_meta(META_LAST_REPORT_AT)


def get_telegram_offset(db) -> int | None:
    s = db.get_meta(META_TELEGRAM_OFFSET)
    if not s:
        return None
    try:
        return int(s)
    except Exception:
        return None


def set_telegram_offset(db, offset: int) -> None:
    db.set_meta(META_TELEGRAM_OFFSET, str(int(offset)))


def reset_thresholds(db) -> Dict[str, float]:
    db.set_meta(META_THRESHOLDS_KEY, json.dumps({}, ensure_ascii=False))
    return default_thresholds()


def _validate_cron(expr: str, field_name: str) -> str:
    value = (expr or "").strip()
    if not value:
        raise ValueError(f"{field_name} is required")
    try:
        CronTrigger.from_crontab(value)
    except Exception as e:
        raise ValueError(f"{field_name} is invalid cron: {e}")
    return value


def _validate_timezone(tz_name: str) -> str:
    value = (tz_name or "").strip()
    if not value:
        raise ValueError("timezone is required")
    try:
        ZoneInfo(value)
    except Exception:
        raise ValueError("timezone is invalid (use IANA timezone, e.g. Europe/Berlin)")
    return value


def get_schedule_settings(db, env_refresh: str | None, env_report: str | None, env_timezone: str | None) -> Dict[str, str]:
    refresh = (db.get_meta(META_SCHEDULE_CRON) or env_refresh or DEFAULT_REFRESH_CRON).strip()
    report = (db.get_meta(META_SCHEDULE_REPORT_CRON) or env_report or DEFAULT_REPORT_CRON).strip()
    timezone = (db.get_meta(META_SCHEDULE_TIMEZONE) or env_timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    return {
        "refresh_cron": refresh,
        "report_cron": report,
        "timezone": timezone,
    }


def validate_schedule_payload(payload: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    errors: Dict[str, str] = {}
    out: Dict[str, str] = {}

    for in_key, out_key, validator in [
        ("refresh_cron", "refresh_cron", _validate_cron),
        ("report_cron", "report_cron", _validate_cron),
    ]:
        try:
            out[out_key] = validator(str(payload.get(in_key, "")), in_key)
        except ValueError as e:
            errors[in_key] = str(e)

    try:
        out["timezone"] = _validate_timezone(str(payload.get("timezone", "")))
    except ValueError as e:
        errors["timezone"] = str(e)

    return out, errors


def save_schedule_settings(db, payload: Dict[str, Any]) -> Tuple[Dict[str, str] | None, Dict[str, str]]:
    cleaned, errors = validate_schedule_payload(payload)
    if errors:
        return None, errors

    db.set_meta(META_SCHEDULE_CRON, cleaned["refresh_cron"])
    db.set_meta(META_SCHEDULE_REPORT_CRON, cleaned["report_cron"])
    db.set_meta(META_SCHEDULE_TIMEZONE, cleaned["timezone"])
    return cleaned, {}
