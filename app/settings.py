from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, Tuple

from .config import TriggerConfig


META_THRESHOLDS_KEY = "thresholds_json"
META_LAST_ALERT_HASH = "last_alert_hash"
META_LAST_ALERT_AT = "last_alert_at"
META_LAST_REPORT_AT = "last_report_at"
META_TELEGRAM_OFFSET = "telegram_offset"


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
