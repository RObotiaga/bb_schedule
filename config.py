import os
from datetime import datetime

# --- КОНФИГУРАЦИЯ (УНИФИКАЦИЯ ПУТЕЙ) ---
# Путь, который будет смонтирован через Docker Volume.
# Он должен быть точно таким же, как в docker-compose volumes: /app/data
DB_PATH = os.path.join("data", "schedule.db")

# Путь к папке для скачивания расписаний
DOWNLOAD_DIR = os.path.join("data", "schedules")

# Проверяем и создаем директории для данных, если их нет.
# Это полезно для первого запуска, когда Docker Volume может быть пустым.
if not os.path.exists("data"):
    os.makedirs("data", exist_ok=True)
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
# ---------------------------------------

CURRENT_YEAR = datetime.now().year