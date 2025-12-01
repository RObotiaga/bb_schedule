# tests/test_process_schedules.py
import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from process_schedules import (
    determine_week_type,
    parse_date_from_cell,
    parse_lesson_cell,
    MONTHS_MAP
)


# === Helper Function Tests ===

def test_determine_week_type_odd():
    """Тест определения нечетной недели."""
    assert determine_week_type("расписание_нечетная.xlsx") == "нечетная"
    assert determine_week_type("Нечет_неделя.xls") == "нечетная"


def test_determine_week_type_even():
    """Тест определения четной недели."""
    assert determine_week_type("расписание_четная.xlsx") == "четная"
    assert determine_week_type("Чет_неделя.xls") == "четная"


def test_determine_week_type_unknown():
    """Тест неизвестного типа недели."""
    assert determine_week_type("schedule.xlsx") == "неизвестно"


def test_parse_date_from_cell():
    """Тест парсинга даты из ячейки."""
    # Позитивные тесты
    assert parse_date_from_cell("15 декабря", 2024) == "2024-12-15"
    assert parse_date_from_cell("1 января", 2025) == "2025-01-01"
    assert parse_date_from_cell("30 мая", 2024) == "2024-05-30"
    
    # Негативные тесты
    assert parse_date_from_cell("какой-то текст", 2024) is None
    assert parse_date_from_cell("", 2024) is None
    assert parse_date_from_cell(123, 2024) is None  # Не строка


def test_parse_lesson_cell_full():
    """Тест парсинга полной информации о паре."""
    cell_content = """Математика
Иванов И.И.
Ауд. 101"""
    
    result = parse_lesson_cell(cell_content)
    
    assert result is not None
    assert result["subject"] == "Математика"
    assert result["teacher"] == "Иванов И.И."
    assert result["location"] == "Ауд. 101"


def test_parse_lesson_cell_with_subgroup():
    """Тест парсинга с подгруппой."""
    cell_content = """Физика 1 п/г
Петров П.П.
Ауд. 202"""
    
    result = parse_lesson_cell(cell_content)
    
    assert result is not None
    assert "1п/г" in result["subject"] or "п/г" in result["subject"]


def test_parse_lesson_cell_minimal():
    """Тест парсинга минимальной информации (только предмет)."""
    cell_content = "Программирование"
    
    result = parse_lesson_cell(cell_content)
    
    assert result is not None
    assert result["subject"] == "Программирование"
    assert result["teacher"] == "Не указан"
    assert result["location"] == "Не указана"


def test_parse_lesson_cell_empty():
    """Тест парсинга пустой ячейки."""
    assert parse_lesson_cell("") is None
    assert parse_lesson_cell("   ") is None
    assert parse_lesson_cell(None) is None


# === Integration Test (mock file processing) ===

def test_months_map():
    """Тест корректности словаря месяцев."""
    assert MONTHS_MAP["января"] == 1
    assert MONTHS_MAP["декабря"] == 12
    assert len(MONTHS_MAP) == 12


@pytest.mark.parametrize("month_name,expected_num", [
    ("января", 1),
    ("февраля", 2),
    ("марта", 3),
    ("апреля", 4),
    ("мая", 5),
    ("июня", 6),
    ("июля", 7),
    ("августа", 8),
    ("сентября", 9),
    ("октября", 10),
    ("ноября", 11),
    ("декабря", 12),
])
def test_months_map_parametrized(month_name, expected_num):
    """Параметризованный тест словаря месяцев."""
    assert MONTHS_MAP[month_name] == expected_num
