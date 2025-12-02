# tests/test_bot_handlers.py
import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot import filter_results_by_settings, format_results


# === Helper Functions Tests ===

def test_filter_results_by_settings_hide_5(sample_session_results):
    """Тест фильтрации оценок Отлично (5)."""
    settings = {"hide_5": True}
    
    filtered = filter_results_by_settings(sample_session_results, settings)
    
    # Математика (5) должна быть скрыта
    assert len(filtered) < len(sample_session_results)
    assert all(item.get('grade_value') != 5 for item in filtered)


def test_filter_results_by_settings_hide_4(sample_session_results):
    """Тест фильтрации оценок Хорошо (4)."""
    settings = {"hide_4": True}
    
    filtered = filter_results_by_settings(sample_session_results, settings)
    
    assert all(item.get('grade_value') != 4 for item in filtered)


def test_filter_results_by_settings_hide_passed_non_exam(sample_session_results):
    """Тест скрытия зачетов без оценки."""
    settings = {"hide_passed_non_exam": True}
    
    filtered = filter_results_by_settings(sample_session_results, settings)
    
    # "Программирование (Зачтено)" должно быть скрыто
    assert all(not (item.get('passed') and item.get('grade_value') is None) for item in filtered)


def test_filter_results_by_settings_hide_failed(sample_session_results):
    """Тест скрытия незачетов/недопусков."""
    settings = {"hide_failed": True}
    
    filtered = filter_results_by_settings(sample_session_results, settings)
    
    # "Алгебра (Недопуск)" должна быть скрыта
    assert all(item.get('passed') for item in filtered)


def test_filter_results_by_settings_multiple(sample_session_results):
    """Тест комбинации фильтров."""
    settings = {
        "hide_5": True,
        "hide_failed": True
    }
    
    filtered = filter_results_by_settings(sample_session_results, settings)
    
    # Должны остаться только Хорошо (4) и Зачтено
    assert all(item.get('grade_value') != 5 for item in filtered)
    assert all(item.get('passed') for item in filtered)


def test_format_results_empty():
    """Тест форматирования пустого списка результатов."""
    result = format_results([], {})
    assert "не найдены" in result.lower()


def test_format_results_all_hidden(sample_session_results):
    """Тест когда все предметы скрыты фильтрами."""
    # Скрываем всё
    settings = {
        "hide_5": True,
        "hide_4": True,
        "hide_3": True,
        "hide_passed_non_exam": True,
        "hide_failed": True
    }
    
    result = format_results(sample_session_results, settings)
    assert "скрыты" in result.lower()


def test_format_results_with_data(sample_session_results):
    """Тест форматирования результатов с данными."""
    settings = {}
    
    result = format_results(sample_session_results, settings)
    
    # Проверяем наличие ключевых элементов
    assert "Математика" in result
    assert "Физика" in result
    assert "1 семестр" in result
    assert "2 семестр" in result
    assert "✅" in result or "❌" in result  # Иконки


# === Keyboard Tests ===

def test_get_session_results_keyboard():
    """Тест клавиатуры результатов сессии."""
    from bot import get_session_results_keyboard
    
    keyboard = get_session_results_keyboard()
    
    # Проверяем наличие кнопок
    inline_keyboard = keyboard.inline_keyboard
    assert len(inline_keyboard) > 0
    
    # Проверяем callback_data кнопок
    all_callbacks = []
    for row in inline_keyboard:
        for button in row:
            all_callbacks.append(button.callback_data)
    
    assert "notes_root" in all_callbacks
    assert "refresh_results" in all_callbacks
    assert "session_settings" in all_callbacks


def test_get_settings_keyboard():
    """Тест клавиатуры настроек."""
    from bot import get_settings_keyboard
    
    settings = {"hide_5": True, "hide_4": False}
    keyboard = get_settings_keyboard(settings)
    
    inline_keyboard = keyboard.inline_keyboard
    assert len(inline_keyboard) > 0


# === Handler Tests (with mocks) ===

@pytest.mark.asyncio
async def test_notes_root_handler_no_record_book(mock_callback_query, mocker):
    """Тест notes_root без номера зачетки."""
    from bot import notes_root
    
    # Mock DB call
    mocker.patch('bot.get_record_book_number', return_value=None)
    
    await notes_root(mock_callback_query)
    
    # Должен вызваться callback.answer с сообщением
    mock_callback_query.answer.assert_called_once()
    assert "сначала получите" in mock_callback_query.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_notes_root_handler_with_data(mock_callback_query, mocker, sample_session_results):
    """Тест notes_root с данными."""
    from bot import notes_root
    
    # Mock DB calls
    mocker.patch('bot.get_record_book_number', return_value="12345")
    mocker.patch('bot.UsurtScraper.get_session_results', return_value=("SUCCESS", sample_session_results))
    mocker.patch('bot.get_user_settings', return_value={})
    
    await notes_root(mock_callback_query)
    
    # Должно отредактироваться сообщение с семестрами
    mock_callback_query.message.edit_text.assert_called_once()
    call_text = mock_callback_query.message.edit_text.call_args[0][0]
    assert "семестр" in call_text.lower()


@pytest.mark.asyncio
async def test_refresh_results_handler(mock_callback_query, mocker, sample_session_results):
    """Тест обработчика refresh_results."""
    from bot import refresh_session_results
    
    # Mock DB
    mocker.patch('bot.get_record_book_number', return_value="99999")
    # Mock scraper (force refresh)
    mocker.patch('bot.UsurtScraper.get_session_results', return_value=("SUCCESS", sample_session_results))
    # Mock show_results_view
    mock_show = mocker.patch('bot.show_results_view')
    
    await refresh_session_results(mock_callback_query)
    
    # Должен вызваться scraper с use_cache=False
    # И затем показаться обновленные результаты
    mock_show.assert_called_once()


# === FSM Tests ===

@pytest.mark.asyncio
async def test_note_edit_text_save(mock_message, mocker):
    """Тест сохранения текста заметки через FSM."""
    from bot import note_edit_text_save
    from aiogram.fsm.context import FSMContext
    
    # Mock FSM context
    mock_state = mocker.AsyncMock(spec=FSMContext)
    mock_state.get_data.return_value = {
        "current_subject": "Математика",
        "current_semester": "1 семестр"
    }
    
    # Mock DB
    mocker.patch('database.get_subject_note', return_value={"note_text": "", "checklist": []})
    mock_save = mocker.patch('database.save_subject_note')
    mocker.patch('bot.show_subject_note_view')
    
    mock_message.text = "Новая заметка"
    
    await note_edit_text_save(mock_message, mock_state)
    
    # Проверяем, что заметка сохранена
    mock_save.assert_called_once()
    assert mock_save.call_args[0][2] == "Новая заметка"  # note_text


@pytest.mark.asyncio
async def test_checklist_add_item(mock_message, mocker):
    """Тест добавления пункта чеклиста."""
    from bot import note_add_item_save
    from aiogram.fsm.context import FSMContext
    
    mock_state = mocker.AsyncMock(spec=FSMContext)
    mock_state.get_data.return_value = {
        "current_subject": "Физика",
        "current_semester": "2 семестр"
    }
    
    # Существующая заметка с пустым чеклистом
    mocker.patch('database.get_subject_note', return_value={
        "note_text": "Текст", 
        "checklist": []
    })
    mock_save = mocker.patch('database.save_subject_note')
    mocker.patch('bot.show_subject_note_view')
    
    mock_message.text = "Новый пункт"
    
    await note_add_item_save(mock_message, mock_state)
    
    # Проверяем, что пункт добавлен
    mock_save.assert_called_once()
    saved_checklist = mock_save.call_args[0][3]
    assert len(saved_checklist) == 1
    assert saved_checklist[0]["text"] == "Новый пункт"
    assert saved_checklist[0]["done"] is False
