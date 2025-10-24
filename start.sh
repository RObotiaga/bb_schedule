podman run -d \
  --name my-bb-bot \
  -v /root/db/bb_bot:/app:z \
  --env-file .env \
  bb-schedule-bot

echo -e "\n$Готово! Контейнер запущен в фоновом режиме."
echo "Чтобы посмотреть логи, используйте команду: podman logs -f my-bb-bot"
echo "Чтобы остановить контейнер, используйте команду: podman stop my-bb-bot"