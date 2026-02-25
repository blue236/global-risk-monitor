import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Database
from app import main


def test_cron_defaults_are_daily():
    s = main._current_schedule_settings()
    assert s["refresh_cron"] == "0 7 * * *"
    assert s["report_cron"] == "5 7 * * *"
    assert s["timezone"] == "Europe/Berlin"


def test_build_report_payload_includes_summary_and_operational_meta(tmp_path: Path, monkeypatch):
    test_db = Database(tmp_path / "report.sqlite")
    test_db.set_meta("last_refresh", "2026-02-25T07:00:00")
    test_db.set_meta("last_refresh_errors", "none")
    monkeypatch.setattr(main, "db", test_db)

    triggers = [
        {
            "key": "DGS10",
            "name": "US 10Y Treasury yield",
            "status": "ALERT",
            "latest_date": "2026-02-25",
            "latest_value": 4.5,
            "wow_change": 45.0,
            "wow_change_unit": "bp",
            "rationale": "급등",
        },
        {
            "key": "QQQ",
            "name": "Nasdaq-100 proxy (QQQ)",
            "status": "WATCH",
            "latest_date": "2026-02-25",
            "latest_value": 490.0,
            "wow_change": -4.2,
            "wow_change_unit": "%",
            "rationale": "약세",
        },
    ]

    payload = main._build_report_payload(triggers)

    assert payload["trigger_summary"] == {"ALERT": 1, "WATCH": 1, "OK": 0}
    assert payload["last_refresh"] == "2026-02-25T07:00:00"
    assert payload["last_errors"] == "none"
    assert "운영 상태" in payload["text"]
    assert "최근 새로고침" in payload["markdown"]


def test_internal_scheduled_report_not_blocked_by_api_auth(monkeypatch):
    sent = {"telegram": [], "email": []}

    monkeypatch.setattr(main, "AUTH_ENABLED", True)
    monkeypatch.setattr(main, "AUTH_USER", "admin")
    monkeypatch.setattr(main, "AUTH_PASS", "secret")

    monkeypatch.setattr(
        main,
        "_compute_triggers_from_db",
        lambda: [
            {
                "key": "X",
                "name": "X",
                "status": "OK",
                "latest_date": "2026-02-25",
                "latest_value": 1.0,
                "wow_change": 0.0,
                "wow_change_unit": "%",
                "rationale": "ok",
            }
        ],
    )
    monkeypatch.setattr(main, "send_telegram", lambda body: sent["telegram"].append(body) or None)
    monkeypatch.setattr(main, "send_email", lambda subject, body: sent["email"].append((subject, body)) or None)
    monkeypatch.setattr(main, "set_last_report_at", lambda db, now: None)

    client = TestClient(main.app)
    r = client.get("/api/report")
    assert r.status_code == 401

    errors = asyncio.run(main._send_scheduled_report())
    assert errors == []
    assert sent["telegram"]
    assert sent["email"]


def test_scheduler_registers_refresh_report_and_telegram_jobs(monkeypatch):
    calls = []

    class FakeScheduler:
        def __init__(self, timezone=None):
            self.timezone = timezone

        def add_job(self, func, trigger, **kwargs):
            calls.append((func, trigger, kwargs))

        def start(self):
            return None

        def shutdown(self, wait=False):
            return None

    monkeypatch.setattr(main, "BackgroundScheduler", FakeScheduler)
    monkeypatch.setattr(main, "fetch_telegram_updates", lambda offset=None, timeout=0: ([], None))
    monkeypatch.setattr(main, "get_telegram_offset", lambda db: 1)

    async def no_refresh():
        return None

    monkeypatch.setattr(main, "refresh_all", no_refresh)

    asyncio.run(main.on_startup())

    job_ids = {kwargs.get("id") for _, _, kwargs in calls}
    assert "refresh_job" in job_ids
    assert "report_job" in job_ids
    assert "telegram_commands" in job_ids

    refresh_job = next(kwargs for _, _, kwargs in calls if kwargs.get("id") == "refresh_job")
    assert refresh_job["coalesce"] is True
    assert refresh_job["max_instances"] == 1
