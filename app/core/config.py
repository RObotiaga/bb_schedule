import os
import sys
from decouple import config

# Определяем базовую директорию проекта (на уровень выше от app/core/config.py -> app/core -> app -> root)
# Или просто os.getcwd() если запускаем из корня
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None)
ADMIN_ID = config("ADMIN_ID", default=None, cast=int)

BB_LOGIN = config("BB_LOGIN", default=None)
BB_PASSWORD = config("BB_PASSWORD", default=None)
BB_URL = config("BB_URL", default="https://bb.usurt.ru/")

# Пути к данным
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "schedule.db")
DOWNLOAD_DIR = os.path.join(DATA_DIR, "schedules")

# Создаем директории если их нет
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Проверка критических переменных
if not TELEGRAM_BOT_TOKEN:
    print("Критическая ошибка: TELEGRAM_BOT_TOKEN не задан!")
    sys.exit(1)

if not ADMIN_ID:
    print("Критическая ошибка: ADMIN_ID не задан!")
    sys.exit(1)
