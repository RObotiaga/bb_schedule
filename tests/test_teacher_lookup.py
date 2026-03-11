"""
Тесты для функций сопоставления зачётки → группа → преподаватель.
"""
import pytest
import os
import tempfile
import aiosqlite
import json

from app.bot.handlers.session import format_results


# ─── Fixture: тестовая БД с расписанием, rating_data, cluster_groups ───

@pytest.fixture
async def teacher_db():
    """Создает тестовую БД с нужными таблицами и тестовыми данными."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    tmp.close()
    db_path = tmp.name

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row

        await db.execute("""
            CREATE TABLE schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty TEXT, course TEXT, group_name TEXT, week_type TEXT,
                lesson_date TEXT, time TEXT, subject TEXT, teacher TEXT, location TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE rating_data (
                record_book TEXT PRIMARY KEY,
                enrollment_year INTEGER,
                subjects_json TEXT,
                total_subjects INTEGER DEFAULT 0,
                passed_subjects INTEGER DEFAULT 0,
                pass_rate REAL DEFAULT 0.0,
                cluster_id INTEGER,
                is_expelled INTEGER DEFAULT 0,
                last_academic_year TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE cluster_groups (
                cluster_id INTEGER PRIMARY KEY,
                group_name TEXT NOT NULL,
                similarity REAL DEFAULT 0.0
            )
        """)

        await db.execute("""
            CREATE TABLE teacher_stats (
                teacher TEXT,
                subject TEXT,
                group_name TEXT,
                total_students INTEGER,
                passed_students INTEGER,
                pass_rate REAL,
                academic_year TEXT,
                UNIQUE(teacher, subject)
            )
        """)

        # --- Тестовые данные ---

        # Расписание: Физика — два преподавателя (лекции + практика)
        await db.execute(
            "INSERT INTO schedule (group_name, subject, teacher, lesson_date, time, faculty, course, week_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("СОт-115", "Физика", "Першин Виталий Константинович, Профессор", "2026-03-13", "14:30-16:05", "ФТ", "1", "нечетная")
        )
        await db.execute(
            "INSERT INTO schedule (group_name, subject, teacher, lesson_date, time, faculty, course, week_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("СОт-115", "Физика (1п/г)", "Авксентьева Екатерина Ивановна, Старший преподаватель", "2026-03-18", "08:30-10:05", "ФТ", "1", "нечетная")
        )
        await db.execute(
            "INSERT INTO schedule (group_name, subject, teacher, lesson_date, time, faculty, course, week_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("СОт-115", "Физика (2п/г)", "Авксентьева Екатерина Ивановна, Старший преподаватель", "2026-03-19", "12:00-13:35", "ФТ", "1", "нечетная")
        )

        # Расписание: Математика — один преподаватель
        await db.execute(
            "INSERT INTO schedule (group_name, subject, teacher, lesson_date, time, faculty, course, week_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("СОт-115", "Математика", "Садов Алексей Павлович, Доцент", "2026-03-14", "10:15-11:50", "ФТ", "1", "нечетная")
        )

        # Расписание: предмет с "Не указан" — не должен возвращаться
        await db.execute(
            "INSERT INTO schedule (group_name, subject, teacher, lesson_date, time, faculty, course, week_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("СОт-115", "Философия", "Не указан", "2026-03-15", "10:15-11:50", "ФТ", "1", "нечетная")
        )

        # Студент → кластер → группа
        subjects = [
            {"subject": "Физика", "grade": "Отлично", "passed": True},
            {"subject": "Математика", "grade": "Хорошо", "passed": True},
            {"subject": "Философия", "grade": "Недопуск", "passed": False},
            {"subject": "Химия", "grade": "Зачтено", "passed": True},
        ]
        await db.execute(
            "INSERT INTO rating_data (record_book, enrollment_year, subjects_json, cluster_id, is_expelled) VALUES (?, ?, ?, ?, ?)",
            ("2025001", 2025, json.dumps(subjects), 2025001, 0)
        )
        await db.execute(
            "INSERT INTO cluster_groups (cluster_id, group_name, similarity) VALUES (?, ?, ?)",
            (2025001, "СОт-115", 0.95)
        )

        # Отчисленный студент — не должен маппиться
        await db.execute(
            "INSERT INTO rating_data (record_book, enrollment_year, subjects_json, cluster_id, is_expelled) VALUES (?, ?, ?, ?, ?)",
            ("2025999", 2025, "[]", 2025001, 1)
        )

        # teacher_stats: предмет из прошлого семестра (не в текущем расписании)
        await db.execute(
            "INSERT OR IGNORE INTO teacher_stats (teacher, subject, group_name, total_students, passed_students, pass_rate, academic_year) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("Старов Иван Петрович, Доцент", "История транспорта", "СОт-115", 20, 18, 90.0, "2024/2025")
        )

        await db.commit()

    yield db_path

    try:
        os.unlink(db_path)
    except:
        pass


# ─── Тесты ───

@pytest.mark.asyncio
async def test_get_group_by_record_book(teacher_db, monkeypatch):
    """record_book → cluster_id → group_name корректно работает."""
    import app.core.database as db_mod

    conn = await aiosqlite.connect(teacher_db)
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db_mod, "_global_db_conn", conn)

    from app.core.repositories.rating import get_group_by_record_book

    group = await get_group_by_record_book("2025001")
    assert group == "СОт-115"

    # Несуществующая зачетка
    group2 = await get_group_by_record_book("9999999")
    assert group2 is None

    # Отчисленный студент
    group3 = await get_group_by_record_book("2025999")
    assert group3 is None

    await conn.close()
    monkeypatch.setattr(db_mod, "_global_db_conn", None)


@pytest.mark.asyncio
async def test_get_teachers_for_subject(teacher_db, monkeypatch):
    """Находит всех преподавателей для предмета, включая лабораторные подгруппы."""
    import app.core.database as db_mod

    conn = await aiosqlite.connect(teacher_db)
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db_mod, "_global_db_conn", conn)

    from app.core.repositories.schedule import get_teachers_for_subject

    # Физика — два преподавателя (лекции + лабы)
    teachers = await get_teachers_for_subject("СОт-115", "Физика")
    assert len(teachers) == 2
    teacher_names = {t.split(",")[0].strip() for t in teachers}
    assert "Першин Виталий Константинович" in teacher_names
    assert "Авксентьева Екатерина Ивановна" in teacher_names

    # Математика — один преподаватель
    teachers_math = await get_teachers_for_subject("СОт-115", "Математика")
    assert len(teachers_math) == 1
    assert "Садов" in teachers_math[0]

    # Философия — "Не указан" не возвращается
    teachers_phil = await get_teachers_for_subject("СОт-115", "Философия")
    assert len(teachers_phil) == 0

    # Несуществующий предмет
    teachers_none = await get_teachers_for_subject("СОт-115", "Астрономия")
    assert len(teachers_none) == 0

    # Несуществующая группа
    teachers_no_group = await get_teachers_for_subject("АБВГ-999", "Физика")
    assert len(teachers_no_group) == 0

    await conn.close()
    monkeypatch.setattr(db_mod, "_global_db_conn", None)


@pytest.mark.asyncio
async def test_get_teachers_for_subject_subgroup(teacher_db, monkeypatch):
    """При запросе предмета с подгруппой находит и базовых преподавателей."""
    import app.core.database as db_mod

    conn = await aiosqlite.connect(teacher_db)
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db_mod, "_global_db_conn", conn)

    from app.core.repositories.schedule import get_teachers_for_subject

    # Запрос с подгруппой "(1п/г)" — должен найти и лекционного преподавателя
    teachers = await get_teachers_for_subject("СОт-115", "Физика (1п/г)")
    assert len(teachers) == 2

    await conn.close()
    monkeypatch.setattr(db_mod, "_global_db_conn", None)


def test_format_results_with_teacher_map():
    """format_results корректно отображает преподавателей."""
    data = [
        {
            "semester": "1 семестр 2025/2026",
            "subject": "Физика",
            "grade": "Отлично",
            "date": "2026-01-15",
            "grade_value": 5,
            "is_exam": True,
            "passed": True,
        }
    ]
    teacher_map = {
        "Физика": [
            "Першин Виталий Константинович, Профессор",
            "Авксентьева Екатерина Ивановна, Старший преподаватель",
        ]
    }

    result = format_results(data, teacher_map=teacher_map)
    assert "Першин В.К." in result
    assert "Авксентьева Е.И." in result
    assert "👨\u200d🏫" in result


def test_format_results_without_teacher_map():
    """format_results работает без teacher_map (обратная совместимость)."""
    data = [
        {
            "semester": "1 семестр 2025/2026",
            "subject": "Физика",
            "grade": "Отлично",
            "date": "2026-01-15",
            "grade_value": 5,
            "is_exam": True,
            "passed": True,
        }
    ]

    result = format_results(data)
    assert "Физика" in result
    assert "👨\u200d🏫" not in result


@pytest.mark.asyncio
async def test_get_teachers_fallback_to_teacher_stats(teacher_db, monkeypatch):
    """Если предмет отсутствует в schedule, берёт преподавателя из teacher_stats."""
    import app.core.database as db_mod

    conn = await aiosqlite.connect(teacher_db)
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db_mod, "_global_db_conn", conn)

    from app.core.repositories.schedule import get_teachers_for_subject

    # "История транспорта" есть только в teacher_stats, не в текущем расписании
    teachers = await get_teachers_for_subject("СОт-115", "История транспорта")
    assert len(teachers) == 1
    assert "Старов" in teachers[0]

    # Предмет из текущего расписания — НЕ должен уходить в fallback
    teachers_physics = await get_teachers_for_subject("СОт-115", "Физика")
    assert len(teachers_physics) == 2  # из schedule, не из teacher_stats

    await conn.close()
    monkeypatch.setattr(db_mod, "_global_db_conn", None)
