import asyncio
import gzip
import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from itertools import groupby
from typing import Any
from urllib.parse import parse_qsl, urlencode

import uvicorn
from aiogram import Bot
from aiogram.types import BufferedInputFile
from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.bot.formatter import filter_results_by_settings
from app.bot.handlers.teachers import is_teacher_match
from app.core.config import ADMIN_ID, BASE_DIR, DB_PATH, TELEGRAM_BOT_TOKEN
from app.core.database import close_db_connection, get_db_connection, initialize_database
from app.core.repositories.job_log import get_last_two_job_logs
from app.core.repositories.rating import (
    get_cluster_by_group,
    get_cluster_subjects,
    get_expelled_statistics,
    get_group_by_record_book,
    get_rating_position,
    get_student_cluster_info,
    get_top_students,
)
from app.core.repositories.schedule import get_schedule_by_teacher, get_teachers_for_subject
from app.core.repositories.subject import (
    get_cluster_subject_stats,
    get_global_subject_stats,
    get_record_book_subjects,
    get_record_books_in_cluster,
    get_subject_note,
    get_subject_status_in_cluster,
    get_subjects_with_stats,
    get_subscribed_teachers,
    is_subscribed_to_teacher,
    save_subject_note,
    subscribe_teacher,
    unsubscribe_teacher,
)
from app.core.repositories.user import (
    get_all_user_ids,
    get_record_book_number,
    get_user_group_db,
    get_user_settings,
    get_users_by_record_book,
    save_record_book_number,
    save_user_group_db,
    update_user_settings,
)
from app.core.state import GlobalState
from app.services.db_transfer import export_rating_data, import_rating_data
from app.services.rating_updater import run_rating_update
from app.services.schedule_api import UsurtScraper
from app.services.schedule_sync import run_full_sync

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск веб-приложения. Загрузка структуры расписания...")
    await initialize_database()
    if not GlobalState.FACULTIES_LIST:
        await GlobalState.reload()
    yield
    logger.info("Остановка веб-приложения.")
    await close_db_connection()


app = FastAPI(lifespan=lifespan, title="USURT Schedule")

TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
if not os.path.exists(TEMPLATES_DIR):
    TEMPLATES_DIR = "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)


class JobRegistry:
    def __init__(self):
        self._locks = {
            "schedule_sync": asyncio.Lock(),
            "rating_update": asyncio.Lock(),
            "db_import": asyncio.Lock(),
            "broadcast": asyncio.Lock(),
        }
        self._jobs: dict[str, dict[str, Any]] = {
            name: {"name": name, "status": "idle", "message": "", "updated_at": None}
            for name in self._locks
        }

    def snapshot(self, name: str | None = None):
        if name:
            return dict(self._jobs[name])
        return {job_name: dict(data) for job_name, data in self._jobs.items()}

    def start(self, name: str, coro_factory):
        if name not in self._locks:
            raise HTTPException(status_code=404, detail="Unknown job")
        if self._locks[name].locked():
            return self.snapshot(name)
        asyncio.create_task(self._runner(name, coro_factory))
        self._jobs[name] = {
            "name": name,
            "status": "queued",
            "message": "Задача поставлена в очередь",
            "updated_at": datetime.now().isoformat(),
        }
        return self.snapshot(name)

    async def _runner(self, name: str, coro_factory):
        async with self._locks[name]:
            self._jobs[name] = {
                "name": name,
                "status": "running",
                "message": "Задача выполняется",
                "updated_at": datetime.now().isoformat(),
            }
            try:
                result = await coro_factory()
                self._jobs[name] = {
                    "name": name,
                    "status": "success",
                    "message": str(result or "Готово"),
                    "updated_at": datetime.now().isoformat(),
                }
            except Exception as exc:
                logger.exception("Web admin job failed: %s", name)
                self._jobs[name] = {
                    "name": name,
                    "status": "error",
                    "message": str(exc),
                    "updated_at": datetime.now().isoformat(),
                }


jobs = JobRegistry()


def verify_telegram_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict[str, Any]:
    if not init_data:
        raise ValueError("initData is empty")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("initData hash is missing")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("initData hash is invalid")

    auth_date_raw = pairs.get("auth_date")
    if auth_date_raw:
        auth_date = int(auth_date_raw)
        if time.time() - auth_date > max_age_seconds:
            raise ValueError("initData is expired")

    user_raw = pairs.get("user")
    if not user_raw:
        raise ValueError("initData user is missing")
    user = json.loads(user_raw)
    return {"user": user, "raw": pairs}


async def get_current_user(x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    try:
        data = verify_telegram_init_data(x_telegram_init_data or "", TELEGRAM_BOT_TOKEN)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData") from exc

    user = data["user"]
    user_id = int(user["id"])
    return {
        "id": user_id,
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "is_admin": user_id == ADMIN_ID,
    }


async def require_admin(user: dict = Depends(get_current_user)):
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def _date_label(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    days_map = {
        "Monday": "Понедельник",
        "Tuesday": "Вторник",
        "Wednesday": "Среда",
        "Thursday": "Четверг",
        "Friday": "Пятница",
        "Saturday": "Суббота",
        "Sunday": "Воскресенье",
    }
    return f"{days_map.get(dt.strftime('%A'), dt.strftime('%A'))}, {dt.strftime('%d.%m.%Y')}"


def _lesson_dict(row) -> dict[str, Any]:
    return {
        "time": row["time"],
        "subject": row["subject"],
        "teacher": row["teacher"],
        "location": row["location"],
        "week_type": row["week_type"],
        "group_name": row["group_name"] if "group_name" in row.keys() else None,
    }


async def _resolve_schedule_group(group: str | None) -> str | None:
    if not group:
        return None
    db = await get_db_connection()
    async with db.execute("SELECT 1 FROM schedule WHERE group_name = ? LIMIT 1", (group,)) as cursor:
        if await cursor.fetchone():
            return group

    target = group.casefold()
    async with db.execute("SELECT DISTINCT group_name FROM schedule") as cursor:
        rows = await cursor.fetchall()
    matches = [row["group_name"] for row in rows if row["group_name"] and row["group_name"].casefold() == target]
    return matches[0] if len(matches) == 1 else group


async def _schedule_for_group(group: str, target_date: str | None = None, user_id: int | None = None):
    group = await _resolve_schedule_group(group) or group
    db = await get_db_connection()
    params: tuple[Any, ...]
    if target_date:
        query = """
            SELECT lesson_date, time, subject, teacher, location, week_type, group_name
            FROM schedule
            WHERE group_name = ? AND lesson_date = ?
            ORDER BY lesson_date, time
        """
        params = (group, target_date)
    else:
        query = """
            SELECT lesson_date, time, subject, teacher, location, week_type, group_name
            FROM schedule
            WHERE group_name = ?
            ORDER BY lesson_date, time
        """
        params = (group,)

    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()

    extra_rows = []
    if user_id and target_date:
        for teacher in await get_subscribed_teachers(user_id):
            teacher_lessons = await get_schedule_by_teacher(teacher, target_date)
            extra_rows.extend(teacher_lessons)

    all_rows = list(rows) + list(extra_rows)
    all_rows.sort(key=lambda r: (r["lesson_date"], r["time"], r["subject"], r["teacher"], r["location"]))

    days = []
    for day, items in groupby(all_rows, key=lambda row: row["lesson_date"]):
        lessons = []
        week_type = ""
        seen = set()
        for row in items:
            key = (row["time"], row["subject"], row["teacher"], row["location"], row["group_name"])
            if key in seen:
                continue
            seen.add(key)
            lesson = _lesson_dict(row)
            lesson["is_subscription"] = row["group_name"] != group
            lessons.append(lesson)
            week_type = row["week_type"]
        days.append({"date": day, "date_display": _date_label(day), "week_type": week_type, "lessons": lessons})
    return days


async def _teacher_schedule(teacher_name: str, day_offset: int):
    target_date = date.today() + timedelta(days=day_offset)
    lessons_raw = await get_schedule_by_teacher(teacher_name, target_date.strftime("%Y-%m-%d"))
    merged = {}
    for lesson in lessons_raw:
        key = (lesson["time"], lesson["subject"], lesson["location"])
        if key not in merged:
            item = dict(lesson)
            item["groups"] = [lesson["group_name"]]
            merged[key] = item
        else:
            merged[key]["groups"].append(lesson["group_name"])
    lessons = list(merged.values())
    lessons.sort(key=lambda item: item["time"])
    return {
        "teacher": teacher_name,
        "date": target_date.strftime("%Y-%m-%d"),
        "date_display": _date_label(target_date.strftime("%Y-%m-%d")),
        "lessons": lessons,
    }


async def _session_payload(user_id: int, record_book: str, use_cache: bool = True):
    from app.core.repositories.rating import get_group_by_record_book

    settings = await get_user_settings(user_id)
    status, data = await UsurtScraper.get_session_results(record_book, use_cache=use_cache)
    if status != "SUCCESS" or data is None:
        return {"status": status, "record_book": record_book, "results": [], "summary": None}

    rating_info = {}
    for scope in ("cluster", "year", "all"):
        pos = await get_rating_position(record_book, scope)
        if pos:
            rating_info[f"{scope}_pos"] = pos

    cluster_id = None
    db = await get_db_connection()
    async with db.execute("SELECT cluster_id FROM rating_data WHERE record_book = ?", (record_book,)) as cur:
        row = await cur.fetchone()
        if row and row[0]:
            cluster_id = row[0]

    cluster_subject_stats = await get_cluster_subject_stats(cluster_id) if cluster_id else {}
    subject_stats = {}
    for item in data:
        subject = item.get("subject", "").strip()
        if subject and subject not in subject_stats:
            stats = await get_global_subject_stats(subject)
            if stats:
                subject_stats[subject] = stats

    teacher_map = {}
    student_group = await get_group_by_record_book(record_book)
    if student_group:
        seen = set()
        for item in data:
            subject = item.get("subject", "").strip()
            if subject and subject not in seen:
                seen.add(subject)
                teachers = await get_teachers_for_subject(student_group, subject)
                if teachers:
                    teacher_map[subject] = teachers

    filtered = filter_results_by_settings(data, settings)
    total = len(data)
    passed = sum(1 for item in data if item.get("passed"))
    debts = sum(1 for item in filtered if not item.get("passed"))
    return {
        "status": "SUCCESS",
        "record_book": record_book,
        "results": filtered,
        "raw_results": data,
        "settings": settings,
        "rating": rating_info,
        "subject_stats": subject_stats,
        "cluster_subject_stats": cluster_subject_stats,
        "teacher_map": teacher_map,
        "summary": {
            "total": total,
            "passed": passed,
            "pass_rate": round((passed / total * 100), 1) if total else 0,
            "debts": debts,
        },
    }


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "schedule_web"}


@app.get("/api/faculties")
async def get_faculties():
    return GlobalState.FACULTIES_LIST


@app.get("/api/courses/{faculty}")
async def get_courses(faculty: str):
    if faculty not in GlobalState.STRUCTURED_DATA:
        return []
    courses = list(GlobalState.STRUCTURED_DATA[faculty].keys())
    try:
        courses.sort(key=lambda x: int(x) if str(x).isdigit() else x)
    except TypeError:
        courses.sort()
    return courses


@app.get("/api/groups/{faculty}/{course}")
async def get_groups(faculty: str, course: str):
    if faculty not in GlobalState.STRUCTURED_DATA or course not in GlobalState.STRUCTURED_DATA[faculty]:
        return []
    return sorted(GlobalState.STRUCTURED_DATA[faculty][course])


@app.get("/api/resolve_user")
async def resolve_user(user_id: int):
    group = await get_user_group_db(user_id)
    if group:
        return {"group": group}
    return {"error": "User not found or group not set"}


@app.get("/api/me")
async def get_me(user: dict = Depends(get_current_user)):
    record_book = await get_record_book_number(user["id"])
    group = await get_user_group_db(user["id"])
    resolved_group = await _resolve_schedule_group(group)
    if group and resolved_group and resolved_group != group:
        await save_user_group_db(user["id"], resolved_group)
        group = resolved_group
    return {
        "user": user,
        "group": group,
        "record_book": record_book,
        "settings": await get_user_settings(user["id"]),
        "subscriptions": await get_subscribed_teachers(user["id"]),
        "is_admin": user["is_admin"],
    }


@app.post("/api/me/group")
async def set_my_group(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    group = payload.get("group")
    if not group:
        raise HTTPException(status_code=400, detail="group is required")
    group = await _resolve_schedule_group(group) or group
    await save_user_group_db(user["id"], group)
    return {"group": group}


@app.post("/api/me/record-book")
async def set_my_record_book(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    record_book = str(payload.get("record_book", "")).strip()
    if not record_book.isdigit():
        raise HTTPException(status_code=400, detail="record_book must contain digits only")
    await save_record_book_number(user["id"], record_book, user.get("username"), user.get("first_name"))
    return {"record_book": record_book}


@app.post("/api/me/settings")
async def set_my_settings(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    settings = payload.get("settings", payload)
    if not isinstance(settings, dict):
        raise HTTPException(status_code=400, detail="settings must be an object")
    await update_user_settings(user["id"], settings)
    return {"settings": settings}


@app.get("/api/schedule")
async def api_schedule(
    group: str | None = None,
    day_offset: int | None = None,
    include_subscriptions: bool = True,
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data"),
):
    user = None
    if x_telegram_init_data:
        user = await get_current_user(x_telegram_init_data)
    if not group and user:
        group = await get_user_group_db(user["id"])
    if not group:
        raise HTTPException(status_code=400, detail="group is required")
    group = await _resolve_schedule_group(group) or group
    target_date = None
    if day_offset is not None:
        target_date = (date.today() + timedelta(days=day_offset)).strftime("%Y-%m-%d")
    days = await _schedule_for_group(group, target_date, user["id"] if user and include_subscriptions else None)
    return {"group": group, "days": days}


@app.get("/api/teachers/search")
async def api_teacher_search(q: str):
    q = q.strip()
    if not q:
        return {"teachers": []}
    matches = [teacher for teacher in GlobalState.ALL_TEACHERS_LIST if is_teacher_match(q, teacher)]
    return {"teachers": matches[:50], "total": len(matches)}


@app.get("/api/teachers/{teacher_name}/schedule")
async def api_teacher_schedule(teacher_name: str, day_offset: int = 0, user: dict = Depends(get_current_user)):
    data = await _teacher_schedule(teacher_name, day_offset)
    data["is_subscribed"] = await is_subscribed_to_teacher(user["id"], teacher_name)
    return data


@app.post("/api/teachers/{teacher_name}/subscribe")
async def api_teacher_subscribe(teacher_name: str, payload: dict = Body(default={}), user: dict = Depends(get_current_user)):
    subscribed = bool(payload.get("subscribed", True))
    if subscribed:
        await subscribe_teacher(user["id"], teacher_name)
    else:
        await unsubscribe_teacher(user["id"], teacher_name)
    return {"teacher": teacher_name, "is_subscribed": subscribed}


@app.get("/api/session/results")
async def api_session_results(refresh: bool = False, user: dict = Depends(get_current_user)):
    record_book = await get_record_book_number(user["id"])
    if not record_book:
        raise HTTPException(status_code=404, detail="record_book is not set")
    return await _session_payload(user["id"], record_book, use_cache=not refresh)


@app.post("/api/session/results")
async def api_session_results_for_record(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    record_book = str(payload.get("record_book", "")).strip()
    if not record_book.isdigit():
        raise HTTPException(status_code=400, detail="record_book must contain digits only")
    await save_record_book_number(user["id"], record_book, user.get("username"), user.get("first_name"))
    return await _session_payload(user["id"], record_book, use_cache=not payload.get("refresh", False))


@app.get("/api/session/notes")
async def api_get_note(subject: str, user: dict = Depends(get_current_user)):
    return await get_subject_note(user["id"], subject)


@app.post("/api/session/notes")
async def api_save_note(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    subject = payload.get("subject")
    if not subject:
        raise HTTPException(status_code=400, detail="subject is required")
    note = payload.get("note_text", "")
    checklist = payload.get("checklist")
    if checklist is None:
        current = await get_subject_note(user["id"], subject)
        checklist = current.get("checklist", [])
    await save_subject_note(user["id"], subject, note, checklist)
    return await get_subject_note(user["id"], subject)


@app.post("/api/session/notes/checklist")
async def api_update_checklist(payload: dict = Body(...), user: dict = Depends(get_current_user)):
    subject = payload.get("subject")
    action = payload.get("action")
    if not subject or action not in {"add", "toggle", "delete"}:
        raise HTTPException(status_code=400, detail="subject and valid action are required")
    current = await get_subject_note(user["id"], subject)
    checklist = current.get("checklist", [])
    if action == "add":
        text = str(payload.get("text", "")).strip()
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        checklist.append({"text": text, "done": False})
    else:
        idx = int(payload.get("index", -1))
        if idx < 0 or idx >= len(checklist):
            raise HTTPException(status_code=400, detail="index is invalid")
        if action == "toggle":
            checklist[idx]["done"] = not checklist[idx].get("done", False)
        elif action == "delete":
            checklist.pop(idx)
    await save_subject_note(user["id"], subject, current.get("note_text", ""), checklist)
    return await get_subject_note(user["id"], subject)


@app.get("/api/rating/group")
async def api_group_rating(user: dict = Depends(get_current_user)):
    record_book = await get_record_book_number(user["id"])
    if not record_book:
        raise HTTPException(status_code=404, detail="record_book is not set")
    info = await get_student_cluster_info(record_book)
    if not info or info["is_expelled"]:
        return {"students": [], "cluster_id": info["cluster_id"] if info else None}
    students = await get_top_students(scope="cluster", scope_value=info["cluster_id"], limit=100)
    for pos, student in enumerate(students, start=1):
        student["position"] = pos
        student["is_current"] = student["record_book"] == record_book
        if user["is_admin"]:
            student["users"] = await get_users_by_record_book(student["record_book"])
    return {"cluster_id": info["cluster_id"], "students": students}


@app.get("/api/subjects")
async def api_subjects(q: str | None = None):
    subjects = await get_subjects_with_stats()
    if q:
        q_lower = q.lower()
        subjects = [subject for subject in subjects if q_lower in subject.lower()]
    return {"subjects": subjects[:100], "total": len(subjects)}


@app.get("/api/subjects/{subject_name}/stats")
async def api_subject_stats(subject_name: str):
    stats = await get_global_subject_stats(subject_name)
    if not stats:
        raise HTTPException(status_code=404, detail="subject stats not found")
    return {"subject": subject_name, "stats": stats}


@app.get("/api/admin/jobs")
async def api_admin_jobs(admin: dict = Depends(require_admin)):
    return jobs.snapshot()


@app.get("/api/admin/status")
async def api_admin_status(admin: dict = Depends(require_admin)):
    return {
        "schedule_sync": await get_last_two_job_logs("schedule_sync"),
        "rating_update": await get_last_two_job_logs("rating_update"),
        "jobs": jobs.snapshot(),
    }


@app.post("/api/admin/jobs/{job_name}/start")
async def api_admin_start_job(job_name: str, admin: dict = Depends(require_admin)):
    if job_name == "schedule_sync":
        async def run():
            success = await run_full_sync()
            if success:
                await GlobalState.reload()
            return "Расписание обновлено" if success else "Обновление завершилось с ошибкой"

        return jobs.start("schedule_sync", run)
    if job_name == "rating_update":
        return jobs.start("rating_update", lambda: run_rating_update())
    if job_name == "reload_structure":
        await GlobalState.reload()
        return {"status": "success", "message": "Структура перезагружена"}
    raise HTTPException(status_code=404, detail="Unknown job")


@app.get("/api/admin/rating/export")
async def api_admin_export_rating(admin: dict = Depends(require_admin)):
    data = await export_rating_data()
    compressed = gzip.compress(data.encode("utf-8"))
    headers = {"Content-Disposition": 'attachment; filename="rating_export.json.gz"'}
    return Response(content=compressed, media_type="application/gzip", headers=headers)


@app.post("/api/admin/rating/import")
async def api_admin_import_rating(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    raw = await file.read()
    if file.filename and file.filename.endswith(".gz"):
        raw = gzip.decompress(raw)
    success = await import_rating_data(raw.decode("utf-8"))
    if not success:
        raise HTTPException(status_code=400, detail="rating import failed")
    return {"status": "success"}


@app.post("/api/admin/db/import")
async def api_admin_import_db(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    if not file.filename or not file.filename.endswith(".db"):
        raise HTTPException(status_code=400, detail="Only .db files are allowed")
    if jobs._locks["db_import"].locked():
        return jobs.snapshot("db_import")

    raw = await file.read()

    async def run():
        await close_db_connection()
        with open(DB_PATH, "wb") as db_file:
            db_file.write(raw)
        await initialize_database()
        await GlobalState.reload()
        return "База данных импортирована"

    return jobs.start("db_import", run)


@app.get("/api/admin/expelled")
async def api_admin_expelled(admin: dict = Depends(require_admin)):
    return await get_expelled_statistics()


@app.get("/api/admin/group/{group_name}")
async def api_admin_group(group_name: str, admin: dict = Depends(require_admin)):
    cluster_id = await get_cluster_by_group(group_name)
    if cluster_id is None:
        raise HTTPException(status_code=404, detail="cluster not found")
    subjects = sorted(list(await get_cluster_subjects(cluster_id)))
    records = await get_record_books_in_cluster(cluster_id)
    return {"group": group_name, "cluster_id": cluster_id, "subjects": subjects, "record_books": records}


@app.get("/api/admin/group/{group_name}/subject/{subject_name}")
async def api_admin_group_subject(group_name: str, subject_name: str, admin: dict = Depends(require_admin)):
    cluster_id = await get_cluster_by_group(group_name)
    if cluster_id is None:
        raise HTTPException(status_code=404, detail="cluster not found")
    return {"group": group_name, "subject": subject_name, "statuses": await get_subject_status_in_cluster(cluster_id, subject_name)}


@app.get("/api/admin/record-book/{record_book}")
async def api_admin_record_book(record_book: str, admin: dict = Depends(require_admin)):
    subjects = await get_record_book_subjects(record_book)
    group_name = await get_group_by_record_book(record_book)
    rating = {}
    for scope in ("cluster", "year", "all"):
        pos = await get_rating_position(record_book, scope)
        if pos:
            rating[f"{scope}_pos"] = pos
    return {"record_book": record_book, "group": group_name, "subjects": subjects, "rating": rating}


@app.post("/api/admin/broadcast")
async def api_admin_broadcast(
    text: str = Form(""),
    file: UploadFile | None = File(default=None),
    admin: dict = Depends(require_admin),
):
    if jobs._locks["broadcast"].locked():
        return jobs.snapshot("broadcast")
    file_payload = None
    if file and file.filename:
        file_payload = (await file.read(), file.filename, file.content_type or "application/octet-stream")

    async def run():
        user_ids = await get_all_user_ids()
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        success = 0
        failed = 0
        try:
            for user_id in user_ids:
                try:
                    if file_payload:
                        content, filename, _content_type = file_payload
                        await bot.send_document(
                            user_id,
                            BufferedInputFile(content, filename=filename),
                            caption=text or None,
                        )
                    elif text.strip():
                        await bot.send_message(user_id, text)
                    else:
                        failed += 1
                        continue
                    success += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.05)
        finally:
            await bot.session.close()
        return f"Рассылка завершена. Успешно: {success}, не удалось: {failed}"

    return jobs.start("broadcast", run)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, user_id: int | None = None):
    if user_id:
        group = await get_user_group_db(user_id)
        if group:
            group = await _resolve_schedule_group(group) or group
            return RedirectResponse(url=f"/schedule?{urlencode({'group': group})}")
    return templates.TemplateResponse(request, "index.html", {"now_year": datetime.now().year})


@app.get("/schedule", response_class=HTMLResponse)
async def view_schedule(request: Request, group: str | None = None):
    if not group:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"error": "Группа не выбрана", "now_year": datetime.now().year},
        )

    group = await _resolve_schedule_group(group) or group
    schedule_data = await _schedule_for_group(group)
    if not schedule_data:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "error": f"Расписание для группы {group} не найдено.",
                "now_year": datetime.now().year,
            },
        )

    return templates.TemplateResponse(
        request,
        "schedule.html",
        {"group": group, "schedule": schedule_data, "now_year": datetime.now().year},
    )


async def run_web():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, reload=False)
    server = uvicorn.Server(config)
    await server.serve()
