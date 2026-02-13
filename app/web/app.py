import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn
import os

from app.core.database import get_db_connection, load_structure_from_db, get_user_group_db
from app.core.state import GlobalState
from app.core.config import BASE_DIR

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск веб-приложения. Линк со структурой...")
    # Используем GlobalState, который может быть уже инициализирован ботом, 
    # но если веб запускается отдельно, надо инициализировать.
    if not GlobalState.FACULTIES_LIST:
        await GlobalState.reload()
    yield
    logger.info("Остановка веб-приложения.")

app = FastAPI(lifespan=lifespan, title="USURT Schedule")

# Путь к шаблонам. Если мы в app/web/, то templates в ../../templates? 
# Нет, лучше переместить templates в app/web/templates или оставить в корне.
# В плане было app/web/templates. Но пока оставим как есть или настроим путь.
# Если запускаем из корня (python -m app.main), то templates должны быть доступны.
# BASE_DIR указывает на корень проекта.
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
if not os.path.exists(TEMPLATES_DIR):
    # Fallback to local if running differently
    TEMPLATES_DIR = "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# API Endpoints
@app.get("/api/faculties")
async def get_faculties():
    return GlobalState.FACULTIES_LIST

@app.get("/api/courses/{faculty}")
async def get_courses(faculty: str):
    if faculty not in GlobalState.STRUCTURED_DATA:
        return []
    courses = list(GlobalState.STRUCTURED_DATA[faculty].keys())
    try:
        courses.sort(key=lambda x: int(x) if x.isdigit() else x)
    except:
        courses.sort()
    return courses

@app.get("/api/groups/{faculty}/{course}")
async def get_groups(faculty: str, course: str):
    if faculty not in GlobalState.STRUCTURED_DATA or course not in GlobalState.STRUCTURED_DATA[faculty]:
        return []
    groups = GlobalState.STRUCTURED_DATA[faculty][course]
    groups.sort()
    return groups

@app.get("/api/resolve_user")
async def resolve_user(user_id: int):
    group = await get_user_group_db(user_id)
    if group:
        return {"group": group}
    return {"error": "User not found or group not set"}

# Web Routes
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, user_id: int = None):
    if user_id:
        group = await get_user_group_db(user_id)
        if group:
             return RedirectResponse(url=f"/schedule?group={group}")
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/schedule", response_class=HTMLResponse)
async def view_schedule(request: Request, group: str):
    if not group:
         return templates.TemplateResponse("index.html", {"request": request, "error": "Группа не выбрана"})

    schedule_data = []
    async with await get_db_connection() as db:
        db.row_factory = None
        async with db.execute(
            "SELECT lesson_date, time, subject, teacher, location, week_type FROM schedule WHERE group_name = ? ORDER BY lesson_date, time", 
            (group,)
        ) as cursor:
            rows = await cursor.fetchall()
            
            from itertools import groupby
            for date, items in groupby(rows, key=lambda x: x[0]):
                lessons = []
                week_type = ""
                for item in items:
                    lessons.append({
                        "time": item[1],
                        "subject": item[2],
                        "teacher": item[3],
                        "location": item[4]
                    })
                    week_type = item[5]
                
                try:
                    from datetime import datetime
                    dt = datetime.strptime(date, '%Y-%m-%d')
                    date_display = dt.strftime('%d.%m.%Y')
                    day_name = dt.strftime('%A')
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

async def run_web():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, reload=False)
    server = uvicorn.Server(config)
    await server.serve()
