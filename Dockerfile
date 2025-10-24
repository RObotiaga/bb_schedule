# --- ЭТАП 1: "builder" ---
# (этот этап остается без изменений)
FROM python:3.13-slim as builder
RUN pip install uv
ENV VENV_PATH=/opt/venv
RUN uv venv $VENV_PATH
WORKDIR /app
COPY pyproject.toml requirements.txt ./
RUN uv pip install --no-cache --python ${VENV_PATH}/bin/python -r requirements.txt .


# --- ЭТАП 2: Финальный образ ---
FROM python:3.13-slim

ENV VENV_PATH=/opt/venv
ENV PATH="${VENV_PATH}/bin:${PATH}"

COPY --from=builder ${VENV_PATH} ${VENV_PATH}

RUN playwright install --with-deps chromium

ARG UID=1001
RUN useradd -m -s /bin/bash -u ${UID} appuser

WORKDIR /app

COPY --chown=appuser:appuser . .

RUN mkdir schedules && chown appuser:appuser schedules

USER appuser

ENV TELEGRAM_BOT_TOKEN=""
ENV BB_LOGIN=""
ENV BB_PASSWORD=""
ENV ADMIN_ID=""

# --- НОВАЯ СТРОКА ---
# Указываем Playwright использовать общесистемный кэш браузеров,
# а не кэш конкретного пользователя (/home/appuser/.cache)
ENV PLAYWRIGHT_BROWSERS_PATH=0

# Команда для запуска нашего стартового скрипта
CMD ["./run.sh"]