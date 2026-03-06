import pytest
from unittest.mock import AsyncMock, patch

from app.bot.main import periodic_update
from aiogram import Bot

@pytest.mark.asyncio
async def test_periodic_update_success(mocker):
    # Mock bot
    bot = mocker.AsyncMock(spec=Bot)
    
    # Mock successful sync
    mocker.patch('app.bot.main.run_full_sync', return_value=True)
    mock_reload = mocker.patch('app.bot.main.GlobalState.reload')
    
    # Mock ADMIN_ID inside the function
    mocker.patch('app.core.config.ADMIN_ID', 123456789)
    
    await periodic_update(bot)
    
    # Verify behavior
    mock_reload.assert_called_once()
    bot.send_message.assert_called_once()
    args, kwargs = bot.send_message.call_args
    assert args[0] == 123456789
    assert "✅ *Автоматическое обновление расписания*" in args[1]


@pytest.mark.asyncio
async def test_periodic_update_failure(mocker):
    # Mock bot
    bot = mocker.AsyncMock(spec=Bot)
    
    # Mock failed sync
    mocker.patch('app.bot.main.run_full_sync', return_value=False)
    mock_reload = mocker.patch('app.bot.main.GlobalState.reload')
    
    # Mock ADMIN_ID
    mocker.patch('app.core.config.ADMIN_ID', 123456789)
    
    await periodic_update(bot)
    
    # Verify behavior
    mock_reload.assert_not_called()
    bot.send_message.assert_called_once()
    args, kwargs = bot.send_message.call_args
    assert args[0] == 123456789
    assert "❌ *Ошибка авто-обновления*" in args[1]
