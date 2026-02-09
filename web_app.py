import logging
import json
from contextlib import asynccontextmanager
from typing import List, Dict, Any

from fastapi import FastAPI, Request, Form, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn

from database import get_db_connection, load_structure_from_db, get_schedule_by_group
from config import DB_PATH

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Глобальные переменные для хранения структуры (кэш)
STRUCTURE_CACHE = {}
FACULTIES_CACHE = []

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Управление жизненным циклом приложения.
    Загружаем структуру при старте.
    """
    global STRUCTURE_CACHE, FACULTIES_CACHE
    logger.info("Запуск веб-приложения. Загрузка структуры расписания...")
    try:
        structure, faculties, _ = await load_structure_from_db()
        STRUCTURE_CACHE = structure
        FACULTIES_CACHE = faculties
        logger.info(f"Структура загружена: {len(FACULTIES_CACHE)} факультетов.")
    except Exception as e:
        logger.error(f"Ошибка при загрузке структуры: {e}")
    
    yield
    
    logger.info("Остановка веб-приложения.")

app = FastAPI(lifespan=lifespan, title="USURT Schedule")

# Подключаем шаблоны
templates = Jinja2Templates(directory="templates")

# --- API Endpoints для динамических списков ---

@app.get("/api/faculties")
async def get_faculties():
    return FACULTIES_CACHE

@app.get("/api/courses/{faculty}")
async def get_courses(faculty: str):
    if faculty not in STRUCTURE_CACHE:
        return []
    # Сортируем курсы
    courses = list(STRUCTURE_CACHE[faculty].keys())
    # Попробуем отсортировать как числа, если это возможно, иначе лексикографически
    try:
        courses.sort(key=lambda x: int(x) if x.isdigit() else x)
    except:
        courses.sort()
    return courses

@app.get("/api/groups/{faculty}/{course}")
async def get_groups(faculty: str, course: str):
    if faculty not in STRUCTURE_CACHE or course not in STRUCTURE_CACHE[faculty]:
        return []
    groups = STRUCTURE_CACHE[faculty][course]
    groups.sort()
    return groups

@app.get("/api/resolve_user")
async def resolve_user(user_id: int):
    """
    API для определения группы пользователя по его Telegram ID.
    Используется frontend-ом (Telegram Web App) и для редиректов.
    """
    from database import get_user_group_db
    group = await get_user_group_db(user_id)
    if group:
        return {"group": group}
    return {"error": "User not found or group not set"}

# --- Web Routes ---

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, user_id: int = None):
    """
    Главная страница.
    Если передан user_id (в query param), пытаемся определить группу и сделать редирект.
    Также JS на странице будет пытаться определить пользователя через Telegram Web App.
    """
    if user_id:
        from database import get_user_group_db
        group = await get_user_group_db(user_id)
        if group:
             # Если группа найдена, редиректим на расписание
             from fastapi.responses import RedirectResponse
             return RedirectResponse(url=f"/schedule?group={group}")

    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/schedule", response_class=HTMLResponse)
async def view_schedule(request: Request, group: str):
    """Страница отображения расписания для группы."""
    if not group:
         return templates.TemplateResponse("index.html", {"request": request, "error": "Группа не выбрана"})

    # Мы не можем просто взять "все" расписание, так как get_schedule_by_group требует дату.
    # Нам нужно отобразить ВСЕ расписание, сгруппированное по датам/неделям.
    # Функция в database.py (get_schedule_by_group) фильтрует по дате.
    # Нам нужна новая функция или мы сделаем RAW запрос здесь (или добавим в database.py).
    # Для чистоты архитектуры, лучше использовать database.py, но там нет метода "получить всё для группы".
    # Сделаем прямой запрос здесь для скорости, или добавим метод в database.py.
    # ДАВАЙТЕ ДОБАВИМ МЕТОД в database.py в следующем шаге, а пока сделаем запрос тут.
    # Или, лучше, сделаем запрос "SELECT * FROM schedule WHERE group_name = ? ORDER BY lesson_date, time"
    
    schedule_data = []
    async with await get_db_connection() as db:
        db.row_factory = None # Возвращаем tuple для простоты или aiosqlite.Row
        async with db.execute(
            "SELECT lesson_date, time, subject, teacher, location, week_type FROM schedule WHERE group_name = ? ORDER BY lesson_date, time", 
            (group,)
        ) as cursor:
            rows = await cursor.fetchall()
            
            # Группировка по дням
            # rows: [(date, time, subj, teach, loc, week_type), ...]
            from itertools import groupby
            
            for date, items in groupby(rows, key=lambda x: x[0]):
                lessons = []
                week_type = ""
                for item in items:
                    # item: (date, time, subj, teach, loc, week_type)
                    lessons.append({
                        "time": item[1],
                        "subject": item[2],
                        "teacher": item[3],
                        "location": item[4]
                    })
                    week_type = item[5] # Берем из любой пары
                
                # Форматируем дату
                # lesson_date expected YYYY-MM-DD
                try:
                    from datetime import datetime
                    dt = datetime.strptime(date, '%Y-%m-%d')
                    date_display = dt.strftime('%d.%m.%Y')
                    day_name = dt.strftime('%A')
                    # Русские дни недели
                    days_map = {
                        'Monday': 'Понедельник', 'Tuesday': 'Вторник', 'Wednesday': 'Среда',
                        'Thursday': 'Четверг', 'Friday': 'Пятница', 'Saturday': 'Суббота', 'Sunday': 'Воскресенье'
                    }
                    day_name_ru = days_map.get(day_name, day_name)
                    full_date_display = f"{day_name_ru}, {date_display}"
                except:
                    full_date_display = date

                schedule_data.append({
                    "date": full_date_display,
                    "week_type": week_type,
                    "lessons": lessons
                })

    if not schedule_data:
        return templates.TemplateResponse("index.html", {
            "request": request, 
            "error": f"Расписание для группы {group} не найдено."
        })

    return templates.TemplateResponse("schedule.html", {
        "request": request, 
        "group": group, 
        "schedule": schedule_data
    })

if __name__ == "__main__":
    uvicorn.run("web_app:app", host="0.0.0.0", port=8000, reload=True)
