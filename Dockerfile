# --- ЭТАП 1: "builder" ---
# На этом этапе мы устанавливаем все зависимости в виртуальное окружение.
FROM python:3.13-slim as builder

# Устанавливаем uv - быстрый установщик пакетов
RUN pip install uv

# Создаём виртуальное окружение с помощью uv
ENV VENV_PATH=/opt/venv
RUN uv venv $VENV_PATH

# Копируем файлы с зависимостями
WORKDIR /app
COPY pyproject.toml requirements.txt ./

# Активируем venv и устанавливаем зависимости с помощью uv.
# uv автоматически обработает зависимости из обоих файлов.
RUN . $VENV_PATH/bin/activate && \
    uv pip install --no-cache -r requirements.txt && \
    uv pip install --no-cache .


# --- ЭТАП 2: Финальный образ ---
# Здесь мы создаём чистый образ, копируя только необходимое из "builder".
FROM python:3.13-slim

# Копируем готовое виртуальное окружение из этапа "builder"
ENV VENV_PATH=/opt/venv
COPY --from=builder ${VENV_PATH} ${VENV_PATH}

# Добавляем venv в PATH, чтобы команды (python, playwright) были доступны напрямую
ENV PATH="${VENV_PATH}/bin:${PATH}"

# --- НАЧАЛО ИСПРАВЛЕНИЯ PLAYWRIGHT ---

# 1. Явно указываем Playwright, куда скачивать и где потом искать браузеры.
# Это избавляет от путаницы с домашними директориями /root/.cache и /home/appuser/.cache.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 2. Устанавливаем системные зависимости, сам браузер и выставляем права доступа.
#    - Создаем общую директорию /ms-playwright.
#    - Запускаем `playwright install`, который теперь будет использовать путь из PLAYWRIGHT_BROWSERS_PATH.
#    - Командой `chmod -R 755` мы делаем эту папку доступной для чтения и запуска ЛЮБЫМ пользователем.
RUN mkdir ${PLAYWRIGHT_BROWSERS_PATH} && \
    playwright install --with-deps chromium && \
    chmod -R 755 ${PLAYWRIGHT_BROWSERS_PATH}

# --- КОНЕЦ ИСПРАВЛЕНИЯ PLAYWRIGHT ---

# Создаём пользователя без root-прав для безопасности
ARG UID=1001
RUN useradd -m -s /bin/bash -u ${UID} appuser

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем исходный код приложения и назначаем владельцем нашего пользователя
COPY --chown=appuser:appuser . .

# Переключаемся на пользователя без root-прав
USER appuser

# Создаём директорию для скачанных расписаний, чтобы с ней можно было связать том (volume)
RUN mkdir schedules

# Переменные окружения для работы бота.
# Их нужно будет передать при запуске контейнера.
ENV TELEGRAM_BOT_TOKEN=""
ENV BB_LOGIN=""
ENV BB_PASSWORD=""

# Команда для запуска бота при старте контейнера
CMD ["python", "bot.py"]