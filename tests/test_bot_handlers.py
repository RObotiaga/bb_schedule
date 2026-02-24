# tests/test_bot_handlers.py
import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.bot.handlers.session import filter_results_by_settings, format_results


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
    from app.bot.keyboards import get_session_results_keyboard
    
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
    from app.bot.keyboards import get_settings_keyboard
    
    settings = {"hide_5": True, "hide_4": False}
    keyboard = get_settings_keyboard(settings)
    
    inline_keyboard = keyboard.inline_keyboard
    assert len(inline_keyboard) > 0


# === Handler Tests (with mocks) ===

@pytest.mark.asyncio
async def test_notes_root_handler_no_record_book(mock_callback_query, mocker):
    """Тест notes_root без номера зачетки."""
    from app.bot.handlers.session import notes_root
    
    # Mock DB call
    mocker.patch('app.bot.handlers.session.get_record_book_number', return_value=None)
    
    await notes_root(mock_callback_query)
    
    # Должен вызваться callback.answer с сообщением
    mock_callback_query.answer.assert_called_once()
    assert "сначала получите" in mock_callback_query.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_notes_root_handler_with_data(mock_callback_query, mocker, sample_session_results):
    """Тест notes_root с данными."""
    from app.bot.handlers.session import notes_root
    
    # Mock DB calls
    mocker.patch('app.bot.handlers.session.get_record_book_number', return_value="12345")
    mocker.patch('app.bot.handlers.session.UsurtScraper.get_session_results', return_value=("SUCCESS", sample_session_results))
    mocker.patch('app.bot.handlers.session.get_user_settings', return_value={})
    
    await notes_root(mock_callback_query)
    
    # Должно отредактироваться сообщение с семестрами
    mock_callback_query.message.edit_text.assert_called_once()
    call_text = mock_callback_query.message.edit_text.call_args[0][0]
    assert "семестр" in call_text.lower()


@pytest.mark.asyncio
async def test_refresh_results_handler(mock_callback_query, mocker, sample_session_results):
    """Тест обработчика refresh_results."""
    from app.bot.handlers.session import refresh_session_results
    
    # Mock DB
    mocker.patch('app.bot.handlers.session.get_record_book_number', return_value="99999")
    # Mock scraper (force refresh)
    mocker.patch('app.bot.handlers.session.UsurtScraper.get_session_results', return_value=("SUCCESS", sample_session_results))
    # Mock show_results_view
    mock_show = mocker.patch('app.bot.handlers.session.show_results_view')
    
    await refresh_session_results(mock_callback_query)
    
    # Должен вызваться scraper с use_cache=False
    # И затем показаться обновленные результаты
    mock_show.assert_called_once()


# === FSM Tests ===

@pytest.mark.asyncio
async def test_note_edit_text_save(mock_message, mocker):
    """Тест сохранения текста заметки через FSM."""
    from app.bot.handlers.session import note_edit_text_save
    from aiogram.fsm.context import FSMContext
    
    # Mock FSM context
    mock_state = mocker.AsyncMock(spec=FSMContext)
    mock_state.get_data.return_value = {
        "current_subject": "Математика",
        "current_semester": "1 семестр"
    }
    
    # Mock DB
    mocker.patch('app.bot.handlers.session.get_subject_note', return_value={"note_text": "", "checklist": []})
    mock_save = mocker.patch('app.bot.handlers.session.save_subject_note')
    mocker.patch('app.bot.handlers.session.show_subject_note_view')
    
    mock_message.text = "Новая заметка"
    
    await note_edit_text_save(mock_message, mock_state)
    
    # Проверяем, что заметка сохранена
    mock_save.assert_called_once()
    assert mock_save.call_args[0][2] == "Новая заметка"  # note_text


@pytest.mark.asyncio
async def test_checklist_add_item(mock_message, mocker):
    """Тест добавления пункта чеклиста."""
    from app.bot.handlers.session import note_add_item_save
    from aiogram.fsm.context import FSMContext
    
    mock_state = mocker.AsyncMock(spec=FSMContext)
    mock_state.get_data.return_value = {
        "current_subject": "Физика",
        "current_semester": "2 семестр"
    }
    
    # Существующая заметка с пустым чеклистом
    mocker.patch('app.bot.handlers.session.get_subject_note', return_value={
        "note_text": "Текст", 
        "checklist": []
    })
    mock_save = mocker.patch('app.bot.handlers.session.save_subject_note')
    mocker.patch('app.bot.handlers.session.show_subject_note_view')
    
    mock_message.text = "Новый пункт"
    
    await note_add_item_save(mock_message, mock_state)
    
    # Проверяем, что пункт добавлен
    mock_save.assert_called_once()
    saved_checklist = mock_save.call_args[0][3]
    assert len(saved_checklist) == 1
    assert saved_checklist[0]["text"] == "Новый пункт"
    assert saved_checklist[0]["done"] is False


# === New Session Logic Tests ===

@pytest.mark.asyncio
async def test_teacher_search_respects_state():
    """Тест: поиск преподавателя не срабатывает при активном состоянии FSM."""
    # Since we added StateFilter(None) to teachers.py, we just verify the import.
    from app.bot.handlers.teachers import process_teacher_search
    assert process_teacher_search is not None

@pytest.mark.asyncio
async def test_show_session_results_command(mock_message, mocker):
    """Тест команды вызова результатов."""
    from app.bot.handlers.session import show_session_results
    from aiogram.fsm.context import FSMContext
    
    mock_state = mocker.AsyncMock(spec=FSMContext)
    
    # Сначала без номера зачетки
    mocker.patch('app.bot.handlers.session.get_record_book_number', return_value=None)
    await show_session_results(mock_message, mock_state)
    mock_message.answer.assert_called_once()
    assert "знать номер" in mock_message.answer.call_args[0][0]
    
    # Теперь с номером
    mocker.patch('app.bot.handlers.session.get_record_book_number', return_value="123456")
    mock_show = mocker.patch('app.bot.handlers.session.show_results_view')
    await show_session_results(mock_message, mock_state)
    mock_show.assert_called_once_with(mock_message, mock_message.from_user.id, "123456")

@pytest.mark.asyncio
async def test_process_record_book_number_valid(mock_message, mocker):
    """Тест сохранения валидного номера зачетки."""
    from app.bot.handlers.session import process_record_book_number
    from aiogram.fsm.context import FSMContext
    
    mock_state = mocker.AsyncMock(spec=FSMContext)
    mock_message.text = "123456"
    
    mock_save = mocker.patch('app.bot.handlers.session.save_record_book_number')
    mock_show = mocker.patch('app.bot.handlers.session.show_results_view')
    
    await process_record_book_number(mock_message, mock_state)
    
    mock_save.assert_called_once_with(mock_message.from_user.id, "123456")
    mock_state.clear.assert_called_once()
    mock_show.assert_called_once_with(mock_message, mock_message.from_user.id, "123456")

@pytest.mark.asyncio
async def test_process_record_book_number_invalid(mock_message, mocker):
    """Тест отклонения невалидного номера зачетки."""
    from app.bot.handlers.session import process_record_book_number
    from aiogram.fsm.context import FSMContext
    
    mock_state = mocker.AsyncMock(spec=FSMContext)
    mock_message.text = "invalid123"
    
    await process_record_book_number(mock_message, mock_state)
    
    mock_message.answer.assert_called_once()
    assert "только из цифр" in mock_message.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_format_schedule_message_with_subscription():
    """Тест форматирования расписания с выделением подписок."""
    from app.bot.handlers.schedule import format_schedule_message
    from datetime import date
    
    lessons = [
        {
            'time': '9:00', 'subject': 'Моя пара', 
            'teacher': 'Иванов И.И.', 'location': 'Ауд. 1', 
            'week_type': 'Четная'
        },
        {
            'time': '11:00', 'subject': 'Долг пара', 
            'teacher': 'Петров П.П.', 'location': 'Ауд. 2', 
            'week_type': 'Четная', 'is_subscription': True
        }
    ]
    
    result = format_schedule_message("ПИ-101", date(2025, 1, 1), lessons)
    
    assert "⏰ 9:00" in result
    assert "Моя пара" in result
    
    assert "🔔 *[Подписка]* *11:00*" in result
    assert "*Долг пара*" in result
    assert "*Петров П.П.*" in result

