# FILE: app/core/database.py
import asyncio
import aiosqlite
from contextlib import closing
import logging
import json
from typing import List, Dict, Any, Tuple
from app.core.config import DB_PATH
import os
import sqlite3
import tempfile
import threading
import time

_global_db_conn = None
_db_state_lock = threading.Lock()

# Ретраи PRAGMA-фазы: транзиентный дубль при конкурентной инициализации может
# поймать SQLITE_BUSY на смене journal_mode (она не ждёт busy_timeout).
_PRAGMA_ATTEMPTS = 20
_PRAGMA_RETRY_DELAY = 0.05
_DB_CLOSE_TIMEOUT = 5.0
_DB_BACKUP_TIMEOUT = 30.0
_DB_APPLICATION_ID = 0x42425343  # ASCII "BBSC"
_REQUIRED_APP_SCHEMA = {
    "users": {"user_id", "group_name"},
    "schedule": {
        "group_name",
        "lesson_date",
        "time",
        "subject",
        "teacher",
        "location",
    },
}


class _BackupLockDeadline:
    """Progress callback: timeout only while SQLite reports BUSY/LOCKED."""

    def __init__(self, timeout: float):
        self.timeout = max(0.0, timeout)
        self.blocked_since = None

    def __call__(self, status: int, _remaining: int, _total: int) -> None:
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            now = time.monotonic()
            if self.blocked_since is None:
                self.blocked_since = now
            elif now - self.blocked_since >= self.timeout:
                raise TimeoutError("Database backup timed out waiting for SQLite locks")
        else:
            # Успешный step означает, что lock-прогресс восстановился. Большая
            # база может копироваться дольше timeout без ложного прерывания.
            self.blocked_since = None


def _validate_schedule_database(conn: sqlite3.Connection) -> int:
    """Проверяет целостность и минимальную идентичность базы bb_schedule.

    application_id используется для новых баз/бэкапов. Для старых баз с
    application_id=0 оставлена обратная совместимость через проверку двух
    базовых таблиц и ключевых колонок, существовавших с ранних версий проекта.
    Возвращает page_size источника.
    """
    integrity = conn.execute("PRAGMA integrity_check;").fetchone()
    if not integrity or str(integrity[0]).lower() != "ok":
        detail = integrity[0] if integrity else "no result"
        raise ValueError(f"Imported database failed integrity_check: {detail}")

    application_id = int(conn.execute("PRAGMA application_id;").fetchone()[0])
    if application_id not in (0, _DB_APPLICATION_ID):
        raise ValueError(
            f"Imported SQLite database belongs to another application "
            f"(application_id={application_id})"
        )

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for table, required_columns in _REQUIRED_APP_SCHEMA.items():
        if table not in tables:
            raise ValueError(f"Imported database is not bb_schedule: missing table {table}")
        columns = {
            row[1] for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
        missing = required_columns - columns
        if missing:
            raise ValueError(
                f"Imported database is not bb_schedule: table {table} "
                f"is missing columns {sorted(missing)}"
            )

    return int(conn.execute("PRAGMA page_size;").fetchone()[0])


def _online_backup_sync(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    timeout: float,
) -> None:
    """Копирует SQLite DB, ограничивая только ожидание BUSY/LOCKED."""
    progress = _BackupLockDeadline(timeout)
    source.backup(
        target,
        pages=256,
        progress=progress,
        sleep=0.05,
    )


def _is_connection_alive(conn) -> bool:
    """True, если соединение пригодно для работы.

    aiosqlite (>=0.15) официально поддерживает использование одного
    Connection из разных event loop'ов: future каждой операции создаётся в
    вызывающем loop'е. Поэтому привязка к loop'у не нужна — важна только
    живость: не закрыт sqlite-хэндл и работает воркер-поток.
    """
    if conn is None:
        return False
    if getattr(conn, "_connection", None) is None:
        return False
    if not getattr(conn, "_running", False):
        return False

    # aiosqlite может оставить _running=True и sqlite-handle непустым, если
    # worker аварийно завершился при доставке результата в уже закрытый loop.
    # В таком состоянии новая операция повиснет навсегда в очереди без worker.
    worker = getattr(conn, "_thread", None)
    if worker is not None and not worker.is_alive():
        return False
    return True


def _discard_connection_sync(conn):
    """Синхронно останавливает воркер-поток соединения.

    await conn.close() здесь нельзя: соединение может принадлежать уже
    закрытому loop'у. conn.stop() (aiosqlite >= 0.22.1) кладёт задачу в
    очередь живого воркера и не требует работающего loop'а.
    """
    if conn is None:
        return
    try:
        conn.stop()
    except Exception as e:
        logging.debug(f"Не удалось корректно остановить соединение: {e}")


async def _discard_connection_async(conn):
    """Асинхронный вариант остановки для вызовов из живого event loop.

    conn.stop() выполняется в executor-потоке: без работающего loop'а под ним
    aiosqlite не создаёт future для результата, и воркер завершается молча,
    без «Event loop is closed» при закрытии этого loop'а позже.
    """
    if conn is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _discard_connection_sync(conn)
        return
    await loop.run_in_executor(None, _discard_connection_sync, conn)


async def _create_connection() -> aiosqlite.Connection:
    """Полностью инициализирует НОВОЕ соединение или бросает исключение.

    Частично готовое соединение наружу не попадает никогда. SQLITE_BUSY на
    смене journal_mode (конкурирующий создатель/процесс) ретраится.
    """
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

    # aiosqlite создаёт worker Thread в connect(), но запускает его только при
    # await Connection. Переводим worker в daemon до старта. В норме shutdown
    # всё равно делает interrupt()+stop()+join; daemon — последняя страховка,
    # чтобы патологически зависший C/FS вызов не удерживал Python-процесс
    # бесконечно после bounded join.
    pending = aiosqlite.connect(DB_PATH)
    worker = getattr(pending, "_thread", None)
    if worker is None:
        raise RuntimeError(
            "Unsupported aiosqlite internals: worker thread is unavailable"
        )
    if not worker.is_alive():
        worker.daemon = True
    conn = await pending
    try:
        # busy_timeout: обычные блокировки (запись/чтение из второго процесса)
        # ждут освобождения вместо мгновенного "database is locked".
        await conn.execute("PRAGMA busy_timeout=5000;")
        for attempt in range(_PRAGMA_ATTEMPTS):
            try:
                await conn.execute("PRAGMA journal_mode=WAL;")
                await conn.execute("PRAGMA synchronous=NORMAL;")
                break
            except aiosqlite.OperationalError as e:
                if "locked" not in str(e).lower() or attempt == _PRAGMA_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(_PRAGMA_RETRY_DELAY * (attempt + 1))
        conn.row_factory = aiosqlite.Row
    except BaseException:
        # Не оставляем сироту с живым воркер-потоком.
        await _discard_connection_async(conn)
        raise
    return conn


async def get_db_connection():
    """Возвращает глобальное подключение к БД.

    Соединение публикуется только ПОЛНОСТЬЮ инициализированным. Создание
    кандидатов остаётся асинхронным и не держит межпоточный lock. Короткая
    compare-and-publish секция защищена threading.Lock, поэтому два event loop'а
    в разных OS threads не могут одновременно объявить себя победителями.
    """
    global _global_db_conn

    with _db_state_lock:
        current = _global_db_conn
        if _is_connection_alive(current):
            return current

    candidate = await _create_connection()

    # Никаких await под threading.Lock: он защищает только глобальный указатель.
    with _db_state_lock:
        incumbent = _global_db_conn
        if _is_connection_alive(incumbent):
            winner = incumbent
            old = None
            published = False
        else:
            old = incumbent
            _global_db_conn = candidate
            winner = candidate
            published = True

    if not published:
        # Нас опередили: используем победителя, свой дубль останавливаем.
        await _discard_connection_async(candidate)
        return winner

    if old is not None:
        # Мёртвый предшественник: останавливаем его worker, уже не держа lock.
        await _discard_connection_async(old)
    return winner


def _stop_and_join_connection_sync(conn, timeout: float) -> None:
    """Останавливает aiosqlite worker без зависимости от event loop.

    ``asyncio.wait_for(conn.close())`` не является жёстким timeout: wait_for
    после таймаута ждёт завершения cancellation, а ``Connection.close()`` в
    своём finally снова ждёт worker future. Если worker умер в этот момент,
    shutdown опять зависнет. Поэтому глобальный shutdown использует специально
    добавленный в aiosqlite 0.22.1 synchronous ``stop()`` и bounded thread.join.
    """
    if conn is None:
        return

    # sqlite3.Connection.interrupt() допускается вызывать из другого thread и
    # помогает завершить длинный текущий SQL до stop-sentinel.
    underlying = getattr(conn, "_connection", None)
    if underlying is not None:
        try:
            underlying.interrupt()
        except Exception:
            pass

    _discard_connection_sync(conn)

    worker = getattr(conn, "_thread", None)
    if worker is not None and worker.is_alive():
        worker.join(timeout=max(0.0, timeout))
        if worker.is_alive():
            logging.warning(
                "aiosqlite worker не завершился за %.1f сек после stop()",
                timeout,
            )


async def close_db_connection():
    """Безопасно и с ограниченным ожиданием закрывает глобальное подключение.

    Указатель отвязывается атомарно до await. Сам worker останавливается в
    executor-потоке через ``Connection.stop()`` и bounded ``Thread.join`` — это
    работает и для уже умершего worker, и при закрытом/отменённом event loop.
    """
    global _global_db_conn
    with _db_state_lock:
        conn = _global_db_conn
        _global_db_conn = None

    if conn is None:
        return


    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(
        None, _stop_and_join_connection_sync, conn, _DB_CLOSE_TIMEOUT
    )
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Executor job нельзя принудительно отменить. Дожидаемся его bounded
        # join, чтобы вызывающий shutdown не завершился раньше DB cleanup.
        try:
            await worker
        finally:
            raise


def _create_database_snapshot_file_sync() -> str:
    """Создаёт согласованный snapshot live DB через Online Backup API."""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Database does not exist: {DB_PATH}")

    db_dir = os.path.dirname(DB_PATH) or "."
    os.makedirs(db_dir, exist_ok=True)
    fd, snapshot_path = tempfile.mkstemp(
        suffix=".db", prefix="bb_backup_", dir=db_dir
    )
    os.close(fd)
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=0.1)) as source:
            source.execute("PRAGMA busy_timeout=100;")
            with closing(sqlite3.connect(snapshot_path, timeout=0.1)) as target:
                _online_backup_sync(
                    source, target, timeout=_DB_BACKUP_TIMEOUT
                )
                target.commit()
                # Не отправляем пользователю snapshot, который сам не проходит
                # integrity/schema validation. Это также маркирует legacy DB.
                _validate_schedule_database(target)
                target.execute(f"PRAGMA application_id={_DB_APPLICATION_ID};")
                target.commit()
        return snapshot_path
    except BaseException:
        try:
            os.remove(snapshot_path)
        except FileNotFoundError:
            pass
        raise


async def create_database_snapshot_file() -> str:
    """Асинхронно создаёт временный согласованный `.db` для отправки backup."""
    worker = asyncio.create_task(asyncio.to_thread(_create_database_snapshot_file_sync))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Не оставляем backup thread и временный файл жить после scheduler job.
        try:
            path = await worker
        except Exception:
            path = None
        if path:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        raise


def _restore_database_file_sync(raw: bytes) -> None:
    """Восстанавливает DB через SQLite Online Backup API.

    Файл ``DB_PATH`` не заменяется и не truncate'ится. Источник сначала
    проходит integrity/schema validation и проверку page_size, поэтому чужая
    SQLite DB или несовместимый WAL restore не затронут production DB.
    """
    db_dir = os.path.dirname(DB_PATH) or "."
    os.makedirs(db_dir, exist_ok=True)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".db", prefix="bb_import_", dir=db_dir, delete=False
        ) as tmp:
            tmp.write(raw)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name

        with closing(sqlite3.connect(tmp_path, timeout=0.1)) as source:
            source.execute("PRAGMA busy_timeout=100;")
            source_page_size = _validate_schedule_database(source)

            with closing(sqlite3.connect(DB_PATH, timeout=0.1)) as target:
                target.execute("PRAGMA busy_timeout=100;")
                target_page_size = int(
                    target.execute("PRAGMA page_size;").fetchone()[0]
                )
                target_journal = str(
                    target.execute("PRAGMA journal_mode;").fetchone()[0]
                ).lower()

                # SQLite возвращает SQLITE_READONLY при backup в WAL
                # destination с другим page_size. Отдаём понятную ошибку до
                # начала destructive copy.
                if target_journal == "wal" and source_page_size != target_page_size:
                    raise ValueError(
                        "Imported database page_size is incompatible with the live "
                        f"WAL database ({source_page_size} != {target_page_size})"
                    )

                _online_backup_sync(
                    source, target, timeout=_DB_BACKUP_TIMEOUT
                )
                target.execute(f"PRAGMA application_id={_DB_APPLICATION_ID};")
                target.commit()
    finally:
        if tmp_path is not None:
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass


async def restore_database_bytes(raw: bytes) -> None:
    """Асинхронно восстанавливает общую SQLite DB из байтов ``.db``.

    Блокирующая stdlib sqlite3 backup-операция выполняется вне event loop.
    После backup существующий глобальный aiosqlite Connection остаётся
    пригодным: inode файла не менялся, а изменения внесены транзакционно через
    SQLite.
    """
    if not raw:
        raise ValueError("Imported database is empty")

    # to_thread сам по себе не прекращает underlying thread при cancellation.
    # Shield + явное ожидание гарантируют, что JobRegistry.shutdown() не сочтёт
    # db_import завершённым, пока SQLite backup ещё меняет production DB.
    worker = asyncio.create_task(asyncio.to_thread(_restore_database_file_sync, raw))
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await worker
        except Exception as e:
            logging.debug(f"DB restore worker завершился с ошибкой при shutdown: {e}")
        raise

async def initialize_database():
    """Создает все необходимые таблицы, если они не существуют."""
    db = await get_db_connection()
    # Маркер приложения в заголовке SQLite. Старые базы с application_id=0
    # остаются совместимыми и будут помечены при следующей инициализации.
    await db.execute(f"PRAGMA application_id={_DB_APPLICATION_ID};")
        
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            group_name TEXT,
            record_book_number TEXT,
            settings TEXT
        )
    """)
    
    # Миграции
    try:
        await db.execute("ALTER TABLE users ADD COLUMN record_book_number TEXT")
        await db.commit()
    except aiosqlite.OperationalError:
        pass

    try:
        await db.execute("ALTER TABLE users ADD COLUMN settings TEXT")
        await db.commit()
    except aiosqlite.OperationalError:
        pass
        
    try:
        await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        await db.commit()
    except aiosqlite.OperationalError:
        pass
        
    try:
        await db.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
        await db.commit()
    except aiosqlite.OperationalError:
        pass
    
    await db.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
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
        )
    """)
    
    await db.execute("""
        CREATE TABLE IF NOT EXISTS broadcast_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_ids_json TEXT
        )
    """)
    
    # Кэш результатов сессии
    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_cache (
            record_book_number TEXT PRIMARY KEY,
            data_json TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Заметки к предметам
    await db.execute("""
        CREATE TABLE IF NOT EXISTS subject_notes (
            user_id INTEGER,
            subject_name TEXT,
            note_text TEXT,
            checklist_json TEXT,
            PRIMARY KEY (user_id, subject_name)
        )
    """)
    
    # Подписки на преподавателей
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teacher_subscriptions (
            user_id INTEGER,
            teacher_name TEXT,
            PRIMARY KEY (user_id, teacher_name)
        )
    """)

    # Рейтинговые данные студентов
    await db.execute("""
        CREATE TABLE IF NOT EXISTS rating_data (
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
        )
    """)
    
    # Отчисленные студенты
    await db.execute("""
        CREATE TABLE IF NOT EXISTS expelled_students (
            record_book TEXT PRIMARY KEY,
            enrollment_year INTEGER,
            expelled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            cluster_id INTEGER
        )
    """)
    
    # Миграция: перенос существующих отчисленных в новую таблицу
    try:
        await db.execute("""
            INSERT OR IGNORE INTO expelled_students (record_book, enrollment_year, cluster_id)
            SELECT record_book, enrollment_year, cluster_id
            FROM rating_data
            WHERE is_expelled = 1
        """)
        await db.execute("DELETE FROM rating_data WHERE is_expelled = 1")
        await db.commit()
    except aiosqlite.OperationalError as e:
        logging.error(f"Migration expelled_students error: {e}")
    
    # --- Индексы для оптимизации выборок ---
    await db.execute("CREATE INDEX IF NOT EXISTS idx_group_date ON schedule (group_name, lesson_date)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_faculty_course_group ON schedule (faculty, course, group_name)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_teacher_date ON schedule (teacher, lesson_date)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rating_cluster ON rating_data (cluster_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rating_year ON rating_data (enrollment_year)")

    # Маппинг кластеров на реальные группы расписания
    await db.execute("""
        CREATE TABLE IF NOT EXISTS cluster_groups (
            group_name TEXT PRIMARY KEY,
            cluster_id INTEGER NOT NULL,
            similarity REAL DEFAULT 0.0
        )
    """)

    # Статистика закрываемости предметов
    await db.execute("""
        CREATE TABLE IF NOT EXISTS subject_global_stats (
            subject TEXT PRIMARY KEY,
            total_students INTEGER DEFAULT 0,
            passed_students INTEGER DEFAULT 0,
            pass_rate REAL DEFAULT 0.0,
            total_persons INTEGER DEFAULT 0,
            passed_persons INTEGER DEFAULT 0,
            person_pass_rate REAL DEFAULT 0.0
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS cluster_subject_stats (
            cluster_id INTEGER,
            subject TEXT,
            total_students INTEGER DEFAULT 0,
            passed_students INTEGER DEFAULT 0,
            pass_rate REAL DEFAULT 0.0,
            total_persons INTEGER DEFAULT 0,
            passed_persons INTEGER DEFAULT 0,
            person_pass_rate REAL DEFAULT 0.0,
            PRIMARY KEY (cluster_id, subject)
        )
    """)

    # Миграции для новых колонок (persons)
    for table in ["subject_global_stats", "cluster_subject_stats"]:
        for col in ["total_persons", "passed_persons"]:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} INTEGER DEFAULT 0")
                await db.commit()
            except aiosqlite.OperationalError:
                pass
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN person_pass_rate REAL DEFAULT 0.0")
            await db.commit()
        except aiosqlite.OperationalError:
            pass


    # Таблица для логирования фоновых задач (статусы бота)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            status TEXT,          -- 'SUCCESS' или 'ERROR'
            details_json TEXT     -- JSON с дополнительной информацией 
        )
    """)
    
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teacher_stats (
            teacher TEXT,
            subject TEXT,
            group_name TEXT,
            total_students INTEGER,
            passed_students INTEGER,
            pass_rate REAL,
            academic_year TEXT,
            UNIQUE(teacher, subject)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_name ON job_logs (job_name)")

    await db.commit()


