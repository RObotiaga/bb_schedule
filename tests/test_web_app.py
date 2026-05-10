import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:AABBCcDDEEFFGG")
os.environ.setdefault("ADMIN_ID", "42")


def _write_schema(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            group_name TEXT,
            record_book_number TEXT,
            settings TEXT,
            username TEXT,
            first_name TEXT
        );
        CREATE TABLE schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty TEXT,
            course TEXT,
            group_name TEXT,
            week_type TEXT,
            lesson_date TEXT,
            time TEXT,
            subject TEXT,
            teacher TEXT,
            location TEXT
        );
        CREATE TABLE job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT,
            start_time TEXT,
            end_time TEXT,
            status TEXT,
            details_json TEXT
        );
        CREATE TABLE teacher_subscriptions (user_id INTEGER, teacher_name TEXT, PRIMARY KEY (user_id, teacher_name));
        CREATE TABLE subject_notes (user_id INTEGER, subject_name TEXT, note_text TEXT, checklist_json TEXT, PRIMARY KEY (user_id, subject_name));
        CREATE TABLE session_cache (record_book_number TEXT PRIMARY KEY, data_json TEXT, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE rating_data (
            record_book TEXT PRIMARY KEY,
            enrollment_year INTEGER,
            subjects_json TEXT,
            total_subjects INTEGER DEFAULT 0,
            passed_subjects INTEGER DEFAULT 0,
            pass_rate REAL DEFAULT 0.0,
            cluster_id INTEGER,
            is_expelled INTEGER DEFAULT 0,
            last_academic_year TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE cluster_groups (group_name TEXT PRIMARY KEY, cluster_id INTEGER NOT NULL, similarity REAL DEFAULT 0.0);
        CREATE TABLE subject_global_stats (
            subject TEXT PRIMARY KEY,
            total_students INTEGER DEFAULT 0,
            passed_students INTEGER DEFAULT 0,
            pass_rate REAL DEFAULT 0.0,
            total_persons INTEGER DEFAULT 0,
            passed_persons INTEGER DEFAULT 0,
            person_pass_rate REAL DEFAULT 0.0
        );
        CREATE TABLE cluster_subject_stats (
            cluster_id INTEGER,
            subject TEXT,
            total_students INTEGER DEFAULT 0,
            passed_students INTEGER DEFAULT 0,
            pass_rate REAL DEFAULT 0.0,
            total_persons INTEGER DEFAULT 0,
            passed_persons INTEGER DEFAULT 0,
            person_pass_rate REAL DEFAULT 0.0,
            PRIMARY KEY (cluster_id, subject)
        );
        CREATE TABLE expelled_students (record_book TEXT PRIMARY KEY, enrollment_year INTEGER, expelled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, cluster_id INTEGER);
        CREATE TABLE teacher_stats (teacher TEXT, subject TEXT, group_name TEXT, total_students INTEGER, passed_students INTEGER, pass_rate REAL, academic_year TEXT, UNIQUE(teacher, subject));
        """
    )
    conn.execute(
        """
        INSERT INTO schedule (faculty, course, group_name, week_type, lesson_date, time, subject, teacher, location)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Факультет", "1", "ИС-101", "четная", "2026-05-10", "08:30", "Математика", "Иванов И.И.", "101"),
    )
    conn.execute("INSERT INTO users (user_id, group_name) VALUES (?, ?)", (7, "ИС-101"))
    conn.commit()
    conn.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.core import database

    asyncio.run(database.close_db_connection())
    db_path = tmp_path / "schedule.db"
    _write_schema(db_path)
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    from app.web.app import app

    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client

    asyncio.run(database.close_db_connection())


def _init_data(user_id: int, token: str = "123456789:AABBCcDDEEFFGG", auth_date: int | None = None):
    pairs = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "test-query",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_legacy_schedule_page_no_internal_server_error(client):
    response = client.get("/schedule?group=%D0%98%D0%A1-101")

    assert response.status_code == 200
    assert "Математика" in response.text


def test_legacy_root_redirects_by_user_id(client):
    response = client.get("/", params={"user_id": 7}, follow_redirects=False)

    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/schedule?group=%D0%98%D0%A1-101"


def test_valid_init_data_resolves_user(client):
    response = client.get("/api/me", headers={"X-Telegram-Init-Data": _init_data(7)})

    assert response.status_code == 200
    assert response.json()["group"] == "ИС-101"


def test_invalid_init_data_is_rejected(client):
    response = client.get("/api/me", headers={"X-Telegram-Init-Data": _init_data(7) + "broken"})

    assert response.status_code == 401


def test_admin_endpoint_requires_admin(client):
    user_response = client.get("/api/admin/jobs", headers={"X-Telegram-Init-Data": _init_data(7)})
    admin_response = client.get("/api/admin/jobs", headers={"X-Telegram-Init-Data": _init_data(42)})

    assert user_response.status_code == 403
    assert admin_response.status_code == 200
