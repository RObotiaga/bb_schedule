# tests/conftest.py - Global test fixtures
import pytest
import os
import tempfile
import aiosqlite
from datetime import datetime, timezone, timedelta

# === Database Fixtures ===

@pytest.fixture
async def test_db():
    """
    Создает временную тестовую базу данных в памяти.
    После теста автоматически очищается.
    """
    # Используем временный файл для БД
    tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    tmp_db.close()
    db_path = tmp_db.name
    
    # Инициализируем БД
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                group_name TEXT,
                record_book_number TEXT,
                settings TEXT
            )
        """)
        
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
            CREATE TABLE IF NOT EXISTS session_cache (
                record_book_number TEXT PRIMARY KEY,
                data_json TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subject_notes (
                user_id INTEGER,
                subject_name TEXT,
                note_text TEXT,
                checklist_json TEXT,
                PRIMARY KEY (user_id, subject_name)
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_ids_json TEXT
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS teacher_subscriptions (
                user_id INTEGER,
                teacher_name TEXT,
                PRIMARY KEY (user_id, teacher_name)
            )
        """)
        
        await db.commit()
    
    yield db_path
    
    # Cleanup
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture
def sample_session_results():
    """Тестовые данные результатов сессии."""
    return [
        {
            'semester': '1 семестр',
            'subject': 'Математика',
            'grade': 'Отлично',
            'date': '2024-01-15',
            'grade_value': 5,
            'is_exam': True,
            'passed': True
        },
        {
            'semester': '1 семестр',
            'subject': 'Физика',
            'grade': 'Хорошо',
            'date': '2024-01-16',
            'grade_value': 4,
            'is_exam': True,
            'passed': True
        },
        {
            'semester': '1 семестр',
            'subject': 'Программирование',
            'grade': 'Зачтено',
            'date': '2024-01-17',
            'grade_value': None,
            'is_exam': False,
            'passed': True
        },
        {
            'semester': '2 семестр',
            'subject': 'Алгебра',
            'grade': 'Недопуск',
            'date': '',
            'grade_value': None,
            'is_exam': False,
            'passed': False
        }
    ]


@pytest.fixture
def sample_html_table():
    """HTML таблица для тестирования парсера usurt_scraper."""
    return """
    <table>
        <tr><th>Учебный год</th></tr>
        <tr><td>1</td></tr>
        <tr><td>Математика (Отлично)</td></tr>
        <tr><td>Физика (Хорошо)</td></tr>
        <tr><td>2</td></tr>
        <tr><td>Программирование (Зачтено)</td></tr>
    </table>
    """


# === Mock Fixtures ===

@pytest.fixture
def mock_playwright_page(mocker):
    """Мок для Playwright page."""
    page = mocker.AsyncMock()
    page.goto = mocker.AsyncMock()
    page.fill = mocker.AsyncMock()
    page.click = mocker.AsyncMock()
    page.wait_for_load_state = mocker.AsyncMock()
    page.content = mocker.AsyncMock(return_value="<html></html>")
    page.locator = mocker.Mock()
    return page


@pytest.fixture
def mock_bot(mocker):
    """Мок для aiogram Bot."""
    bot = mocker.AsyncMock()
    bot.send_message = mocker.AsyncMock()
    bot.copy_message = mocker.AsyncMock()
    bot.delete_message = mocker.AsyncMock()
    return bot


@pytest.fixture
def mock_message(mocker):
    """Мок для aiogram Message."""
    msg = mocker.AsyncMock()
    msg.from_user.id = 12345
    msg.text = "Test message"
    msg.answer = mocker.AsyncMock()
    msg.edit_text = mocker.AsyncMock()
    msg.delete = mocker.AsyncMock()
    return msg


@pytest.fixture
def mock_callback_query(mocker, mock_message):
    """Мок для aiogram CallbackQuery."""
    callback = mocker.AsyncMock()
    callback.from_user.id = 12345
    callback.data = "test_callback"
    callback.message = mock_message
    callback.answer = mocker.AsyncMock()
    return callback
