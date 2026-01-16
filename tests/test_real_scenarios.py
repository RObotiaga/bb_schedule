
import pytest
import os
import sys
import re
import pandas as pd
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from process_schedules import process_single_file, parse_filename_context

# Constants for paths
SCHEDULES_DIR = os.path.join(os.getcwd(), 'data', 'schedules')

def test_etf_4_course_sot412_parsing():
    """
    Test parsing of 'ЭТФ 4 курс' schedule to ensure group SOt-412 is found.
    Uses the real downloaded file if it exists.
    """
    # Expected path based on fetch logs
    # data\schedules\Электротехнический факультет\4 курс\Четная неделя_ЭТФ 4 курс четная.xls (or similar)
    
    etf_dir = os.path.join(SCHEDULES_DIR, "Электротехнический факультет", "4 курс")
    
    if not os.path.exists(etf_dir):
        pytest.skip(f"Directory {etf_dir} not found. Skipping real file test.")
        
    # Find the target file
    found_file = None
    for f in os.listdir(etf_dir):
        if "СОт-412" in f: # If filename has group - rare but possible
             found_file = os.path.join(etf_dir, f)
             break
        if "ЭТФ 4 курс" in f and f.endswith(".xls") and "с 12 января" not in f: # Standard file
             found_file = os.path.join(etf_dir, f)
             # Don't break yet, keep looking for better match or use this
    
    if not found_file:
         # Try with "с 12 января" if base not found
         for f in os.listdir(etf_dir):
            if "ЭТФ 4 курс" in f and f.endswith(".xls"):
                 found_file = os.path.join(etf_dir, f)
                 break
                 
    if not found_file:
        pytest.skip("Target schedule file for ETF 4 course not found via heuristic.")
        
    print(f"Testing file: {found_file}")
    
    lessons = process_single_file(found_file, "Электротехнический факультет", "4")
    
    # Check if we have lessons for "СОт-412"
    # Group names in DB might be "СОт-412" or "СОт-412 (1п/г)" etc.
    
    sot_412_lessons = [l for l in lessons if "СОт-412" in l[0]]
    
    assert len(sot_412_lessons) > 0, f"No lessons found for group 'СОт-412' in {found_file}. All groups found: {set(l[0] for l in lessons)}"
    
    # Optional: Check for proper Teacher/Subject parsing
    # Just ensure fields are not empty
    for l in sot_412_lessons:
        # l structure: (group, date, time, subject, teacher, location, week_type, faculty, course)
        assert l[3], f"Subject should not be empty in lesson: {l}" # Subject
        
        # Teacher might be "Не указан" if not found, but we expect it to be parsed if present
        # Let's check format of date
        assert re.match(r'\d{4}-\d{2}-\d{2}', l[1]), f"Date {l[1]} invalid format"
        
        # Check time format (basic check)
        assert len(l[2]) >= 3, f"Time {l[2]} seems too short"

def test_session_parsing_1st_sem_2025_2026(tmp_path):
    """
    Test parsing of a mocked Session file: 
    'Промежуточная аттестация за 1 семестр 2025-2026 уч.год Сессия 4 курс.xls'
    """
    filename = "Промежуточная аттестация за 1 семестр 2025-2026 уч.год Сессия 4 курс.xls"
    file_path = tmp_path / filename
    
    # Create a dummy Excel file
    # Structure: Header at row ~5-10 usually. Let's make it simple.
    # col 0: День, col 1: Часы, col 2: TestGroup
    
    data = {
        'День': ['10 января', '15 января', '20 января'],
        'Часы': ['10:00-11:30', '12:00-13:30', '14:00-15:30'],
        'TestGroup': [
            'Exam Subject 1\nTeacher A.A.', 
            'Consultation Subject 2\nTeacher B.B.',
            'Exam Subject 3\nTeacher C.C.'
        ]
    }
    df = pd.DataFrame(data)
    
    # Write to excel with some empty rows on top to simulate real file
    with pd.ExcelWriter(file_path, engine='openpyxl') as writer:
        df.to_excel(writer, startrow=5, index=False)
        
    # Process
    lessons = process_single_file(str(file_path), "TestFaculty", "4")
    
    assert len(lessons) == 3, f"Expected 3 lessons, found {len(lessons)}"
    
    # Verify Date Parsing (Should be 2026 because 1st sem 2025-2026 ends in Feb 2026)
    # 10 jan corresponding to 1st sem 2025-2026 -> 2026
    
    # date is index 1 in the tuple structure from process_single_file
    # lessons structure: (group, date, time, subject, teacher, location, week_type, faculty, course)
    
    # Lesson 1
    l1 = lessons[0]
    assert l1[1] == "2026-01-10"
    assert l1[2] == "10:00-11:30"
    assert "Exam Subject 1" in l1[3]
    assert "Teacher A.A." in l1[4]
    
    # Lesson 2
    l2 = lessons[1]
    assert l2[1] == "2026-01-15"
    assert l2[2] == "12:00-13:30"
    assert "Consultation Subject 2" in l2[3]
    assert "Teacher B.B." in l2[4]
    
    # Lesson 3
    l3 = lessons[2]
    assert l3[1] == "2026-01-20"
    assert l3[2] == "14:00-15:30"
    assert "Exam Subject 3" in l3[3]
    assert "Teacher C.C." in l3[4]
    
    # Verify Week Type
    # index 6 is week_type
    assert lessons[0][6] == 'сессия'
