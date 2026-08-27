"""Регрессии порядка shutdown фоновых задач перед закрытием общей БД."""
import asyncio

import pytest
from fastapi import HTTPException

from app.bot.main import (
    _shutdown_dispatcher_handlers,
    _shutdown_scheduler,
    _tracked_scheduler_job,
)
from app.web.app import JobRegistry


class _FakeScheduler:
    def __init__(self):
        self.running = True
        self.paused = False
        self.shutdown_called = False

    def pause(self):
        self.paused = True

    def shutdown(self, wait=False):
        self.shutdown_called = True
        self.running = False


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_bot_scheduler_shutdown_waits_for_running_job_cleanup():
    active_tasks: set[asyncio.Task] = set()
    stopping = asyncio.Event()
    started = asyncio.Event()
    finalized = asyncio.Event()

    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalized.set()

    task = asyncio.create_task(
        _tracked_scheduler_job(active_tasks, stopping, job)
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    assert task in active_tasks

    scheduler = _FakeScheduler()
    await _shutdown_scheduler(scheduler, active_tasks, stopping)

    assert stopping.is_set()
    assert scheduler.paused
    assert scheduler.shutdown_called
    assert task.done()
    assert finalized.is_set()
    assert not active_tasks


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_web_job_registry_shutdown_cancels_and_awaits_tasks():
    registry = JobRegistry()
    registry.start_accepting()

    started = asyncio.Event()
    finalized = asyncio.Event()

    async def job():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalized.set()

    registry.start("schedule_sync", job)
    await asyncio.wait_for(started.wait(), timeout=2)

    await registry.shutdown()

    assert finalized.is_set()
    assert not [task for task in registry._tasks if not task.done()]
    assert registry.snapshot("schedule_sync")["status"] == "cancelled"

    with pytest.raises(HTTPException) as exc:
        registry.start("schedule_sync", job)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_web_job_registry_reserves_name_before_task_starts():
    registry = JobRegistry()
    registry.start_accepting()

    calls = 0
    release = asyncio.Event()

    async def job():
        nonlocal calls
        calls += 1
        await release.wait()

    first = registry.start("rating_update", job)
    second = registry.start("rating_update", job)

    assert first["status"] == "queued"
    assert second["status"] == "queued"
    assert len(registry._tasks) == 1

    await asyncio.sleep(0)
    assert calls == 1

    release.set()
    await asyncio.gather(*list(registry._tasks), return_exceptions=True)
    await registry.shutdown()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_aiogram_shutdown_hook_cancels_handlers_before_return():
    """Активный update handler обязан завершить finally до выхода shutdown hook."""
    class _FakeDispatcher:
        def __init__(self):
            self._handle_update_tasks: set[asyncio.Task] = set()

    dispatcher = _FakeDispatcher()
    started = asyncio.Event()
    finalized = asyncio.Event()

    async def handler():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finalized.set()

    task = asyncio.create_task(handler())
    dispatcher._handle_update_tasks.add(task)
    task.add_done_callback(dispatcher._handle_update_tasks.discard)

    await asyncio.wait_for(started.wait(), timeout=2)
    await _shutdown_dispatcher_handlers(dispatcher)

    assert task.done()
    assert task.cancelled()
    assert finalized.is_set()
    assert not [task for task in dispatcher._handle_update_tasks if not task.done()]


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_aiogram_shutdown_hook_does_not_cancel_current_task():
    """Защита от самоканцелла, если hook когда-либо окажется в tracked set."""
    class _FakeDispatcher:
        def __init__(self):
            self._handle_update_tasks: set[asyncio.Task] = set()

    dispatcher = _FakeDispatcher()
    current = asyncio.current_task()
    assert current is not None
    dispatcher._handle_update_tasks.add(current)

    await _shutdown_dispatcher_handlers(dispatcher)

    assert not current.cancelled()
