# Stage 1: Base (Dependencies)
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy AS base
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
RUN pytest tests/test_scenario_flow.py
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