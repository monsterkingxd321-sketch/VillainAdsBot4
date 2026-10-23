"""
Centralised error reporting for the Villain Ads Bot.

Owner requirements implemented here:
  * Every unhandled / handled error is forwarded to the Telegram **LOG GROUP**
    and to the **OWNER** (admins). The end user NEVER receives a raw
    traceback or internal exception text any more.
  * Reports are buffered and flushed in batches so a burst of errors can
    never trigger a Telegram flood-wait.
  * The reporter is completely fail-safe: if reporting itself fails the
    error is written to stdout only and never loops back into the buffer.
"""

import asyncio
import logging
import os
import sys
import time
import traceback
from collections import deque
from datetime import datetime

try:
    from villain_ads_bot import config
except Exception:  # pragma: no cover - config must always exist in practice
    config = None

logger = logging.getLogger("error_reporter")

# --------------------------------------------------------------------------
# Global state
# --------------------------------------------------------------------------
_bot_client = None
_buffer = deque(maxlen=50)
_all_error_logs = deque(maxlen=2000)  # Stores all error events since startup
_flush_lock = None
_flush_task = None
_recent_notified = deque(maxlen=200)   # dedupe identical error bursts
_last_flush_at = 0.0
_reports_this_hour = [0, 0.0]         # [count, window_start_timestamp]

FLUSH_INTERVAL = 12.0        # seconds between flushes
MAX_CHUNK_SIZE = 3800       # Telegram message limit safety margin
MAX_RECORDS_PER_FLUSH = 8   # don't flood the log group
MAX_REPORTS_PER_HOUR = 120  # hard cap on how many reports are delivered per hour

# Generic messages that are safe to show to an end user.
GENERIC_ERROR = "<blockquote><b>» ❌ Something Went Wrong</b>\n\n⚠️ <i>An unexpected error occurred. The issue has been reported to the admin team and will be fixed shortly.</i></blockquote>"
GENERIC_ACTION_ERROR = "<blockquote><b>» ❌ Action Failed</b>\n\n⚠️ <i>Something went wrong while performing this action. The issue has been reported to the admin team.</i></blockquote>"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def set_bot_client(client):
    """Register the manager bot client used to deliver reports."""
    global _bot_client
    _bot_client = client
    logger.info("Error reporter bound to bot client.")


def get_bot_client():
    return _bot_client


def get_log_group_id():
    """Resolves the log group id (MongoDB global settings -> config fallback)."""
    lgid = None
    try:
        from villain_ads_bot.data import database
        lgid = database.get_global_settings().get("log_group_id")
    except Exception:
        lgid = None
    if not lgid:
        lgid = getattr(config, "LOG_GROUP_ID", None) if config else None
    if not lgid:
        try:
            lgid = int(os.getenv("LOG_GROUP_ID", "0")) or None
        except Exception:
            lgid = None
    try:
        lgid = int(lgid)
    except (TypeError, ValueError):
        return None
    return lgid if lgid else None


def get_owner_ids():
    """Returns the list of owner/admin user ids that should receive reports."""
    owners = set()
    try:
        owners |= set(config.ORIGINAL_ADMIN_IDS or [])
    except Exception:
        pass
    try:
        from villain_ads_bot.data import database
        owners |= set(database.get_global_settings().get("admins", []) or [])
    except Exception:
        pass
    return [o for o in owners if o]


def _format_record(record) -> str:
    """Turns a logging record into a readable Telegram-friendly block."""
    try:
        message = record.getMessage()
    except Exception:
        message = str(record.msg)
    exc_text = ""
    if record.exc_info:
        try:
            exc_text = "".join(traceback.format_exception(*record.exc_info))
        except Exception:
            exc_text = ""
    stamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
    head = f"⚠️ <b>{record.levelname}</b> in <code>{record.name}</code>\n🕒 <code>{stamp}</code>"
    body = message
    if len(body) > 1500:
        body = body[:1500] + "…"
    out = f"{head}\n\n<pre>{_escape(body)}</pre>"
    if exc_text:
        if len(exc_text) > 1800:
            exc_text = exc_text[:1800] + "\n…"
        out += f"\n<b>Traceback :</b>\n<pre>{_escape(exc_text)}</pre>"
    return out


def _escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _chunks(text: str, size: int = MAX_CHUNK_SIZE):
    for i in range(0, len(text), size):
        yield text[i:i + size]


# --------------------------------------------------------------------------
# Buffering
# --------------------------------------------------------------------------
def add_error(context: str, error: BaseException = None, tb=None, level: str = "ERROR"):
    """
    Buffers an error for delivery to the log group + owner.
    Safe to call from anywhere, including inside except blocks.
    """
    try:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if error is not None:
            detail = f"{type(error).__name__}: {error}"
            if tb:
                try:
                    detail += "\n" + "".join(traceback.format_exception(type(error), error, tb))
                except Exception:
                    pass
        else:
            detail = str(context)

        item_record = {
            "context": context,
            "detail": detail,
            "stamp": stamp,
            "level": level,
        }
        _all_error_logs.append(item_record)

        # Dedupe identical bursts (same context + same message within 60s)
        signature = f"{context}|{detail[:200]}"
        now = datetime.now().timestamp()
        for sig, ts in list(_recent_notified):
            if sig == signature and (now - ts) < 60:
                return
        _recent_notified.append((signature, now))

        _buffer.append(item_record)
        logger.log(logging.ERROR, "%s | %s", context, detail.split("\n")[0][:500])
    except Exception:
        # Reporting must never raise.
        pass


def get_error_logs_text() -> str:
    """Returns all error logs recorded since the bot started as formatted text."""
    if not _all_error_logs:
        return ""
    lines = []
    lines.append("=" * 70)
    lines.append("VILLAIN ADS BOT - ERROR LOGS (SINCE UPTIME)")
    lines.append(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Total error events recorded: {len(_all_error_logs)}")
    lines.append("=" * 70)
    lines.append("")
    for i, item in enumerate(_all_error_logs, 1):
        lines.append(f"[{i}] [{item.get('stamp', '?')}] [{item.get('level', 'ERROR')}] {item.get('context', 'unknown')}")
        detail = item.get("detail", "")
        lines.append(detail)
        lines.append("-" * 70)
    return "\n".join(lines)


def get_error_logs_count() -> int:
    """Returns the total number of errors captured since startup."""
    return len(_all_error_logs)


def report(context: str, error: BaseException = None, tb=None):
    """
    Public API: report an error to the log group and owner.
    Use this INSTEAD of showing raw exception text to the end user.
    """
    add_error(context, error, tb)
    schedule_flush()


# --------------------------------------------------------------------------
# Flushing
# --------------------------------------------------------------------------
async def _resolve(client, chat_id):
    """
    Resolves a chat id into an entity.

    A freshly created bot has an empty entity cache, so sending with a raw
    id fails with "Could not find the input entity". Resolving it first
    makes the reports work from the very first second.
    """
    try:
        return await client.get_entity(chat_id)
    except Exception:
        try:
            return await client.get_input_entity(chat_id)
        except Exception:
            return chat_id


# A chat that can not be reached is skipped for a while instead of being
# retried every 12 seconds (the console was flooded with it).
_unreachable = {}


def _is_unreachable(chat_id) -> bool:
    try:
        entry = _unreachable.get(str(chat_id))
        if not entry:
            return False
        count, until = entry
        if count >= 3 and time.time() < until:
            return True
        return False
    except Exception:
        return False


def _mark_unreachable(chat_id):
    try:
        key = str(chat_id)
        count, until = _unreachable.get(key, (0, 0))
        if time.time() >= until:
            count = 0
        count += 1
        _unreachable[key] = (count, time.time() + 3600)
        return count
    except Exception:
        return 0


def _reset_unreachable(chat_id):
    try:
        _unreachable.pop(str(chat_id), None)
    except Exception:
        pass


async def _send_to_log_group(text: str):
    client = _bot_client
    lgid = get_log_group_id()
    if not client or not lgid:
        return
    if _is_unreachable(lgid):
        return
    try:
        entity = await _resolve(client, lgid)
        await client.send_message(entity, text, parse_mode="html")
        _reset_unreachable(lgid)
    except Exception as e:
        count = _mark_unreachable(lgid)
        if count == 1:
            print(f"[error_reporter] The log group is not reachable ({e}). "
                  f"Add the bot to the log group - the reports are skipped for an hour.",
                  flush=True)


async def _send_to_owner(text: str):
    client = _bot_client
    if not client:
        return
    for owner_id in get_owner_ids():
        if _is_unreachable(owner_id):
            continue
        try:
            entity = await _resolve(client, owner_id)
            await client.send_message(entity, text, parse_mode="html")
            _reset_unreachable(owner_id)
        except Exception as e:
            count = _mark_unreachable(owner_id)
            if count == 1:
                print(f"[error_reporter] The owner {owner_id} is not reachable ({e}). "
                      f"They have to press /start in the bot once - the reports are "
                      f"skipped for an hour.", flush=True)


async def _flush_once():
    global _last_flush_at
    if not _buffer:
        return
    items = []
    dropped = 0
    while _buffer and len(items) < MAX_RECORDS_PER_FLUSH:
        items.append(_buffer.popleft())
    if not items:
        return

    # Hard hourly cap so a burst of errors can never flood the log group.
    now_ts = datetime.now().timestamp()
    if now_ts - _reports_this_hour[1] > 3600:
        _reports_this_hour[0] = 0
        _reports_this_hour[1] = now_ts
    delivered = 0
    to_send = []
    for item in items:
        if _reports_this_hour[0] >= MAX_REPORTS_PER_HOUR:
            dropped += 1
            continue
        _reports_this_hour[0] += 1
        delivered += 1
        to_send.append(item)

    if to_send:
        await _deliver_batch(to_send)

    if dropped:
        print(f"[error_reporter] Hourly report cap reached, {dropped} report(s) dropped.", flush=True)

    _last_flush_at = now_ts


async def _deliver(item):
    """Sends a single buffered report to the log group and the owner."""
    detail = item["detail"]
    if len(detail) > 1800:
        detail = detail[:1800] + "\n…"
    body = (
        f"🚨 <b>Bot Error Report</b>\n"
        f"📍 <b>Context :</b> <code>{_escape(item['context'])}</code>\n"
        f"🕒 <b>Time :</b> <code>{item['stamp']}</code>\n\n"
        f"<pre>{_escape(detail)}</pre>"
    )
    header = (
        "🚨 <b>VILLAIN ADS BOT — ERROR REPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <i>These errors are visible to the owner only. End users receive a generic message.</i>"
    )
    for chunk in _chunks(full):
        await _send_to_log_group(chunk)
        # Note: Direct owner messaging disabled so owner's DM is not spammed.
        # Owner can view/download errorlogs.txt from the admin panel anytime.


async def _deliver_batch(items):
    """
    Sends the WHOLE batch as ONE message.

    One message per error flooded the log group - now the owner gets a
    single, tidy report per flush.
    """
    if not items:
        return
    blocks = []
    for item in items:
        detail = item["detail"]
        if len(detail) > 700:
            detail = detail[:700] + "\n..."
        blocks.append(
            f"\U0001f4cd <b>{_escape(item['context'])}</b>  <code>{item['stamp']}</code>\n"
            f"<pre>{_escape(detail)}</pre>"
        )
    count = len(items)
    header = (
        "\U0001f6a8 <b>VILLAIN ADS BOT \u2014 ERROR REPORT</b>\n"
        "\u2501" * 30 + "\n"
        f"\U0001f4c9 <b>{count}</b> error(s)\n"
        "\u26a0\ufe0f <i>Visible to the owner only. End users receive a generic message.</i>"
    )
    full = header + "\n\n" + "\n\n".join(blocks)
    for chunk in _chunks(full):
        await _send_to_log_group(chunk)
        # Direct owner PM disabled by user request. Can download full errorlogs.txt in Admin Panel.


async def _flush_loop():
    """Background task that flushes buffered errors on a fixed interval."""
    while True:
        try:
            await asyncio.sleep(FLUSH_INTERVAL)
            await _flush_once()
        except asyncio.CancelledError:
            # Final best-effort flush on shutdown.
            try:
                await _flush_once()
            except Exception:
                pass
            break
        except Exception as e:
            print(f"[error_reporter] Flush loop error: {e}", flush=True)


def schedule_flush():
    """Schedules an immediate flush if the event loop is running."""
    global _flush_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (sync context) - the buffered report will be
        # delivered on the next flush once the loop is running again.
        return
    if _flush_task is None or _flush_task.done():
        _flush_task = loop.create_task(_flush_now())


async def _flush_now():
    try:
        await _flush_once()
    except Exception as e:
        print(f"[error_reporter] Immediate flush error: {e}", flush=True)


# --------------------------------------------------------------------------
# Logging integration
# --------------------------------------------------------------------------
class TelegramErrorLogHandler(logging.Handler):
    """Root-logger handler that forwards ERROR records to the log group."""

    def emit(self, record):
        try:
            if record.levelno < logging.ERROR:
                return
            if record.name == "error_reporter":
                return  # never report ourselves

            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            detail = _format_record(record)
            item_record = {
                "context": f"logger:{record.name}",
                "detail": detail,
                "stamp": stamp,
                "level": record.levelname,
            }
            _all_error_logs.append(item_record)

            signature = f"logger:{record.name}|{detail[:200]}"
            now = datetime.now().timestamp()
            for sig, ts in list(_recent_notified):
                if sig == signature and (now - ts) < 60:
                    return
            _recent_notified.append((signature, now))

            _buffer.append(item_record)
            schedule_flush()
        except Exception:
            pass


def setup_error_reporter():
    """Installs the Telegram error handler on the root logger."""
    root = logging.getLogger()
    # Avoid installing the handler twice.
    for h in root.handlers:
        if isinstance(h, TelegramErrorLogHandler):
            return
    handler = TelegramErrorLogHandler(level=logging.ERROR)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    logger.info("Telegram error reporter installed on root logger.")


def setup_asyncio_exception_handler(loop):
    """Routes unhandled asyncio exceptions into the log group."""
    if loop is None:
        return

    def _handler(active_loop, context):
        exc = context.get("exception")
        message = context.get("message", "Unhandled exception in event loop")
        try:
            tb = exc.__traceback__ if exc else None
            add_error(f"asyncio:{message}", exc, tb)
            schedule_flush()
        except Exception:
            pass

    try:
        loop.set_exception_handler(_handler)
    except Exception:
        pass


def setup_excepthook():
    """Routes uncaught exceptions into the log group."""
    original = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            add_error("uncaught_exception", exc_value, exc_tb)
            schedule_flush()
        except Exception:
            pass
        try:
            original(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _hook
