from pathlib import Path

from fastapi.testclient import TestClient

from app.db import Database
from app import main
from app.settings import get_schedule_settings, save_schedule_settings


def test_schedule_settings_read_write_db_overrides(tmp_path: Path):
    test_db = Database(tmp_path / "schedule.sqlite")

    base = get_schedule_settings(test_db, env_refresh="1 2 * * *", env_report="3 4 * * *", env_timezone="UTC")
    assert base == {"refresh_cron": "1 2 * * *", "report_cron": "3 4 * * *", "timezone": "UTC"}

    saved, errors = save_schedule_settings(
        test_db,
        {"refresh_cron": "0 8 * * *", "report_cron": "15 8 * * *", "timezone": "Europe/Berlin"},
    )
    assert errors == {}
    assert saved["refresh_cron"] == "0 8 * * *"

    loaded = get_schedule_settings(test_db, env_refresh="1 2 * * *", env_report="3 4 * * *", env_timezone="UTC")
    assert loaded == {"refresh_cron": "0 8 * * *", "report_cron": "15 8 * * *", "timezone": "Europe/Berlin"}


def test_schedule_settings_validation_errors(tmp_path: Path):
    test_db = Database(tmp_path / "schedule.sqlite")
    saved, errors = save_schedule_settings(
        test_db,
        {"refresh_cron": "bad cron", "report_cron": "* * *", "timezone": "Mars/Phobos"},
    )
    assert saved is None
    assert "refresh_cron" in errors
    assert "report_cron" in errors
    assert "timezone" in errors


def test_schedule_api_put_and_get_and_apply(monkeypatch, tmp_path: Path):
    test_db = Database(tmp_path / "api_schedule.sqlite")
    monkeypatch.setattr(main, "db", test_db)

    applied = {"called": 0}

    def fake_apply():
        applied["called"] += 1

    monkeypatch.setattr(main, "_apply_scheduler_settings", fake_apply)

    client = TestClient(main.app)

    bad = client.put(
        "/api/schedule",
        json={"refresh_cron": "x", "report_cron": "5 7 * * *", "timezone": "Europe/Berlin"},
    )
    assert bad.status_code == 422
    assert bad.json()["ok"] is False
    assert "refresh_cron" in bad.json()["errors"]

    ok = client.put(
        "/api/schedule",
        json={"refresh_cron": "0 9 * * *", "report_cron": "10 9 * * *", "timezone": "Asia/Seoul"},
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["applied"] is True
    assert applied["called"] == 1

    got = client.get("/api/schedule")
    assert got.status_code == 200
    assert got.json()["schedule"] == {
        "refresh_cron": "0 9 * * *",
        "report_cron": "10 9 * * *",
        "timezone": "Asia/Seoul",
    }
