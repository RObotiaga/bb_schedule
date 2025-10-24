#!/bin/bash

# --- Конфигурация ---
IMAGE_NAME="bb-schedule-app"
CONTAINER_NAME="bb-schedule-bot"

# --- Логика скрипта ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Проверка .env файла
if [ ! -f .env ]; then
    echo -e "${YELLOW}Файл .env не найден! Пожалуйста, создайте его и заполните.${NC}"
    exit 1
fi

# 2. Проверка и создание директории для расписаний на хосте
if [ ! -d "schedules" ]; then
    echo -e "${YELLOW}Директория 'schedules' не найдена. Создаю...${NC}"
    mkdir schedules
fi

# 3. Остановка и удаление старого контейнера
echo "Проверяем наличие старого контейнера..."
if [ $(podman ps -a -q -f name=^/${CONTAINER_NAME}$) ]; then
    echo "Останавливаем и удаляем старый контейнер ($CONTAINER_NAME)..."
    podman stop $CONTAINER_NAME
    podman rm $CONTAINER_NAME
fi

# 4. Сборка нового образа
echo -e "\n${GREEN}Собираем новый образ '$IMAGE_NAME'...${NC}"
podman build -t $IMAGE_NAME .
if [ $? -ne 0 ]; then
    echo -e "\n${YELLOW}Ошибка при сборке образа. Запуск прерван.${NC}"
    exit 1
fi

# 5. Запуск нового контейнера
echo -e "\n${GREEN}Запускаем контейнер '$CONTAINER_NAME'...${NC}"
podman run -d \
    --name $CONTAINER_NAME \
    --env-file .env \
    -v $(pwd)/schedules:/app/schedules \
    --restart unless-stopped \
    $IMAGE_NAME

echo -e "\n${GREEN}Готово! Контейнер запущен в фоновом режиме.${NC}"
echo "Чтобы посмотреть логи, используйте команду: podman logs -f $CONTAINER_NAME"
echo "Чтобы остановить контейнер, используйте команду: podman stop $CONTAINER_NAME"