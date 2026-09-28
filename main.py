import os
import logging
import asyncio
import nest_asyncio
from telethon import TelegramClient
import config
import database
import handlers
import userbot_manager

# Create directories if they do not exist
os.makedirs("logs", exist_ok=True)
os.makedirs(config.USER_DATA_DIR, exist_ok=True)

# Configure logging to console and a log file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/userbot_manager.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

# Apply Windows compatibility patch for nested asyncio loops
nest_asyncio.apply()

LOCK_FILE = "bot.lock"
_lock_fp = None

def check_single_instance():
    global _lock_fp
    import sys
    if os.path.exists(LOCK_FILE):
        try:
            os.remove(LOCK_FILE)
        except OSError:
            logger.error("Another instance of the bot is already running! (bot.lock is locked)")
            print("\n[X] ERROR: Another instance of the bot is already running!")
            print("Please kill the running Python processes and try again.\n")
            sys.exit(1)
            
    try:
        _lock_fp = open(LOCK_FILE, "w")
        _lock_fp.write(str(os.getpid()))
        _lock_fp.flush()
    except IOError:
        logger.error("Failed to acquire lock. Another instance might be running.")
        print("\n[X] ERROR: Failed to acquire lock. Another instance might be running.\n")
        sys.exit(1)

async def main():
    check_single_instance()
    logger.info("Initializing database connection...")
    database.db_init()
    
    # Initialize Bot Client
    logger.info("Initializing Bot client...")
    bot = TelegramClient("bot_session", config.API_ID, config.API_HASH)
    
    # Register all command and callback handlers
    logger.info("Registering event handlers...")
    handlers.register_all_handlers(bot)
    
    logger.info("Starting bot manager...")
    await bot.start(bot_token=config.BOT_TOKEN)
    logger.info("Telegram Bot Manager is running successfully.")
    
    # Track tasks to prevent garbage collection
    background_tasks = set()

    # Resume userbots that were running prior to shutdown in background
    t1 = asyncio.create_task(userbot_manager.start_all_running_bots())
    background_tasks.add(t1)
    t1.add_done_callback(background_tasks.discard)
    
    # Start Gmail autopay approval check loop in background
    from handlers.payments_extended import start_gmail_polling
    t2 = asyncio.create_task(start_gmail_polling(bot))
    background_tasks.add(t2)
    t2.add_done_callback(background_tasks.discard)
    
    try:
        # Keep the bot running and auto-reconnect if it disconnects
        while True:
            try:
                if not bot.is_connected():
                    await bot.connect()
                await bot.run_until_disconnected()
            except Exception as e:
                logger.error(f"Bot disconnected due to error: {e}. Retrying in 5 seconds...")
                await asyncio.sleep(5)
    finally:
        # Stop all background userbots gracefully
        await userbot_manager.stop_all_bots()
        
        # Release and clean up lock file
        global _lock_fp
        if _lock_fp:
            _lock_fp.close()
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot Manager stopped gracefully.")
