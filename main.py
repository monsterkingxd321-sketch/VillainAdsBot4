import sys
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

print("[*] Starting Villain Ads Bot v4.0...", flush=True)
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
os.makedirs(config.USER_DATA_DIR, exist_ok=True)

# Configure console logging (NO file logging to save VPS disk space)
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
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
        # Try to detect if the previous PID is still alive and running main.py
        try:
            with open(LOCK_FILE, 'r') as f:
                content = f.read().strip()
                old_pid = int(content) if content else 0
            
            if old_pid > 0 and old_pid != os.getpid():
                import psutil
                if psutil.pid_exists(old_pid):
                    is_our_bot = False
                    try:
                        p = psutil.Process(old_pid)
                        cmdline = " ".join(p.cmdline()).lower()
                        if "main.py" in cmdline:
                            is_our_bot = True
                    except Exception:
                        is_our_bot = False

                    if is_our_bot:
                        logger.error(f"Another instance of the bot is already running! (PID: {old_pid})")
                        print("\n[X] ERROR: Another instance of the bot is already running!")
                        print(f"Running PID: {old_pid}. To kill it run: kill -9 {old_pid}\n")
                        sys.exit(1)
                    else:
                        logger.warning(f"Removed stale bot.lock (PID {old_pid} is not main.py).")
                        try:
                            os.remove(LOCK_FILE)
                        except OSError:
                            pass
                else:
                    logger.warning(f"Removed stale bot.lock from inactive PID {old_pid}.")
                    try:
                        os.remove(LOCK_FILE)
                    except OSError:
                        pass
            else:
                try:
                    os.remove(LOCK_FILE)
                except OSError:
                    pass
        except (ValueError, ImportError, OSError):
            try:
                os.remove(LOCK_FILE)
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
    """Background task to optimize RAM usage and clean up downloaded media and cache to save VPS space."""
    import time
    import gc
    while True:
        try:
            # 1. Force Python Garbage Collection to reduce RAM usage on VPS
            gc.collect()
            
            # 2. Disk Space Cleanup: Delete files older than 1 hour in 'downloads' and 'cache' folders
            now = time.time()
            for folder in ["downloads", "cache"]:
                if os.path.exists(folder):
                    for fname in os.listdir(folder):
                        fpath = os.path.join(folder, fname)
                        if os.path.isfile(fpath):
                            if os.stat(fpath).st_mtime < now - 3600:
                                try:
                                    os.remove(fpath)
                                    logger.info(f"Auto-cleaned old media: {fpath}")
                                except Exception:
                                    pass
        except Exception as e:
            logger.error(f"Cleanup loop error: {e}")
            
        await asyncio.sleep(300)  # Run every 5 minutes

async def main():
    check_single_instance()
    print("[1/5] [>] Connecting to Database (MongoDB)...", flush=True)
    database.db_init()
    print("[2/5] [+] Database Connected Successfully!", flush=True)
    
    # Initialize Bot Client with resilient connection retries for VPS/Cloud environments
    print("[3/5] [>] Connecting Telegram Bot Manager...", flush=True)
    bot = TelegramClient(
        "bot_session", 
        config.API_ID, 
        config.API_HASH,
        connection_retries=15,
        retry_delay=2,
        timeout=25,
        auto_reconnect=True
    )
    
    # Register all command and callback handlers
    print("[4/5] [>] Registering Event Handlers...", flush=True)
    
    # GLOBAL ANTIVIRUS & ANTI-HACK SHIELD
    from telethon import events
    @bot.on(events.NewMessage)
    async def global_antivirus_shield(event):
        # Prevent receiving malicious executable files (Basic Anti-Hack)
        if event.message and event.message.file:
            ext = event.message.file.ext
            if ext and ext.lower() in [".exe", ".bat", ".vbs", ".sh", ".py", ".cmd", ".dll", ".pif", ".scr", ".js"]:
                logger.warning(f"ANTIVIRUS ALERT: Blocked malicious file {ext} from user {event.sender_id}")
                await event.reply("Security Alert (Anti-Virus): Executable or malicious files are strictly prohibited. Your action has been blocked to protect the bot infrastructure!")
                raise events.StopPropagation
                
    handlers.register_all_handlers(bot)
    
    print("[5/5] [>] Starting Telegram Bot...", flush=True)
    await bot.start(bot_token=config.BOT_TOKEN)
    me = await bot.get_me()
    bot_username = getattr(me, 'username', None) or str(me.id)
    print("=" * 60, flush=True)
    print(f" [+] VILLAIN ADS BOT IS LIVE: @{bot_username} ({me.id})", flush=True)
    print(" [+] Ready & Listening for commands in background!", flush=True)
    print("=" * 60, flush=True)
    
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

    # Resume userbots that were running prior to shutdown in background (one-shot startup task)
    print("[*] Resuming active userbots in background...", flush=True)
    t1 = asyncio.create_task(userbot_manager.start_all_running_bots())
    background_tasks.add(t1)
    t1.add_done_callback(background_tasks.discard)
    
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
            logger.error(f"Unhandled exception in main(): {e}. Restarting in {RESTART_DELAY}s...", exc_info=True)
            time.sleep(RESTART_DELAY)
