import os
import sys

# Make sure the project root is importable no matter where the bot is started
# from (systemd, cron, docker, ...).
_BOT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _BOT_ROOT not in sys.path:
    sys.path.insert(0, _BOT_ROOT)

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

print("[*] Starting Villain Ads Bot v4.1...", flush=True)
import logging
import asyncio
import nest_asyncio
from telethon import TelegramClient
from villain_ads_bot import config
from villain_ads_bot.data import database
from villain_ads_bot import handlers
from villain_ads_bot.services import userbot_manager
from villain_ads_bot.core import error_reporter
from villain_ads_bot.core import music as _music

# Apply the music gateway credentials that were configured in the admin panel
try:
    _music.apply_configured_credentials()
except Exception:
    pass

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

# Forward every ERROR record to the Telegram LOG GROUP and to the OWNER.
# End users never see raw tracebacks any more.
error_reporter.setup_error_reporter()
error_reporter.setup_excepthook()


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
            
            # 2. Disk Space Cleanup: delete old media from the downloads and
            #    the cache folder. The cleanup RECURSES into sub folders (the
            #    old version only looked at the top level, so the extracted
            #    session zips were never removed) and it never deletes a file
            #    that is currently being streamed.
            now = time.time()
            from villain_ads_bot.core import utils as _core_utils
            for folder in (config.DOWNLOADS_DIR, os.path.join(config.PROJECT_ROOT, "cache")):
                if not os.path.isdir(folder):
                    continue
                for dirpath, _dirnames, filenames in os.walk(folder):
                    for fname in filenames:
                        fpath = os.path.join(dirpath, fname)
                        if not os.path.isfile(fpath):
                            continue
                        if fname.lower().endswith((".ttf", ".otf")):
                            continue
                        # Never delete what is being played right now.
                        if _core_utils.is_media_active(fpath):
                            continue
                        try:
                            if os.stat(fpath).st_mtime < now - 3600:
                                os.remove(fpath)
                                logger.info(f"Auto-cleaned old media: {fpath}")
                        except Exception:
                            pass
                    # Remove the (now empty) sub folders.
                    try:
                        if dirpath != folder and not os.listdir(dirpath):
                            os.rmdir(dirpath)
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"Cleanup loop error: {e}")
            
        await asyncio.sleep(300)  # Run every 5 minutes

def _token_bot_id(token: str):
    """Returns the user id of the bot the token belongs to (None on error)."""
    try:
        import json
        import urllib.request
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=10) as r:
            data = json.loads(r.read().decode())
        if data.get("ok"):
            return data["result"].get("id")
    except Exception:
        pass
    return None


def _is_duplicate_session_error(exc) -> bool:
    """True when the session was used from two IPs and is dead for good."""
    if exc is None:
        return False
    name = type(exc).__name__
    if "AuthKeyDuplicated" in name:
        return True
    text = str(exc).lower()
    return "used under two different ip addresses" in text


def _drop_bot_session():
    """Removes the stored bot session (file + MongoDB copy)."""
    import os
    for name in ("bot_session.session", "bot_session.session-journal", "bot_session.session-wal",
                 "bot_session.session-shm"):
        try:
            if os.path.exists(name):
                os.remove(name)
        except Exception:
            pass
    try:
        database._db.settings.update_one(
            {"id": "global"},
            {"$unset": {"bot_session_str": ""}},
        )
    except Exception:
        pass
    print("[!] The old session belonged to a different bot - it was removed.", flush=True)


async def main():
    check_single_instance()
    
    # 0. Validate Essential Credentials
    if not config.BOT_TOKEN:
        print("\n[X] FATAL: BOT_TOKEN is missing or empty in .env!", flush=True)
        print("    Please set BOT_TOKEN in your .env file or environment variables.\n", flush=True)
        sys.exit(1)
        
    if not config.API_ID or not config.API_HASH:
        print("\n[X] FATAL: API_ID or API_HASH is missing or empty in .env!", flush=True)
        print(f"    API_ID: {config.API_ID}, API_HASH: {'SET' if config.API_HASH else 'EMPTY'}\n", flush=True)
        sys.exit(1)

    print("[1/5] [>] Connecting to Database (MongoDB)...", flush=True)
    database.db_init()
    print("[2/5] [+] Database Connected Successfully!", flush=True)
    
    # Initialize Bot Client with Obfuscated TCP to bypass cloud/container firewalls (Deepnote/Docker/VPS)
    print("[3/5] [>] Configuring Telegram Bot Client...", flush=True)
    from telethon.network.connection import ConnectionTcpObfuscated
    from telethon.sessions import StringSession, SQLiteSession
    from telethon import errors
    
    session_file = "bot_session.session"
    
    # 1. Fetch authorized session from MongoDB if available
    mongo_session_str = None
    try:
        doc = database._db.settings.find_one({"id": "global"})
        if doc and doc.get("bot_session_str"):
            mongo_session_str = doc.get("bot_session_str").strip()
    except Exception as e:
        logger.warning(f"Failed to query bot session from MongoDB: {e}")

    # Clean up stale session file if we have a valid cloud session string
    if mongo_session_str and os.path.exists(session_file):
        try:
            os.remove(session_file)
        except Exception:
            pass

    session_target = "bot_session"
    if mongo_session_str:
        print("[+] Loaded verified Telegram bot session from database cache!", flush=True)
        session_target = StringSession(mongo_session_str)

    # Endpoints to try (DC 5 Port 443 -> DC 5 Port 80 -> DC 5 Backup IP)
    ENDPOINTS = [
        ("91.108.56.163", 443, "DC 5 Port 443"),
        ("91.108.56.163", 80, "DC 5 Port 80 (HTTP Bypass)"),
        ("91.108.56.186", 80, "DC 5 Backup Port 80 (HTTP Bypass)"),
        ("91.108.56.186", 443, "DC 5 Backup Port 443"),
    ]

    bot = None
    me = None
    
    print(f"[4/5] [>] Authenticating Bot Client (Token: {config.BOT_TOKEN[:8]}...)...", flush=True)

    for ip, port, desc in ENDPOINTS:
        print(f" [>] Connecting to Telegram via {desc} ({ip}:{port})...", flush=True)
        if mongo_session_str:
            target = StringSession(mongo_session_str)
            target.set_dc(5, ip, port)
        else:
            target = "bot_session"
            
        client = TelegramClient(
            target,
            config.API_ID,
            config.API_HASH,
            connection=ConnectionTcpObfuscated,
            connection_retries=2,
            retry_delay=1,
            timeout=5,
            auto_reconnect=True
        )
        if not mongo_session_str:
            try:
                client.session.set_dc(5, ip, port)
            except Exception:
                pass
                
        try:
            await asyncio.wait_for(client.connect(), timeout=7)
            if client.is_connected():
                me = await asyncio.wait_for(client.get_me(), timeout=5)
                if me:
                    print(f" [+] Connected successfully via {desc}!", flush=True)
                    bot = client
                    break
        except Exception as conn_err:
            if _is_duplicate_session_error(conn_err):
                # The session is dead - drop it and authorize with the token.
                print(" [!] The session was used from two IPs and can not be used "
                      "again. It will be replaced by a fresh authorization.", flush=True)
                _drop_bot_session()
                mongo_session_str = None
                try:
                    await client.disconnect()
                except Exception:
                    pass
                break
            print(f" [!] {desc} timed out ({type(conn_err).__name__}). Switching...", flush=True)
            try:
                await client.disconnect()
            except Exception:
                pass

    if not bot or not me:
        print(" [!] Direct DC 5 endpoints exhausted. Attempting standard Telethon connect...", flush=True)
        bot = TelegramClient(
            session_target,
            config.API_ID,
            config.API_HASH,
            connection=ConnectionTcpObfuscated,
            connection_retries=5,
            retry_delay=2,
            timeout=10,
            auto_reconnect=True
        )
        try:
            await bot.connect()
            me = await bot.get_me()
        except Exception as fallback_err:
            if _is_duplicate_session_error(fallback_err):
                print(" [!] The stored session is dead - authorizing again with the "
                      "token.", flush=True)
                _drop_bot_session()
                bot = TelegramClient(
                    "bot_session",
                    config.API_ID,
                    config.API_HASH,
                    connection=ConnectionTcpObfuscated,
                    connection_retries=5,
                    retry_delay=2,
                    timeout=10,
                    auto_reconnect=True,
                )
                await bot.connect()
                me = await bot.get_me()
            else:
                raise

    # 1.5 The session must belong to THIS bot. A swapped token invalidates
    #     the old session - drop it and authorize again.
    if me is not None:
        expected_bot_id = _token_bot_id(config.BOT_TOKEN)
        if expected_bot_id and getattr(me, "id", None) != expected_bot_id:
            print(f"[!] The session belongs to bot {me.id} but the token is bot "
                  f"{expected_bot_id}.", flush=True)
            _drop_bot_session()
            try:
                await bot.disconnect()
            except Exception:
                pass
            me = None
            mongo_session_str = None
            bot = TelegramClient(
                "bot_session",
                config.API_ID,
                config.API_HASH,
                connection=ConnectionTcpObfuscated,
                connection_retries=5,
                retry_delay=2,
                timeout=10,
                auto_reconnect=True,
            )
            try:
                await asyncio.wait_for(bot.connect(), timeout=15)
                me = await asyncio.wait_for(bot.get_me(), timeout=10)
            except Exception as conn_err:
                print(f" [!] Reconnect failed: {conn_err}", flush=True)

    if me is None:
        print("[*] No active session found. Authorizing bot token...", flush=True)
        try:
            await bot.sign_in(bot_token=config.BOT_TOKEN)
            me = await bot.get_me()
        except errors.FloodWaitError as fwe:
            print(f"\n[X] TELEGRAM RATE LIMIT: Telegram requires waiting {fwe.seconds}s before authorizing this bot token.", flush=True)
            print("    Please wait or do not delete session files repeatedly.\n", flush=True)
            raise fwe

    # Register all command and callback handlers on the active connected bot
    print("[5/5] [>] Registering Event Handlers...", flush=True)
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

    # Bind the bot client to the error reporter so errors reach the log group + owner
    try:
        error_reporter.set_bot_client(bot)
        error_reporter.setup_asyncio_exception_handler(asyncio.get_running_loop())
    except Exception as er_err:
        logger.warning(f"Could not initialise error reporter bindings: {er_err}")

    # Persist the authorized session to MongoDB so that any future restart/deployment is instant
    try:
        current_str = StringSession.save(bot.session)
        if current_str:
            database._db.settings.update_one(
                {"id": "global"},
                {"$set": {"bot_session_str": current_str}},
                upsert=True
            )
            logger.info("Persisted authorized Telegram bot session to MongoDB.")
    except Exception as save_err:
        logger.debug(f"Could not persist session string to DB: {save_err}")

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
    if os.getenv("SKIP_AUTOSTART", "").strip().lower() in ("1", "true", "yes"):
        print("[*] SKIP_AUTOSTART is set - the userbots are NOT resumed.", flush=True)
    else:
        print("[*] Resuming active userbots in background...", flush=True)
        t1 = asyncio.create_task(userbot_manager.start_all_running_bots())
        background_tasks.add(t1)
        t1.add_done_callback(background_tasks.discard)
    
    # Start VPS Optimization (RAM/Space Cleanup) loop (self-healing)
    t3 = asyncio.create_task(resilient_task(cleanup_loop, "cleanup_loop"))
    background_tasks.add(t3)
    t3.add_done_callback(background_tasks.discard)
    
    # Auto mirror the proxy drop channel into the owner's own channel.
    # The drop channel posts every 30 minutes, so every 10 minutes is enough.
    try:
        from villain_ads_bot.services import proxy_mirror
        t4 = asyncio.create_task(proxy_mirror.auto_mirror_loop(bot, interval=600))
        background_tasks.add(t4)
        t4.add_done_callback(background_tasks.discard)
    except Exception as mirror_err:
        logger.warning(f"Could not start the proxy mirror: {mirror_err}")

    # Optional lightweight HTTP health check server for cloud platforms (Render, Railway, Koyeb, Heroku)
    # When deployed as a Web Service, platforms require listening on $PORT.
    http_runner = None
    port_env = os.getenv("PORT")
    if port_env:
        try:
            from aiohttp import web
            async def _health_handler(request):
                return web.Response(text="Villain Ads Bot is running!", status=200)

            http_app = web.Application()
            http_app.router.add_get("/", _health_handler)
            http_app.router.add_get("/health", _health_handler)
            http_runner = web.AppRunner(http_app)
            await http_runner.setup()
            http_site = web.TCPSite(http_runner, "0.0.0.0", int(port_env))
            await http_site.start()
            print(f"[*] Health check HTTP server listening on port {port_env}", flush=True)
        except Exception as http_err:
            logger.warning(f"Failed to start HTTP health server on port {port_env}: {http_err}")

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
        # Stop HTTP server if running
        if http_runner:
            try:
                await http_runner.cleanup()
            except Exception:
                pass

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
