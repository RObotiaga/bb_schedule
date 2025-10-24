# --- ЭТАП 1: "builder" ---
# Устанавливаем все зависимости в одно чистое виртуальное окружение.
FROM python:3.13-slim as builder

# Устанавливаем uv
RUN pip install uv

# Создаём и указываем путь к виртуальному окружению
ENV VENV_PATH=/opt/venv
RUN uv venv $VENV_PATH

# Устанавливаем зависимости с помощью uv.
# Копируем только необходимые файлы, чтобы не нарушать кэш Docker
WORKDIR /app
COPY pyproject.toml requirements.txt ./

# --- ИСПРАВЛЕННАЯ СТРОКА ---
# Вызываем системный uv и указываем ему, в какой Python (из нашего venv) ставить пакеты
RUN uv pip install --no-cache --python ${VENV_PATH}/bin/python -r requirements.txt .


# --- ЭТАП 2: Финальный образ ---
# Создаём чистый образ, копируя только необходимое из "builder".
FROM python:3.13-slim

# Задаём путь к venv и сразу добавляем его в PATH.
# Это позволит нам вызывать `python` и `playwright` напрямую в последующих командах.
ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"

# Копируем готовое виртуальное окружение из этапа "builder"
COPY --from=builder ${VENV_PATH} ${VENV_PATH}

# Устанавливаем браузеры Playwright и их СИСТЕМНЫЕ зависимости.
# Скрипт использует только Chromium, поэтому устанавливаем только его для экономии места.
RUN playwright install --with-deps chromium
RUN uv run playwright install --with-deps chromium

# Создаём пользователя без root-прав для безопасности
ARG UID=1001
RUN useradd -m -s /bin/bash -u ${UID} appuser

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем исходный код приложения
COPY --chown=appuser:appuser . .

# Создаём директорию для расписаний и назначаем владельцем нашего пользователя
RUN mkdir schedules && chown appuser:appuser schedules

# Переключаемся на пользователя без root-прав
USER appuser

# Переменные окружения для работы бота (будут переданы при запуске)
ENV TELEGRAM_BOT_TOKEN=""
ENV BB_LOGIN=""
ENV BB_PASSWORD=""

# Команда для запуска приложения
CMD ["./run.sh"]