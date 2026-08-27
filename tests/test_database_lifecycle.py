"""Регрессионные тесты жизненного цикла глобального aiosqlite-соединения.

Проверяют логику get_db_connection():
- конкурентную инициализацию (два одновременных первых вызова);
- ПОЗДНИЙ вызов во время чужой PRAGMA-инициализации: вызывающий должен
  получить только полностью готовое соединение (connect+PRAGMA+row_factory);
- работу из разных event loop'ов (aiosqlite >=0.15 официально поддерживает
  использование одного Connection из нескольких loop'ов);
- обнаружение worker thread, умершего при _running=True;
- атомарную публикацию между двумя event loop в разных OS threads;
- close/reopen без остаточного состояния;
- shutdown не зависает на уже умершем worker и имеет аварийный timeout.
"""
import asyncio
import threading

import aiosqlite
import pytest

import app.core.database as db_mod


@pytest.fixture(autouse=True)
def _tmp_db_path(tmp_path, monkeypatch):
    """Изолированный файл БД на каждый тест."""
    monkeypatch.setattr(db_mod, "DB_PATH", str(tmp_path / "lifecycle.db"))
    yield


@pytest.fixture(autouse=True)
async def _no_global_conn_between_tests():
    # Не теряем ссылку на живой Connection: это само создавало orphan-worker
    # и маскировалось глобальным PytestUnhandledThreadExceptionWarning.
    await db_mod.close_db_connection()
    yield
    await db_mod.close_db_connection()


def _run_in_fresh_loop(coro_factory, timeout: float = 20.0):
    """Запускает корутину в отдельном потоке со своим asyncio.run().

    Если код зависает (регрессия), тест падает по таймауту, а не висит вечно.
    """
    result: dict = {}

    def runner():
        result["value"] = asyncio.run(coro_factory())

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "Операция с БД зависла — регрессия жизненного цикла"
    return result["value"]


def _alive_connections(conns) -> int:
    """Сколько соединений имеют реально живой worker thread."""
    alive = 0
    for conn in conns:
        worker = getattr(conn, "_thread", None)
        if (
            getattr(conn, "_connection", None) is not None
            and getattr(conn, "_running", False)
            and worker is not None
            and worker.is_alive()
        ):
            alive += 1
    return alive


async def _wait_stopped(conn, timeout: float = 5.0) -> bool:
    """Дожидается фактической остановки воркера после conn.stop().

    stop() только ставит задачу в очередь воркер-потока — закрытие sqlite-хэндла
    происходит асинхронно, поэтому мгновенная проверка была бы гонкой.
    """
    import time
    deadline = time.monotonic() + timeout
    while getattr(conn, "_connection", None) is not None:
        if time.monotonic() > deadline:
            return False
        await asyncio.sleep(0.01)
    return True


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_concurrent_first_connection_is_shared(monkeypatch):
    """Два одновременных первых вызова получают одно и то же живое соединение.

    Допустимый транзиентный дубль (создание без блокировок) обязан быть
    синхронно остановлен: живым остаётся ровно одно соединение.
    """
    created = []
    orig_connect = db_mod.aiosqlite.connect

    async def counting_connect(*args, **kwargs):
        conn = await orig_connect(*args, **kwargs)
        created.append(conn)
        return conn

    monkeypatch.setattr(db_mod.aiosqlite, "connect", counting_connect)

    async def user():
        c = await db_mod.get_db_connection()
        await c.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
        await c.execute("INSERT INTO t VALUES (1)")
        await c.commit()
        return c

    c1, c2 = await asyncio.gather(user(), user())

    assert c1 is c2, "Получены разные соединения"
    assert getattr(c1, "_connection", None) is not None
    assert _alive_connections(created) == 1, (
        f"Живых соединений { _alive_connections(created) }, ожидалось 1 "
        "(дубль не был остановлен)"
    )
    losers = [conn for conn in created if conn is not c1]
    assert all([await _wait_stopped(conn) for conn in losers]), (
        "Проигравший worker физически не завершился после stop()"
    )


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_late_caller_during_init_gets_fully_initialized_connection(monkeypatch):
    """Поздний вызов во время PRAGMA-фазы другого вызова.

    Регрессия на публикацию частично готового соединения: пока первая корутина
    находится внутри инициализации (глобал ещё пуст), поздний вызывающий не
    должен получить соединение с row_factory=None или без применённых PRAGMA.
    Транзиентный дубль допустим, но к концу теста живым остаётся одно соединение.
    """
    pragma_started = asyncio.Event()
    release_pragma = asyncio.Event()
    created = []

    orig_connect = db_mod.aiosqlite.connect

    class ProxyConn:
        """Прокси: подвешивает первый journal_mode PRAGMA до сигнала теста."""

        def __init__(self, real):
            self._real = real

        async def execute(self, sql, *args):
            if sql.startswith("PRAGMA journal_mode"):
                pragma_started.set()
                await release_pragma.wait()
            return await self._real.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._real, name)

    slowed = {"used": False}

    async def slow_connect(*args, **kwargs):
        conn = await orig_connect(*args, **kwargs)
        created.append(conn)
        # Замедляем только ПЕРВОЕ соединение: поздний вызов пойдёт своим ходом.
        if slowed["used"]:
            return conn
        slowed["used"] = True
        return ProxyConn(conn)

    monkeypatch.setattr(db_mod.aiosqlite, "connect", slow_connect)

    task_first = asyncio.create_task(db_mod.get_db_connection())

    # Первая корутина вошла в PRAGMA-фазу и ещё ничего не опубликовала.
    await asyncio.wait_for(pragma_started.wait(), timeout=10)
    assert db_mod._global_db_conn is None, "Частично готовое соединение опубликовано"

    # Поздний вызов поверх чужой незавершённой инициализации.
    late = await asyncio.wait_for(db_mod.get_db_connection(), timeout=10)

    assert late.row_factory is aiosqlite.Row, (
        "Выдано соединение с незавершённой инициализацией"
    )
    cursor = await late.execute("SELECT 1")
    assert await cursor.fetchone() is not None
    assert db_mod._global_db_conn is late

    release_pragma.set()

    first = await asyncio.wait_for(task_first, timeout=10)

    # Победитель публикуется глобально; дубли не остаются живыми.
    assert first is db_mod._global_db_conn is late
    # Дубль первой корутины (created[0], замедленный) остановлен воркером.
    assert await _wait_stopped(created[0]), "Дубль соединения не остановлен"
    assert _alive_connections(created) == 1


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_connection_usable_across_event_loops():
    """Соединение остаётся рабочим при обращении из другого event loop'а.

    aiosqlite >=0.15 поддерживает несколько loop'ов для одного Connection:
    операции не должны зависать и результаты должны доходить до вызывающего.
    """
    async def create_and_insert():
        c = await db_mod.get_db_connection()
        await c.execute("CREATE TABLE IF NOT EXISTS ping (x INTEGER)")
        await c.execute("INSERT INTO ping VALUES (42)")
        await c.commit()
        return c

    conn_a = _run_in_fresh_loop(create_and_insert)
    assert conn_a is not None

    async def read_from_new_loop():
        c = await db_mod.get_db_connection()
        cursor = await c.execute("SELECT x FROM ping")
        row = await cursor.fetchone()
        return c, row[0]

    c, value = _run_in_fresh_loop(read_from_new_loop)
    assert value == 42
    assert c is not None


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_recovers_when_worker_thread_is_dead_but_running_flag_is_true():
    """Мёртвый worker нельзя считать живым только по _running/_connection.

    Это моделирует состояние aiosqlite после аварии worker при попытке
    call_soon_threadsafe() в уже закрытый event loop:
    _running=True, sqlite-handle ещё установлен, но _thread.is_alive() == False.
    """
    dead_thread = threading.Thread(target=lambda: None)
    dead_thread.start()
    dead_thread.join(timeout=5)
    assert not dead_thread.is_alive()

    class DeadConnection:
        _connection = object()
        _running = True
        _thread = dead_thread

        def stop(self):
            self._running = False

    dead = DeadConnection()
    assert not db_mod._is_connection_alive(dead)

    with db_mod._db_state_lock:
        db_mod._global_db_conn = dead

    replacement = await asyncio.wait_for(db_mod.get_db_connection(), timeout=10)
    assert replacement is not dead
    assert db_mod._is_connection_alive(replacement)
    await replacement.execute("SELECT 1")


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_parallel_event_loops_publish_exactly_one_connection(monkeypatch):
    """Два OS threads/loop одновременно публикуют только одного победителя."""
    created = []
    created_lock = threading.Lock()
    publish_barrier = threading.Barrier(2)
    orig_create = db_mod._create_connection

    async def synchronized_create():
        conn = await orig_create()
        with created_lock:
            created.append(conn)
        # Оба кандидата должны быть полностью готовы до compare-and-publish.
        await asyncio.to_thread(publish_barrier.wait, 15)
        return conn

    monkeypatch.setattr(db_mod, "_create_connection", synchronized_create)

    results = {}
    errors = []
    start_barrier = threading.Barrier(3)

    def worker(index):
        try:
            start_barrier.wait(timeout=15)
            results[index] = asyncio.run(db_mod.get_db_connection())
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(0,), daemon=True),
        threading.Thread(target=worker, args=(1,), daemon=True),
    ]
    for thread in threads:
        thread.start()

    start_barrier.wait(timeout=15)
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads), "Один из loop завис"
    assert not errors, errors
    assert results[0] is results[1] is db_mod._global_db_conn
    assert len(created) == 2, "Тест не заставил оба loop создать кандидата"

    # stop() проигравшего асинхронен относительно его worker. Проверяем не
    # только _running=False, а физическое завершение worker и закрытие handle.
    winner = db_mod._global_db_conn
    losers = [conn for conn in created if conn is not winner]
    assert len(losers) == 1
    assert await _wait_stopped(losers[0]), (
        "Проигравший worker физически не завершился после stop()"
    )
    assert _alive_connections(created) == 1


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_close_does_not_hang_when_worker_is_already_dead():
    """Shutdown не вызывает conn.close() у worker, который уже умер."""
    dead_thread = threading.Thread(target=lambda: None)
    dead_thread.start()
    dead_thread.join(timeout=2)
    assert not dead_thread.is_alive()

    class DeadConnection:
        _connection = object()
        _running = True
        _thread = dead_thread

        def __init__(self):
            self.close_called = False
            self.stop_called = False

        async def close(self):
            self.close_called = True
            await asyncio.Event().wait()

        def stop(self):
            self.stop_called = True
            self._running = False

    dead = DeadConnection()
    with db_mod._db_state_lock:
        db_mod._global_db_conn = dead

    await asyncio.wait_for(db_mod.close_db_connection(), timeout=2)

    assert db_mod._global_db_conn is None
    assert not dead.close_called, "close() мёртвого worker снова может зависнуть"
    assert dead.stop_called


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_close_has_hard_bound_when_worker_does_not_stop(monkeypatch):
    """Stuck worker не может удерживать shutdown дольше bounded join."""
    release = threading.Event()
    # Моделируем опасный non-daemon worker. Реальный Connection v6 переводит
    # worker в daemon до start; finally освобождает fake thread, чтобы сам тест
    # не удерживал pytest-процесс.
    stubborn_thread = threading.Thread(target=release.wait, daemon=False)
    stubborn_thread.start()

    class HangingConnection:
        _connection = object()
        _running = True
        _thread = stubborn_thread

        def __init__(self):
            self.stop_called = False

        async def close(self):
            raise AssertionError("global shutdown не должен использовать await close()")

        def stop(self):
            self.stop_called = True
            self._running = False

    hanging = HangingConnection()
    monkeypatch.setattr(db_mod, "_DB_CLOSE_TIMEOUT", 0.05)
    with db_mod._db_state_lock:
        db_mod._global_db_conn = hanging

    try:
        await asyncio.wait_for(db_mod.close_db_connection(), timeout=1)
        assert db_mod._global_db_conn is None
        assert hanging.stop_called
        assert stubborn_thread.is_alive(), "Тест не смоделировал stuck worker"
    finally:
        release.set()
        stubborn_thread.join(timeout=1)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_real_aiosqlite_worker_is_daemon_fail_safe():
    """Патологически stuck worker не должен удерживать выход Python process."""
    conn = await db_mod.get_db_connection()
    worker = getattr(conn, "_thread", None)
    assert worker is not None and worker.is_alive()
    assert worker.daemon is True


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_close_then_reopen_works():
    """close_db_connection() полностью сбрасывает состояние; повторное открытие работает."""
    c1 = await db_mod.get_db_connection()
    await db_mod.close_db_connection()
    assert db_mod._global_db_conn is None

    c2 = await db_mod.get_db_connection()
    assert c2 is not c1
    await c2.execute("SELECT 1")
