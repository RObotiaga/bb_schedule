#!/bin/bash

# --- Конфигурация ---
# Имя вашего образа и контейнера. Можете изменить, если нужно.
IMAGE_NAME="bb-schedule-app"
CONTAINER_NAME="bb-schedule-bot"

# --- Логика скрипта ---

# Устанавливаем цвет для сообщений
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. Проверка на наличие .env файла
if [ ! -f .env ]; then
    echo -e "${YELLOW}Файл .env не найден! Пожалуйста, создайте его из примера .env.example и заполните своими данными.${NC}"
    exit 1
fi

# 2. Остановка и удаление старого контейнера, если он существует
# Это нужно, чтобы избежать конфликтов имен.
echo "Проверяем наличие старого контейнера..."
if [ $(podman ps -a -q -f name=^/${CONTAINER_NAME}$) ]; then
    echo "Останавливаем и удаляем старый контейнер ($CONTAINER_NAME)..."
    podman stop $CONTAINER_NAME
    podman rm $CONTAINER_NAME
fi

# 3. Сборка нового образа
# Ключ -t задает имя (тег) образа.
echo -e "\n${GREEN}Собираем новый образ '$IMAGE_NAME'...${NC}"
podman build -t $IMAGE_NAME .

# Проверяем, успешно ли собрался образ
if [ $? -ne 0 ]; then
    echo -e "\n${YELLOW}Ошибка при сборке образа. Запуск прерван.${NC}"
    exit 1
fi

# 4. Запуск нового контейнера
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