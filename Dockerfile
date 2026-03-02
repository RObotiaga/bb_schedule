# Stage 1: Base (Dependencies)
FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Test (Run Tests)
FROM base AS test
COPY . .
# Set dummy environment variables required for the bot/tests to initialize
ENV TELEGRAM_BOT_TOKEN=test_token_for_build
ENV ADMIN_ID=12345678
# Install test dependencies
RUN pip install pytest pytest-asyncio pytest-mock pytest-cov
# Run the specific scenario test (or all tests)
# Ensure pytest return code 0 propagates to build success, non-zero stops build
RUN pytest tests/test_scenario_flow.py tests/test_imports.py
# Create a marker file to indicate tests passed
RUN touch /tmp/tests_passed

# Stage 3: Final (Production Image)
FROM base AS final
# Force dependency on test stage by copying the marker file
COPY --from=test /tmp/tests_passed /tmp/tests_passed
COPY . .
RUN mkdir -p /app/data/schedules
# Default command to run the bot
CMD ["python", "-m", "app.main", "bot"]

# Add Healthcheck (simple check if process is running or extend app to have /health endpoint)
# Since this is a bot, a simple PID check or similar is hard without extra tools.
# However, for the Web App container, we can check the endpoint.
# For the Bot container, if it crashes, it exits. Docker handles restart.
# To make it "unhealthy" on hang, we'd need a heartbeat file.
# For now, let's leave it standard.