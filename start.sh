podman run -d \
  --name my-bb-bot \
  -v /root/db/bb_bot:/app:z \
  --env-file .env \
  bb-schedule-bot