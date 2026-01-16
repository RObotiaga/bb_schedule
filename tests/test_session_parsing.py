
import pytest
from datetime import datetime
import sys
import os

# Add project root to sys.path to import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from process_schedules import parse_filename_context, parse_date_from_cell

def test_parse_filename_context():
    # Test valid session filename
    filename = "Промежуточная аттестация за 1 семестр 2025-2026 уч.год_1 курс.xls"
    context = parse_filename_context(filename)
    assert context == (1, 2025, 2026)

    # Test another variation
    filename = "Промежуточная аттестация за 2 семестр 2024-2025 уч.год_2 курс.xlsx"
    context = parse_filename_context(filename)
    assert context == (2, 2024, 2025)

    # Test non-session filename
    filename = "Четная неделя_1 курс.xls"
    context = parse_filename_context(filename)
    assert context is None

def test_parse_date_from_cell_1st_semester():
    # Context: 1st semester 2025-2026 (Sept 2025 - Feb 2026)
    context = {'semester': 1, 'start_year': 2025, 'end_year': 2026}

    # Sept date -> Should be start_year (2025)
    date_str = parse_date_from_cell("15 сентября", context)
    assert date_str == "2025-09-15"

    # Dec date -> Should be start_year (2025)
    date_str = parse_date_from_cell("30 декабря", context)
    assert date_str == "2025-12-30"

    # Jan date -> Should be end_year (2026)
    date_str = parse_date_from_cell("10 января", context)
    assert date_str == "2026-01-10"

    # Feb date -> Should be end_year (2026)
    date_str = parse_date_from_cell("5 февраля", context)
    assert date_str == "2026-02-05"

def test_parse_date_from_cell_2nd_semester():
    # Context: 2nd semester 2025-2026 (Feb 2026 - July 2026)
    context = {'semester': 2, 'start_year': 2025, 'end_year': 2026}

    # Feb date -> Should be end_year (2026)
    date_str = parse_date_from_cell("10 февраля", context)
    assert date_str == "2026-02-10"

    # May date -> Should be end_year (2026)
    date_str = parse_date_from_cell("20 мая", context)
    assert date_str == "2026-05-20"
