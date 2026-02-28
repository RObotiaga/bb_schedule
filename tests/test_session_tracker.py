import pytest
from app.services.session_tracker import compare_session_results

def test_compare_session_results_new_subject():
    old_data = [
        {"semester": "1 семестр", "subject": "Математика", "grade": "Отлично", "passed": True, "course": "1 курс"}
    ]
    new_data = [
        {"semester": "1 семестр", "subject": "Математика", "grade": "Отлично", "passed": True, "course": "1 курс"},
        {"semester": "1 семестр", "subject": "Физика", "grade": "Хорошо", "passed": True, "course": "1 курс"}
    ]
    
    notifications = compare_session_results(old_data, new_data)
    assert len(notifications) == 1
    assert "Новый результат" in notifications[0]
    assert "Физика" in notifications[0]
    assert "Хорошо" in notifications[0]

def test_compare_session_results_changed_grade():
    old_data = [
        {"semester": "2 семестр", "subject": "Электротехника", "grade": "Недопуск", "passed": False, "course": "2 курс"}
    ]
    new_data = [
        {"semester": "2 семестр", "subject": "Электротехника", "grade": "Удовлетворительно", "passed": True, "course": "2 курс"}
    ]
    
    notifications = compare_session_results(old_data, new_data)
    assert len(notifications) == 1
    assert "Изменение оценки" in notifications[0]
    assert "Электротехника" in notifications[0]
    assert "Недопуск" in notifications[0]
    assert "Удовлетворительно" in notifications[0]

def test_compare_session_results_no_changes():
    old_data = [
        {"semester": "1 семестр", "subject": "Математика", "grade": "Отлично", "passed": True, "course": "1 курс"}
    ]
    new_data = [
        {"semester": "1 семестр", "subject": "Математика", "grade": "Отлично", "passed": True, "course": "1 курс"}
    ]
    
    notifications = compare_session_results(old_data, new_data)
    assert len(notifications) == 0

def test_compare_session_results_empty_old():
    # If old data is empty, it shouldn't spam the user with "new" notifications for everything
    new_data = [
        {"semester": "1 семестр", "subject": "Математика", "grade": "Отлично", "passed": True, "course": "1 курс"}
    ]
    notifications = compare_session_results([], new_data)
    assert len(notifications) == 0
