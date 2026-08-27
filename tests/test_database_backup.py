"""Регрессии согласованного backup SQLite/WAL."""
import os
import sqlite3

import pytest

import app.core.database as db_mod
import app.services.backup as backup_mod


def _create_minimal_app_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, group_name TEXT)")
    conn.execute(
        """
        CREATE TABLE schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty TEXT, course TEXT, group_name TEXT, week_type TEXT,
            lesson_date TEXT, time TEXT, subject TEXT, teacher TEXT, location TEXT
        )
        """
    )


@pytest.fixture(autouse=True)
def _tmp_db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path / "live.db"))
    yield


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_snapshot_includes_committed_data_still_in_wal():
    """Простой copy(DB_PATH) был бы stale; Online Backup обязан видеть WAL."""
    live = sqlite3.connect(db_mod.DB_PATH)
    snapshot_path = None
    try:
        assert live.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        live.execute("PRAGMA wal_autocheckpoint=0")
        _create_minimal_app_schema(live)
        live.execute("CREATE TABLE marker (value TEXT)")
        live.execute("INSERT INTO marker VALUES ('old')")
        live.commit()
        live.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        # Это изменение committed, но специально оставлено в WAL.
        live.execute("UPDATE marker SET value='new'")
        live.commit()
        wal_path = db_mod.DB_PATH + "-wal"
        assert os.path.exists(wal_path) and os.path.getsize(wal_path) > 0

        snapshot_path = await db_mod.create_database_snapshot_file()
        with sqlite3.connect(snapshot_path) as snapshot:
            assert snapshot.execute("SELECT value FROM marker").fetchone()[0] == "new"
            assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert int(snapshot.execute("PRAGMA application_id").fetchone()[0]) == db_mod._DB_APPLICATION_ID
    finally:
        live.close()
        if snapshot_path:
            try:
                os.remove(snapshot_path)
            except FileNotFoundError:
                pass


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_snapshot_rejects_non_application_database():
    conn = sqlite3.connect(db_mod.DB_PATH)
    try:
        conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="not bb_schedule"):
        await db_mod.create_database_snapshot_file()

    leftovers = [
        name for name in os.listdir(os.path.dirname(db_mod.DB_PATH))
        if name.startswith("bb_backup_") and name.endswith(".db")
    ]
    assert leftovers == []


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_send_db_backup_sends_snapshot_not_live_file(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot.db"
    snapshot.write_bytes(b"consistent-snapshot")

    async def fake_snapshot():
        return str(snapshot)

    class FakeInputFile:
        def __init__(self, path, filename):
            self.path = path
            self.filename = filename

    sent = {}

    class FakeBot:
        async def send_document(self, **kwargs):
            sent.update(kwargs)
            document = kwargs["document"]
            assert document.path == str(snapshot)
            assert snapshot.read_bytes() == b"consistent-snapshot"

    monkeypatch.setattr(backup_mod, "ADMIN_ID", 123)
    monkeypatch.setattr(backup_mod, "create_database_snapshot_file", fake_snapshot)
    monkeypatch.setattr(backup_mod, "FSInputFile", FakeInputFile)

    await backup_mod.send_db_backup(FakeBot())

    assert sent["chat_id"] == 123
    assert sent["document"].filename == "schedule_backup.db"
    assert not snapshot.exists(), "Временный snapshot должен удаляться после отправки"
