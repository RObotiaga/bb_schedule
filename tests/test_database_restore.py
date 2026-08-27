"""Регрессии безопасного импорта полной SQLite базы."""
import asyncio
import sqlite3
import threading

import pytest

import app.core.database as db_mod


@pytest.fixture(autouse=True)
def _tmp_db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path / "live.db"))
    yield


@pytest.fixture(autouse=True)
async def _clean_global_connection():
    await db_mod.close_db_connection()
    yield
    await db_mod.close_db_connection()


def _create_minimal_app_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY, group_name TEXT)"
    )
    conn.execute(
        """
        CREATE TABLE schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty TEXT, course TEXT, group_name TEXT, week_type TEXT,
            lesson_date TEXT, time TEXT, subject TEXT, teacher TEXT, location TEXT
        )
        """
    )


def _database_bytes(path, value: str, *, page_size: int | None = None) -> bytes:
    conn = sqlite3.connect(path)
    try:
        if page_size is not None:
            conn.execute(f"PRAGMA page_size={page_size}")
        _create_minimal_app_schema(conn)
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_restore_uses_online_backup_and_keeps_open_connections_valid(tmp_path):
    """Импорт не заменяет inode: уже открытые web/bot-like соединения живы."""
    live = await db_mod.get_db_connection()
    await live.execute("CREATE TABLE marker (value TEXT)")
    await live.execute("INSERT INTO marker VALUES ('old')")
    await live.commit()

    # Моделирует второй процесс/подключение, открытое до импорта.
    external = sqlite3.connect(db_mod.DB_PATH, timeout=10)
    try:
        source_path = tmp_path / "source.db"
        raw = _database_bytes(source_path, "new")

        await db_mod.restore_database_bytes(raw)

        # Глобальный aiosqlite Connection не закрывался/не заменялся.
        assert await db_mod.get_db_connection() is live
        cursor = await live.execute("SELECT value FROM marker")
        assert (await cursor.fetchone())[0] == "new"

        # Ранее открытое внешнее соединение также остаётся пригодным и видит
        # новую committed-снимок после Online Backup API.
        assert external.execute("SELECT value FROM marker").fetchone()[0] == "new"
    finally:
        external.close()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_restore_rejects_invalid_database_before_touching_live_db():
    live = await db_mod.get_db_connection()
    await live.execute("CREATE TABLE marker (value TEXT)")
    await live.execute("INSERT INTO marker VALUES ('old')")
    await live.commit()

    with pytest.raises((sqlite3.DatabaseError, ValueError)):
        await db_mod.restore_database_bytes(b"this is not sqlite")

    cursor = await live.execute("SELECT value FROM marker")
    assert (await cursor.fetchone())[0] == "old"


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_restore_has_deadline_when_live_db_is_write_locked(tmp_path, monkeypatch):
    live = await db_mod.get_db_connection()
    await live.execute("CREATE TABLE marker (value TEXT)")
    await live.execute("INSERT INTO marker VALUES ('old')")
    await live.commit()

    raw = _database_bytes(tmp_path / "locked_source.db", "new")
    monkeypatch.setattr(db_mod, "_DB_BACKUP_TIMEOUT", 0.2)

    # Отдельный процессоподобный writer удерживает RESERVED lock.
    locker = sqlite3.connect(db_mod.DB_PATH, timeout=1)
    locker.execute("BEGIN IMMEDIATE")
    locker.execute("UPDATE marker SET value='locked'")
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(db_mod.restore_database_bytes(raw), timeout=3)
    finally:
        locker.rollback()
        locker.close()

    cursor = await live.execute("SELECT value FROM marker")
    assert (await cursor.fetchone())[0] == "old"


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_restore_rejects_valid_but_unrelated_sqlite_database(tmp_path):
    live = await db_mod.get_db_connection()
    await live.execute("CREATE TABLE marker (value TEXT)")
    await live.execute("INSERT INTO marker VALUES ('old')")
    await live.commit()

    unrelated = tmp_path / "photos.db"
    conn = sqlite3.connect(unrelated)
    try:
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ValueError, match="not bb_schedule"):
        await db_mod.restore_database_bytes(unrelated.read_bytes())

    cursor = await live.execute("SELECT value FROM marker")
    assert (await cursor.fetchone())[0] == "old"


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_restore_rejects_page_size_mismatch_for_wal_destination(tmp_path):
    live = await db_mod.get_db_connection()
    await live.execute("CREATE TABLE marker (value TEXT)")
    await live.execute("INSERT INTO marker VALUES ('old')")
    await live.commit()

    cursor = await live.execute("PRAGMA page_size")
    live_page_size = int((await cursor.fetchone())[0])
    other_page_size = 8192 if live_page_size != 8192 else 16384
    raw = _database_bytes(
        tmp_path / "other_page_size.db", "new", page_size=other_page_size
    )

    with pytest.raises(ValueError, match="page_size"):
        await db_mod.restore_database_bytes(raw)

    cursor = await live.execute("SELECT value FROM marker")
    assert (await cursor.fetchone())[0] == "old"


def test_backup_lock_deadline_resets_after_progress(monkeypatch):
    ticks = iter([0.0, 10.0, 10.1, 10.2, 11.5])
    monkeypatch.setattr(db_mod.time, "monotonic", lambda: next(ticks))
    deadline = db_mod._BackupLockDeadline(timeout=1.0)

    # Большой успешный backup может длиться дольше timeout — это не ошибка.
    deadline(sqlite3.SQLITE_OK, 10, 100)
    deadline(sqlite3.SQLITE_OK, 9, 100)

    # BUSY начинает отдельное окно ожидания. Успешный progress его сбрасывает.
    deadline(sqlite3.SQLITE_BUSY, 9, 100)
    deadline(sqlite3.SQLITE_OK, 8, 100)
    deadline(sqlite3.SQLITE_BUSY, 8, 100)


def test_backup_lock_deadline_raises_only_after_continuous_busy(monkeypatch):
    ticks = iter([1.0, 1.4, 2.2])
    monkeypatch.setattr(db_mod.time, "monotonic", lambda: next(ticks))
    deadline = db_mod._BackupLockDeadline(timeout=1.0)
    deadline(sqlite3.SQLITE_BUSY, 10, 100)
    deadline(sqlite3.SQLITE_LOCKED, 10, 100)
    with pytest.raises(TimeoutError, match="SQLite locks"):
        deadline(sqlite3.SQLITE_BUSY, 10, 100)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_restore_cancellation_waits_for_underlying_backup_thread(monkeypatch):
    """Job cancellation не оставляет sqlite backup работать после task.done()."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def fake_restore(_raw):
        started.set()
        release.wait(timeout=2)
        finished.set()

    monkeypatch.setattr(db_mod, "_restore_database_file_sync", fake_restore)

    task = asyncio.create_task(db_mod.restore_database_bytes(b"db"))
    assert await asyncio.to_thread(started.wait, 1), "backup worker не стартовал"

    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done(), "Cancelled db_import вернулся до остановки backup worker"

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
