import asyncio
from app.services.schedule_sync import run_full_sync
from app.core.database import initialize_database, get_last_two_job_logs

async def main():
    await initialize_database()
    print("DB Initialized")
    success = await run_full_sync()
    print(f"Sync success: {success}")
    logs = await get_last_two_job_logs("schedule_sync")
    print("Logs from DB:")
    for log in logs:
        print(log)

if __name__ == "__main__":
    asyncio.run(main())
