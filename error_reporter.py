import os
import sys
import time
import asyncio
import logging
import traceback
import html
from typing import Optional

ERROR_FILE_PATH = "error.txt"
MAX_FILE_BYTES = 3 * 1024 * 1024  # 3 MB limit for error.txt
TRIM_FILE_BYTES = 1 * 1024 * 1024  # Trim down to 1 MB when limit exceeded

_bot_client = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_is_sending = False
_pending_task: Optional[asyncio.Task] = None
_last_sent_time = 0.0
_MIN_SEND_INTERVAL = 20.0  # At least 20s between file uploads to avoid Telegram FloodWait
_DEBOUNCE_DELAY = 3.0     # Wait 3s after error burst before uploading error.txt

_latest_source = "System"
_latest_error_summary = "An unexpected error occurred."
_has_unsent_error = False


def _append_to_error_file(source: str, level: str, message: str, exc_str: str = ""):
    """Appends formatted error details to error.txt, with automatic file size management."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    entry = [
        "=" * 80,
        f"TIMESTAMP : {timestamp}",
        f"MODULE    : {source}",
        f"LEVEL     : {level}",
        f"MESSAGE   : {message}",
    ]
    if exc_str and exc_str.strip():
        entry.append(f"TRACEBACK :\n{exc_str.strip()}")
    entry.append("=" * 80 + "\n\n")
    
    text_to_append = "\n".join(entry)

    try:
        # Check size before appending
        if os.path.exists(ERROR_FILE_PATH):
            file_size = os.path.getsize(ERROR_FILE_PATH)
            if file_size > MAX_FILE_BYTES:
                try:
                    with open(ERROR_FILE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(file_size - TRIM_FILE_BYTES)
                        # Read to the next line to avoid partial line truncation
                        f.readline()
                        remaining = f.read()
                    with open(ERROR_FILE_PATH, "w", encoding="utf-8") as f:
                        f.write("[... Older errors truncated to maintain optimal file size ...]\n\n")
                        f.write(remaining)
                except Exception:
                    pass

        with open(ERROR_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(text_to_append)
    except Exception as e:
        sys.stderr.write(f"[ERROR_REPORTER_WRITE_FAILED] {e}\n")


async def _send_error_report_async():
    """Async worker that uploads error.txt to the Telegram log group with debouncing and rate limiting."""
    global _is_sending, _last_sent_time, _has_unsent_error, _pending_task

    await asyncio.sleep(_DEBOUNCE_DELAY)

    if _is_sending:
        return

    _is_sending = True
    try:
        # Rate limit cooldown check
        now = time.time()
        elapsed = now - _last_sent_time
        if elapsed < _MIN_SEND_INTERVAL:
            await asyncio.sleep(_MIN_SEND_INTERVAL - elapsed)

        if not _bot_client:
            _has_unsent_error = True
            return

        # Check if bot is connected
        if hasattr(_bot_client, "is_connected") and not _bot_client.is_connected():
            _has_unsent_error = True
            return

        # Get log_group_id from database or config
        log_group_id = None
        try:
            import database
            gs = database.get_global_settings()
            log_group_id = gs.get("log_group_id")
        except Exception:
            pass

        if not log_group_id:
            try:
                import config
                log_group_id = config.DEFAULT_GLOBAL_SETTINGS.get("log_group_id")
            except Exception:
                pass

        if not log_group_id:
            return

        if not os.path.exists(ERROR_FILE_PATH) or os.path.getsize(ERROR_FILE_PATH) == 0:
            return

        # Prepare clean HTML caption
        escaped_source = html.escape(_latest_source[:64])
        escaped_err = html.escape(_latest_error_summary[:300])
        caption = (
            "🚨 <b>BOT ERROR ALERT</b> 🚨\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 <b>Module:</b> <code>{escaped_source}</code>\n"
            f"❌ <b>Error:</b> <code>{escaped_err}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📁 <b>Full traceback & error logs attached in error.txt</b>"
        )

        try:
            await _bot_client.send_file(
                log_group_id,
                ERROR_FILE_PATH,
                caption=caption,
                parse_mode="html"
            )
            _last_sent_time = time.time()
            _has_unsent_error = False
        except Exception as send_err:
            sys.stderr.write(f"[ERROR_REPORTER_SEND_FAILED] {send_err}\n")
            _has_unsent_error = True
    finally:
        _is_sending = False
        _pending_task = None


def trigger_error_report(source: str, summary: str):
    """Schedules the debounced upload of error.txt to the log group."""
    global _latest_source, _latest_error_summary, _has_unsent_error, _pending_task, _loop

    _latest_source = source or "Unknown"
    _latest_error_summary = summary or "Error"
    _has_unsent_error = True

    target_loop = _loop
    if not target_loop or target_loop.is_closed():
        try:
            target_loop = asyncio.get_running_loop()
            _loop = target_loop
        except RuntimeError:
            try:
                target_loop = asyncio.get_event_loop()
                _loop = target_loop
            except RuntimeError:
                target_loop = None

    if target_loop and target_loop.is_running():
        try:
            curr_loop = asyncio.get_running_loop()
        except RuntimeError:
            curr_loop = None

        if curr_loop == target_loop:
            if _pending_task is None or _pending_task.done():
                _pending_task = target_loop.create_task(_send_error_report_async())
        else:
            asyncio.run_coroutine_threadsafe(_send_error_report_async(), target_loop)


class TelegramErrorLogHandler(logging.Handler):
    """Custom logging handler that catches all ERROR and CRITICAL logs."""
    def __init__(self):
        super().__init__(level=logging.ERROR)

    def emit(self, record: logging.LogRecord):
        # Prevent recursion if an error occurs inside the error reporter
        if record.name == __name__ or _is_sending:
            return

        try:
            msg = self.format(record)
            exc_str = ""
            if record.exc_info:
                exc_str = "".join(traceback.format_exception(*record.exc_info))

            _append_to_error_file(
                source=record.name,
                level=record.levelname,
                message=str(record.getMessage()),
                exc_str=exc_str
            )

            trigger_error_report(
                source=record.name,
                summary=str(record.getMessage())
            )
        except Exception as e:
            sys.stderr.write(f"[ERROR_HANDLER_EMIT_FAILED] {e}\n")


def setup_error_reporter():
    """Initializes the Telegram error capture system on the root logger and system hooks."""
    root_logger = logging.getLogger()
    
    # Avoid duplicate handlers
    already_installed = any(isinstance(h, TelegramErrorLogHandler) for h in root_logger.handlers)
    if not already_installed:
        handler = TelegramErrorLogHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    # Global uncaught exception hook
    def uncaught_exception_hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        _append_to_error_file(
            source="UncaughtException",
            level="CRITICAL",
            message=f"{exc_type.__name__}: {exc_value}",
            exc_str=tb_str
        )
        trigger_error_report(
            source="UncaughtException",
            summary=f"{exc_type.__name__}: {exc_value}"
        )
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = uncaught_exception_hook


def setup_asyncio_exception_handler(loop: asyncio.AbstractEventLoop):
    """Sets an unhandled exception handler on the asyncio event loop."""
    global _loop
    _loop = loop

    def loop_exception_handler(curr_loop, context):
        msg = context.get("message", "Unhandled asyncio loop exception")
        exc = context.get("exception")
        exc_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)) if exc else ""
        source = context.get("future") or context.get("task") or "AsyncioLoop"
        source_name = str(source)

        _append_to_error_file(
            source=source_name,
            level="ERROR",
            message=msg,
            exc_str=exc_str
        )
        trigger_error_report(
            source=source_name,
            summary=f"{msg} ({exc})" if exc else msg
        )
        # Call default handler so standard console logging still works
        curr_loop.default_exception_handler(context)

    loop.set_exception_handler(loop_exception_handler)


def set_bot_client(bot):
    """Sets the TelegramClient instance and flushes any pending error reports."""
    global _bot_client, _loop
    _bot_client = bot
    if hasattr(bot, "loop") and bot.loop:
        _loop = bot.loop
    if _has_unsent_error:
        trigger_error_report(_latest_source, _latest_error_summary)
