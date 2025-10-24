# --- ЭТАП 1: "builder" ---
# Этот этап остается без изменений. Он собирает чистое виртуальное окружение.
FROM python:3.13-slim as builder

RUN pip install uv

ENV VENV_PATH=/opt/venv
RUN uv venv $VENV_PATH

WORKDIR /app
COPY pyproject.toml requirements.txt ./

RUN uv pip install --no-cache --python ${VENV_PATH}/bin/python -r requirements.txt .


# --- ЭТАП 2: Финальный образ ---
# Здесь мы создаем чистый образ для запуска приложения.
FROM python:3.13-slim

# --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ №1: Задаем глобальный путь для браузеров ---
# Мы явно указываем Playwright, куда скачивать и где потом искать браузеры.
# Это избавляет от путаницы с домашними директориями /root/.cache и /home/appuser/.cache.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Настраиваем пути для виртуального окружения Python
ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"

# Копируем готовое виртуальное окружение из этапа "builder"
COPY --from=builder ${VENV_PATH} ${VENV_PATH}

# --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ №2: Устанавливаем браузер и выставляем права ---
# 1. Создаем общую директорию /ms-playwright.
# 2. Запускаем `playwright install`, который теперь будет использовать путь из PLAYWRIGHT_BROWSERS_PATH.
# 3. Командой `chmod -R 755` мы делаем эту папку и все файлы в ней доступными для чтения и запуска ЛЮБЫМ пользователем.
#    Это критически важно, чтобы наш не-рутовый 'appuser' смог запустить браузер.
RUN mkdir ${PLAYWRIGHT_BROWSERS_PATH} && \
    playwright install --with-deps chromium && \
    chmod -R 755 ${PLAYWRIGHT_BROWSERS_PATH}

# Создаем пользователя без root-прав для безопасности
ARG UID=1001
RUN useradd -m -s /bin/bash -u ${UID} appuser

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем исходный код приложения
COPY --chown=appuser:appuser . .

# Создаем директорию для расписаний и назначаем владельцем нашего пользователя
RUN mkdir schedules && chown appuser:appuser schedules

# Переключаемся на пользователя без root-прав
USER appuser

# Переменные окружения для работы бота (будут переданы при запуске)
ENV TELEGRAM_BOT_TOKEN=""
ENV BB_LOGIN=""
ENV BB_PASSWORD=""

# Команда для запуска нашего стартового скрипта
CMD ["./run.sh"]