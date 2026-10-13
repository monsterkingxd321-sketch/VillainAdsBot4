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
    level=logging.WARNING, # Changed to WARNING to reduce VPS load and terminal clutter
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/userbot_manager.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)
# Keep telethon at WARNING/ERROR to prevent spam
logging.getLogger("telethon").setLevel(logging.ERROR)
logging.getLogger("pytgcalls").setLevel(logging.ERROR)
logging.getLogger("ntgcalls").setLevel(logging.ERROR)

# Apply Windows compatibility patch for nested asyncio loops
nest_asyncio.apply()

LOCK_FILE = "bot.lock"
_lock_fp = None

def check_single_instance():
    """Acquire a lock file to prevent duplicate instances.
    Stale lock files (from a crashed run) are silently removed and replaced.
    """
    global _lock_fp
    import sys
    if os.path.exists(LOCK_FILE):
        # Try to detect if the previous PID is still alive
        try:
            with open(LOCK_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            # Check if that PID is still running
            import psutil
            if psutil.pid_exists(old_pid):
                logger.error("Another instance of the bot is already running! (bot.lock is locked)")
                print("\n[X] ERROR: Another instance of the bot is already running!")
                print(f"Running PID: {old_pid}. Kill it and try again.\n")
                sys.exit(1)
            else:
                # Stale lock from a previous crash — remove it
                logger.warning(f"Removed stale bot.lock from crashed PID {old_pid}.")
                os.remove(LOCK_FILE)
        except (ValueError, ImportError, OSError):
            # Can't read PID or psutil not available — just remove the stale lock
            try:
                os.remove(LOCK_FILE)
                logger.warning("Removed stale bot.lock file (could not verify PID).")
            except OSError:
                pass
            
    try:
        _lock_fp = open(LOCK_FILE, "w")
        _lock_fp.write(str(os.getpid()))
        _lock_fp.flush()
    except IOError:
        logger.error("Failed to acquire lock. Another instance might be running.")
        print("\n[X] ERROR: Failed to acquire lock. Another instance might be running.\n")
        sys.exit(1)

async def cleanup_loop():
    """Background task to optimize RAM usage and clean up old downloaded media to save VPS space."""
    import time
    import gc
    while True:
        try:
            # 1. Force Python Garbage Collection to reduce RAM usage on VPS
            gc.collect()
            
            # 2. Disk Space Cleanup: Delete files older than 2 hours in 'downloads' folder
            downloads_dir = "downloads"
            if os.path.exists(downloads_dir):
                now = time.time()
                for fname in os.listdir(downloads_dir):
                    fpath = os.path.join(downloads_dir, fname)
                    if os.path.isfile(fpath):
                        if os.stat(fpath).st_mtime < now - 7200:
                            try:
                                os.remove(fpath)
                                logger.info(f"Optimized VPS Space: Deleted old cached media {fname}")
                            except Exception:
                                pass
        except Exception as e:
            logger.error(f"Cleanup loop error: {e}")
            
        await asyncio.sleep(600)  # Run every 10 minutes

async def main():
    check_single_instance()
    logger.info("Initializing database connection...")
    database.db_init()
    
    # Initialize Bot Client
    logger.info("Initializing Bot client...")
    bot = TelegramClient("bot_session", config.API_ID, config.API_HASH)
    
    # Register all command and callback handlers
    logger.info("Registering event handlers...")
    
    # 🛡️ GLOBAL ANTIVIRUS & ANTI-HACK SHIELD
    from telethon import events
    @bot.on(events.NewMessage)
    async def global_antivirus_shield(event):
        # Prevent receiving malicious executable files (Basic Anti-Hack)
        if event.message and event.message.file:
            ext = event.message.file.ext
            if ext and ext.lower() in [".exe", ".bat", ".vbs", ".sh", ".py", ".cmd", ".dll", ".pif", ".scr", ".js"]:
                logger.warning(f"🛡️ ANTIVIRUS ALERT: Blocked malicious file {ext} from user {event.sender_id}")
                await event.reply("🚫 **Security Alert (Anti-Virus):** Executable or malicious files are strictly prohibited. Your action has been blocked to protect the bot infrastructure!")
                raise events.StopPropagation
                
    handlers.register_all_handlers(bot)
    
    logger.info("Starting bot manager...")
    await bot.start(bot_token=config.BOT_TOKEN)
    logger.info("Telegram Bot Manager is running successfully.")
    
    # Track tasks to prevent garbage collection
    background_tasks = set()

    async def resilient_task(coro_func, name, *args, restart_delay=10):
        """Wraps a background coroutine so it auto-restarts if it crashes."""
        while True:
            try:
                await coro_func(*args)
            except asyncio.CancelledError:
                logger.info(f"Background task '{name}' was cancelled.")
                break
            except Exception as e:
                logger.error(f"Background task '{name}' crashed: {e}. Restarting in {restart_delay}s...")
                await asyncio.sleep(restart_delay)

    from handlers.payments_extended import start_gmail_polling

    # Resume userbots that were running prior to shutdown in background (one-shot startup task)
    t1 = asyncio.create_task(userbot_manager.start_all_running_bots())
    background_tasks.add(t1)
    t1.add_done_callback(background_tasks.discard)
    
    # Start Gmail autopay approval check loop in background (self-healing)
    t2 = asyncio.create_task(resilient_task(start_gmail_polling, "gmail_polling", bot))
    background_tasks.add(t2)
    t2.add_done_callback(background_tasks.discard)
    
    # Start VPS Optimization (RAM/Space Cleanup) loop (self-healing)
    t3 = asyncio.create_task(resilient_task(cleanup_loop, "cleanup_loop"))
    background_tasks.add(t3)
    t3.add_done_callback(background_tasks.discard)
    
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
    import time
    RESTART_DELAY = 10  # seconds before auto-restarting after crash
    while True:
        try:
            asyncio.run(main())
            # If main() returns normally (e.g. disconnected without exception), restart
            logger.warning("main() exited unexpectedly. Restarting in %ds...", RESTART_DELAY)
            time.sleep(RESTART_DELAY)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Bot Manager stopped gracefully.")
            break
        except Exception as e:
            logger.error(f"Unhandled exception in main(): {e}. Restarting in {RESTART_DELAY}s...")
            time.sleep(RESTART_DELAY)
