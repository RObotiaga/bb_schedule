import argparse
import asyncio
import logging
import sys
import uvicorn
from app.core.logger import setup_logging

def main():
    setup_logging()
    parser = argparse.ArgumentParser(description="USURT Schedule App")
    parser.add_argument("mode", choices=["bot", "web"], default="bot", nargs="?", help="Mode to run: 'bot' or 'web'")
    args = parser.parse_args()

    if args.mode == "bot":
        from app.bot.main import start_bot
        try:
            asyncio.run(start_bot())
        except (KeyboardInterrupt, SystemExit):
            logging.info("Bot stopped.")
    elif args.mode == "web":
        logging.info("Starting Web App...")
        # Reload is useful for dev, but in docker we might want it off.
        # We can use env var for reload.
        uvicorn.run("app.web.app:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    sys.path.append(".") # Ensure current dir is in path
    main()
