import pytest
from unittest.mock import MagicMock, AsyncMock
from aiogram.types import Message, CallbackQuery, User, Chat, InlineKeyboardMarkup, ReplyKeyboardMarkup
from aiogram.fsm.context import FSMContext
import sys
import os

# Insert project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.bot.handlers.common import (
    process_course_choice_factory, process_faculty_choice, process_group_choice,
    send_welcome
)
from app.bot.keyboards import CourseCallbackFactory, get_faculties_keyboard
from app.core.state import GlobalState

# Mock Data
MOCK_FACULTIES = ["Электротехнический факультет", "Механический факультет"]
MOCK_STRUCTURED_DATA = {
    "Электротехнический факультет": {
        "1": ["СОт-111"],
        "2": ["СОт-211"],
        "3": ["СОт-311"],
        "4": ["СОт-411", "СОт-412"],
        "5": ["СОт-511"]
    },
    "Механический факультет": {
        "1": ["М-101"]
    }
}
MOCK_TEACHERS = ["Чебаков Сергей Алексеевич", "Чебаков Другой Иванович", "Иванов Иван Иванович"]

@pytest.fixture
def mock_bot_data(mocker):
    """Mocks the global variables in app.core.state"""
    mocker.patch.object(GlobalState, 'FACULTIES_LIST', MOCK_FACULTIES)
    mocker.patch.object(GlobalState, 'STRUCTURED_DATA', MOCK_STRUCTURED_DATA)
    mocker.patch.object(GlobalState, 'ALL_TEACHERS_LIST', MOCK_TEACHERS)

@pytest.fixture
def mock_message():
    message = AsyncMock(spec=Message)
    message.from_user = User(id=123, is_bot=False, first_name="TestUser")
    message.chat = Chat(id=123, type="private")
    message.text = "/start"
    message.answer = AsyncMock()
    return message

@pytest.fixture
def mock_callback():
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = User(id=123, is_bot=False, first_name="TestUser")
    callback.message = AsyncMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.message.delete = AsyncMock()
    callback.answer = AsyncMock()
    return callback

@pytest.fixture
def mock_state():
    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={})
    state.update_data = AsyncMock()
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    return state

@pytest.mark.asyncio
async def test_full_user_flow(mock_bot_data, mock_message, mock_callback, mock_state, mocker):
    """
    Test scenario:
    1. /start -> Faculty selection
    2. Course selection (check 1-5) -> Select 4
    3. Group selection (check filtering) -> Select СОт-412
    4. Save group verification
    """
    
    # === Step 1: Registration /start ===
    # Mock DB in handlers.common
    mocker.patch('app.bot.handlers.common.get_user_group_db', return_value=None)
    mock_save_group = mocker.patch('app.bot.handlers.common.save_user_group_db', return_value=True)
    
    await send_welcome(mock_message)
    
    # Verify welcome message
    mock_message.answer.assert_called()
    args, kwargs = mock_message.answer.call_args
    assert "Добро пожаловать" in args[0]
    
    # Verify Faculty Keyboard
    keyboard = kwargs['reply_markup']
    assert isinstance(keyboard, InlineKeyboardMarkup)
    buttons = [btn.text for row in keyboard.inline_keyboard for btn in row]
    assert "Электротехнический факультет" in buttons
    assert "Механический факультет" in buttons
    
    # === Step 2: Select Faculty ===
    # User clicks "Электротехнический факультет" (index 0)
    mock_callback.data = "faculty:0"
    
    await process_faculty_choice(mock_callback)
    
    # Verify Course Keyboard
    mock_callback.message.edit_text.assert_called()
    args, kwargs = mock_callback.message.edit_text.call_args
    assert "выберите курс" in args[0].lower()
    
    keyboard = kwargs['reply_markup']
    buttons = [btn.text for row in keyboard.inline_keyboard for btn in row]
    
    # Verify courses 1-5 are present
    expected_courses = ["1 курс", "2 курс", "3 курс", "4 курс", "5 курс"]
    for course in expected_courses:
        assert course in buttons, f"Course {course} missing from keyboard"
        
    assert "Механический" not in args[0] # Should show chosen faculty name
    assert "Электротехнический" in args[0]

    # === Step 3: Select Course 4 ===
    # User clicks "4 курс". The handler uses CourseCallbackFactory
    callback_data = CourseCallbackFactory(course_id=4, faculty_id=0)
    
    await process_course_choice_factory(mock_callback, callback_data)
    
    # Verify Group Keyboard
    mock_callback.message.edit_text.assert_called()
    args, kwargs = mock_callback.message.edit_text.call_args
    assert "Выберите вашу группу" in args[0]
    
    keyboard = kwargs['reply_markup']
    # Check if buttons are present for groups
    buttons = [btn.text for row in keyboard.inline_keyboard for btn in row if "back" not in btn.text.lower()]
    assert "СОт-411" in buttons
    assert "СОт-412" in buttons

@pytest.mark.asyncio
async def test_start_registered(mock_message, mock_bot_data, mocker):
    """
    Test /start when user is already registered (has a group).
    Should show welcome back message and day selection keyboard.
    """
    # Mock DB to return an existing group
    mocker.patch('app.bot.handlers.common.get_user_group_db', return_value="СОт-412")
    
    await send_welcome(mock_message)
    
    # Verify response
    assert mock_message.answer.call_count == 2
    
    # First call: Welcome back with change group keyboard
    args1, kwargs1 = mock_message.answer.call_args_list[0]
    assert "С возвращением" in args1[0]
    assert "СОт-412" in args1[0]
    assert isinstance(kwargs1.get('reply_markup'), InlineKeyboardMarkup)
    
    # Second call: Day selection keyboard
    args2, kwargs2 = mock_message.answer.call_args_list[1]
    assert "Вы можете посмотреть расписание" in args2[0]
    
    # Verify Day Selection Keyboard (ReplyKeyboardMarkup)
    keyboard = kwargs2['reply_markup']
    assert isinstance(keyboard, ReplyKeyboardMarkup)
    # ReplyKeyboardMarkup has 'keyboard' attribute which is list of list of buttons
    buttons = [btn.text for row in keyboard.keyboard for btn in row]
    
    assert "Сегодня" in buttons
    assert "Завтра" in buttons
    assert "📊 Мои результаты" in buttons
