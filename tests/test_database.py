# tests/test_database.py
import pytest
import json
from datetime import datetime, timedelta, timezone
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Monkey-patch DB_PATH before importing database
original_db_path = None
if 'config' in sys.modules:
    import config
    original_db_path = config.DB_PATH

import database


@pytest.fixture(autouse=True)
async def patch_db_path(test_db, monkeypatch):
    """Автоматически подменяет DB_PATH на тестовую БД для всех тестов."""
    # Patch config module
    import config
    monkeypatch.setattr(config, "DB_PATH", test_db)
    # Patch database module's imported DB_PATH
    monkeypatch.setattr(database, "DB_PATH", test_db)
    yield


# === User CRUD Tests ===

@pytest.mark.asyncio
async def test_save_and_get_user_group():
    """Тест сохранения и получения группы пользователя."""
    user_id = 123
    group_name = "ПИ-101"
    
    await database.save_user_group_db(user_id, group_name)
    result = await database.get_user_group_db(user_id)
    
    assert result == group_name


@pytest.mark.asyncio
async def test_get_user_group_not_exists():
    """Тест получения группы несуществующего пользователя."""
    result = await database.get_user_group_db(99999)
    assert result is None


@pytest.mark.asyncio
async def test_update_user_group():
    """Тест обновления группы пользователя."""
    user_id = 456
    await database.save_user_group_db(user_id, "ПИ-201")
    await database.save_user_group_db(user_id, "ПИ-202")
    
    result = await database.get_user_group_db(user_id)
    assert result == "ПИ-202"


# === Record Book Tests ===

@pytest.mark.asyncio
async def test_save_and_get_record_book_number():
    """Тест сохранения и получения номера зачетки."""
    user_id = 789
    record_number = "20220831"
    
    await database.save_record_book_number(user_id, record_number)
    result = await database.get_record_book_number(user_id)
    
    assert result == record_number


@pytest.mark.asyncio
async def test_record_book_number_not_exists():
    """Тест получения номера зачетки несуществующего пользователя."""
    result = await database.get_record_book_number(88888)
    assert result is None


# === User Settings Tests ===

@pytest.mark.asyncio
async def test_get_default_settings():
    """Тест получения дефолтных настроек."""
    user_id = 111
    settings = await database.get_user_settings(user_id)
    assert settings == {}


@pytest.mark.asyncio
async def test_update_user_settings():
    """Тест обновления настроек пользователя."""
    user_id = 222
    # Сначала создаем пользователя
    await database.save_user_group_db(user_id, None)
    
    test_settings = {"hide_5": True, "hide_4": False}
    await database.update_user_settings(user_id, test_settings)
    
    result = await database.get_user_settings(user_id)
    assert result == test_settings


@pytest.mark.asyncio
async def test_settings_json_decode_error(monkeypatch):
    """Тест обработки невалидного JSON в настройках."""
    user_id = 333
    # Создаем запись с невалидным JSON напрямую в БД
    async with await database.get_db_connection() as db:
        await db.execute("INSERT INTO users (user_id, settings) VALUES (?, ?)", 
                        (user_id, "invalid json"))
        await db.commit()
    
    result = await database.get_user_settings(user_id)
    assert result == {}


# === Session Cache Tests ===

@pytest.mark.asyncio
async def test_save_and_get_cached_session_results(sample_session_results):
    """Тест сохранения и получения кэшированных результатов."""
    record_number = "12345"
    
    await database.save_cached_session_results(record_number, sample_session_results)
    data, last_updated = await database.get_cached_session_results(record_number)
    
    assert data == sample_session_results
    assert last_updated is not None


@pytest.mark.asyncio
async def test_cached_session_results_not_exists():
    """Тест получения несуществующего кэша."""
    data, last_updated = await database.get_cached_session_results("nonexistent")
    
    assert data is None
    assert last_updated is None


@pytest.mark.asyncio
async def test_update_cached_session_results(sample_session_results):
    """Тест обновления кэшированных результатов."""
    record_number = "99999"
    
    # Первое сохранение
    await database.save_cached_session_results(record_number, sample_session_results)
    data1, time1 = await database.get_cached_session_results(record_number)
    
    # Обновление
    new_data = sample_session_results + [{'semester': '3 семестр', 'subject': 'Новый предмет', 
                                           'grade': 'Отлично', 'date': '', 'grade_value': 5, 
                                           'is_exam': True, 'passed': True}]
    await database.save_cached_session_results(record_number, new_data)
    data2, time2 = await database.get_cached_session_results(record_number)
    
    assert len(data2) == len(new_data)
    assert len(data2) > len(data1)


# === Subject Notes Tests ===

@pytest.mark.asyncio
async def test_get_default_subject_note():
    """Тест получения дефолтной заметки."""
    user_id = 555
    subject = "Математика"
    
    note_data = await database.get_subject_note(user_id, subject)
    
    assert note_data == {"note_text": "", "checklist": []}


@pytest.mark.asyncio
async def test_save_and_get_subject_note():
    """Тест сохранения и получения заметки."""
    user_id = 666
    subject = "Физика"
    note_text = "Подготовиться к переэкзаменовке"
    checklist = [{"text": "Повторить главу 1", "done": False}]
    
    await database.save_subject_note(user_id, subject, note_text, checklist)
    result = await database.get_subject_note(user_id, subject)
    
    assert result["note_text"] == note_text
    assert result["checklist"] == checklist


@pytest.mark.asyncio
async def test_update_subject_note():
    """Тест обновления заметки."""
    user_id = 777
    subject = "Программирование"
    
    # Первая версия
    await database.save_subject_note(user_id, subject, "Старая заметка", [])
    
    # Обновление
    new_text = "Новая заметка"
    new_checklist = [{"text": "Задача 1", "done": True}]
    await database.save_subject_note(user_id, subject, new_text, new_checklist)
    
    result = await database.get_subject_note(user_id, subject)
    assert result["note_text"] == new_text
    assert result["checklist"] == new_checklist


@pytest.mark.asyncio
async def test_subject_note_invalid_json(monkeypatch):
    """Тест обработки невалидного JSON в чеклисте."""
    user_id = 888
    subject = "Тест"
    
    # Создаем запись с невалидным JSON
    async with await database.get_db_connection() as db:
        await db.execute(
            "INSERT INTO subject_notes (user_id, subject_name, note_text, checklist_json) VALUES (?, ?, ?, ?)",
            (user_id, subject, "Текст", "invalid json")
        )
        await db.commit()
    
    result = await database.get_subject_note(user_id, subject)
    assert result["note_text"] == "Текст"
    assert result["checklist"] == []


# === Schedule Query Tests ===

@pytest.mark.asyncio
async def test_get_schedule_by_group_empty():
    """Тест получения расписания для группы (пустой результат)."""
    result = await database.get_schedule_by_group("ПИ-999", "2024-12-01")
    assert len(result) == 0


@pytest.mark.asyncio
async def test_get_schedule_by_group_with_data():
    """Тест получения расписания для группы с данными."""
    # Сначала вставим тестовые данные
    async with await database.get_db_connection() as db:
        await db.execute("""
            INSERT INTO schedule (faculty, course, group_name, week_type, lesson_date, time, subject, teacher, location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("ФИТ", "1", "ПИ-101", "четная", "2024-12-01", "9:00-10:30", "Математика", "Иванов И.И.", "Ауд. 101"))
        await db.commit()
    
    result = await database.get_schedule_by_group("ПИ-101", "2024-12-01")
    assert len(result) == 1
    assert result[0]['subject'] == "Математика"


@pytest.mark.asyncio
async def test_get_schedule_by_teacher():
    """Тест получения расписания для преподавателя."""
    # Вставим тестовые данные
    async with await database.get_db_connection() as db:
        await db.execute("""
            INSERT INTO schedule (faculty, course, group_name, week_type, lesson_date, time, subject, teacher, location)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("ФИТ", "2", "ПИ-201", "нечетная", "2024-12-02", "11:00-12:30", "Физика", "Петров П.П.", "Ауд. 202"))
        await db.commit()
    
    result = await database.get_schedule_by_teacher("Петров П.П.", "2024-12-02")
    assert len(result) == 1
    assert result[0]['teacher'] == "Петров П.П."
