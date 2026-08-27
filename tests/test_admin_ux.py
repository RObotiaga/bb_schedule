import inspect

import pytest

from app.bot.handlers import admin_ux
from app.bot.main import create_dispatcher


class FakeState:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.cleared = False

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.cleared = True
        self.data.clear()


class FakeMessage:
    def __init__(self):
        self.edits = []
        self.answers = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


class FakeCallback:
    def __init__(self):
        self.bot = object()
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text=None, **kwargs):
        self.answers.append((text, kwargs))


def test_admin_ux_router_is_before_legacy_admin_router():
    source = inspect.getsource(create_dispatcher)
    ux_pos = source.index("dp.include_router(admin_ux.router)")
    legacy_pos = source.index("dp.include_router(admin.router)")
    assert ux_pos < legacy_pos


@pytest.mark.asyncio
async def test_db_import_confirmation_uses_safe_restore(monkeypatch):
    calls = []
    state = FakeState({"db_file_id": "telegram-file-id"})
    callback = FakeCallback()

    async def fake_download(bot, file_id):
        assert bot is callback.bot
        assert file_id == "telegram-file-id"
        calls.append("download")
        return b"validated-db-bytes"

    async def fake_restore(raw):
        assert raw == b"validated-db-bytes"
        calls.append("restore")

    async def fake_initialize():
        calls.append("initialize")

    async def fake_reload(cls):
        calls.append("reload")

    monkeypatch.setattr(admin_ux, "_download_document_bytes", fake_download)
    monkeypatch.setattr(admin_ux, "restore_database_bytes", fake_restore)
    monkeypatch.setattr(admin_ux, "initialize_database", fake_initialize)
    monkeypatch.setattr(admin_ux.GlobalState, "reload", classmethod(fake_reload))

    await admin_ux.db_import_confirm(callback, state)

    assert calls == ["download", "restore", "initialize", "reload"]
    assert state.cleared is True
    assert any("успешно восстановлена" in text for text, _ in callback.message.edits)


@pytest.mark.asyncio
async def test_document_fallback_does_not_import_file():
    message = FakeMessage()
    await admin_ux.admin_document_fallback(message)
    assert len(message.answers) == 1
    assert "Сначала выберите" in message.answers[0][0]
