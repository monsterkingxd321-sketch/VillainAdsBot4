import os
import asyncio
import logging
from typing import Optional
from telethon import Button
from villain_ads_bot import config
from villain_ads_bot.data import database
from villain_ads_bot.translations import TRANSLATIONS

logger = logging.getLogger(__name__)


def report_error(context: str, error: BaseException = None, tb=None):
    """
    Reports an internal error to the LOG GROUP and the OWNER.
    End users must NEVER be shown raw exception text - use this helper
    instead of leaking ``{e}`` into a user facing message.
    """
    try:
        from villain_ads_bot.core import error_reporter
        error_reporter.report(context, error, tb)
    except Exception:
        try:
            logger.error(f"{context}: {error}", exc_info=True)
        except Exception:
            pass


def user_safe_error(context: str, error: BaseException = None, tb=None) -> str:
    """
    Returns a generic, user-safe error message while reporting the real
    cause to the owner / log group.
    """
    report_error(context, error, tb)
    try:
        from villain_ads_bot.core import error_reporter
        return error_reporter.GENERIC_ERROR
    except Exception:
        return "<blockquote><b>» ❌ Something Went Wrong</b>\n\n⚠️ <i>An unexpected error occurred and has been reported to the admin team.</i></blockquote>"


def normalize_brand_suffix(suffix: str) -> str:
    """
    Normalises a branding suffix:
      * trailing whitespace is removed
      * a separating space is added when the suffix contains a space but does
        not start with one (otherwise "John" + "via @Bot" = "Johnvia @Bot")
    """
    s = (suffix or "").rstrip()
    if not s:
        return ""
    if s.startswith(" "):
        return s
    if " " in s:
        return " " + s
    return s


# ─── ACTIVE MEDIA REGISTRY ────────────────────────────────────────────────
# The cleanup loop must never delete a file that PyTgCalls is streaming from
# right now. Every file that is in use is registered here.
_active_media = set()


def mark_media_active(path: str):
    """Registers a media file as "in use"."""
    if path:
        try:
            _active_media.add(os.path.abspath(path))
        except Exception:
            pass


def unmark_media(path: str):
    """Removes a media file from the "in use" registry."""
    if path:
        try:
            _active_media.discard(os.path.abspath(path))
        except Exception:
            pass


def is_media_active(path: str) -> bool:
    """Returns True when the file is currently being streamed."""
    if not path:
        return False
    try:
        return os.path.abspath(path) in _active_media
    except Exception:
        return False


# ─── MESSAGE LIST / MODE RESOLUTION ──────────────────────────────────────
# The "multiple" mode is NEVER a separate switch the user has to remember.
# It is ALWAYS derived from the data itself, so setting 3 messages can never
# end up broadcasting the single default message.

def _clean_list(values) -> list:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    out = []
    for v in values:
        if v is None:
            continue
        text = str(v).strip()
        if text:
            out.append(text)
    return out


def get_broadcast_messages(settings: dict) -> list:
    """
    Returns the list of broadcast messages that must be used.

    If more than one message is configured the "multiple" mode is used
    automatically - the mode flag is only a display helper, never the source
    of truth.
    """
    settings = settings or {}
    multiple = _clean_list(settings.get("broadcast_messages"))
    single = _clean_list(settings.get("broadcast_msg"))
    if len(multiple) > 1:
        return multiple
    if multiple and not single:
        return multiple
    if single:
        return [single[0]]
    return multiple[:1]


def get_welcome_messages(settings: dict) -> list:
    """Returns the welcome messages (auto multiple-mode)."""
    settings = settings or {}
    multiple = _clean_list(settings.get("welcome_messages"))
    single = _clean_list(settings.get("welcome_msg"))
    if len(multiple) > 1:
        return multiple
    if multiple and not single:
        return multiple
    if single:
        return [single[0]]
    return multiple[:1]


def get_auto_reply_messages(settings: dict) -> list:
    """Returns the tag auto-reply messages (auto multiple-mode)."""
    settings = settings or {}
    multiple = _clean_list(settings.get("auto_reply_messages")) or _clean_list(settings.get("tag_messages"))
    single = _clean_list(settings.get("auto_reply_msg"))
    if len(multiple) > 1:
        return multiple
    if multiple and not single:
        return multiple
    if single:
        return [single[0]]
    return multiple[:1]


def resolve_modes(settings: dict) -> dict:
    """
    Keeps the *_mode flags in sync with the actual data and returns them.

    The flags are only used for the UI - the runtime always derives the
    behaviour from the message lists itself.
    """
    settings = settings or {}
    settings["broadcast_mode"] = "multiple" if len(get_broadcast_messages(settings)) > 1 else "single"
    settings["welcome_mode"] = "multiple" if len(get_welcome_messages(settings)) > 1 else "single"
    settings["auto_reply_mode"] = "multiple" if len(get_auto_reply_messages(settings)) > 1 else "single"
    return settings


def format_duration(seconds) -> str:
    """Converts seconds into a human readable duration (e.g. 1h 30m)."""
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return "0s"
    if total < 60:
        return f"{total}s"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    out = []
    if days:
        out.append(f"{days}d")
    if hours:
        out.append(f"{hours}h")
    if minutes:
        out.append(f"{minutes}m")
    if secs and not out:
        out.append(f"{secs}s")
    return " ".join(out) if out else "0s"


def get_broadcast_interval(settings: dict) -> int:
    """The round repeat interval in seconds (minimum 60)."""
    settings = settings or {}
    try:
        value = int(float(settings.get("broadcast_interval", 300)))
    except (TypeError, ValueError):
        value = 300
    return max(60, value)


def get_inter_group_delay(settings: dict) -> float:
    """The delay between two groups inside one round (minimum 2s)."""
    settings = settings or {}
    try:
        value = float(settings.get("inter_group_delay", 30.0) or 30.0)
    except (TypeError, ValueError):
        value = 30.0
    return max(2.0, value)


def estimate_round_duration(group_count: int, settings: dict) -> int:
    """
    Estimates how long ONE broadcast round takes.

    This is the number the user never saw before: the real gap between two
    broadcasts is the round duration PLUS the repeat interval.
    """
    delay = get_inter_group_delay(settings)
    typing_time = 2.5
    return int(max(0, group_count) * (delay + typing_time))


def get_broadcast_timing(settings: dict, group_count: int = 0) -> dict:
    """
    Returns the complete, CORRECT broadcast timing information.

        {
            "group_delay": 30.0,          # seconds between two groups
            "repeat_interval": 300,       # seconds to wait after a full round
            "round_duration": 247,        # estimated seconds for one round
            "real_period": 547,           # round_duration + repeat_interval
            "group_delay_text": "30s",
            "repeat_text": "5m",
            "round_text": "4m 7s",
            "real_period_text": "9m 7s",
        }
    """
    settings = settings or {}
    group_delay = get_inter_group_delay(settings)
    repeat = get_broadcast_interval(settings)
    round_duration = estimate_round_duration(group_count, settings)
    real_period = round_duration + repeat
    return {
        "group_delay": group_delay,
        "repeat_interval": repeat,
        "round_duration": round_duration,
        "real_period": real_period,
        "group_delay_text": format_duration(group_delay),
        "repeat_text": format_duration(repeat),
        "round_text": format_duration(round_duration),
        "real_period_text": format_duration(real_period),
    }


# ─── PYTGCALLS VERSION COMPATIBILITY ─────────────────────────────────────
# PyTgCalls 2.x renamed the stream control helpers:
#   mute_stream   -> mute
#   unmute_stream -> unmute
#   pause_stream  -> pause
#   resume_stream -> resume
# The old names raise AttributeError on 2.x, which silently broke muting,
# pausing and resuming. These helpers support both APIs transparently.

async def pytg_mute(pytg, chat_id):
    """Mutes the userbot microphone inside the group call."""
    if pytg is None:
        raise RuntimeError("PyTgCalls client is not available")
    for name in ("mute", "mute_stream"):
        fn = getattr(pytg, name, None)
        if fn is None:
            continue
        try:
            result = await fn(chat_id)
        except TypeError:
            result = await fn()
        return result
    raise RuntimeError("This PyTgCalls version exposes no mute() method")

async def pytg_unmute(pytg, chat_id):
    """Unmutes the userbot microphone inside the group call."""
    if pytg is None:
        raise RuntimeError("PyTgCalls client is not available")
    for name in ("unmute", "unmute_stream"):
        fn = getattr(pytg, name, None)
        if fn is None:
            continue
        try:
            result = await fn(chat_id)
        except TypeError:
            result = await fn()
        return result
    raise RuntimeError("This PyTgCalls version exposes no unmute() method")

async def pytg_pause(pytg, chat_id):
    """Pauses the stream."""
    if pytg is None:
        raise RuntimeError("PyTgCalls client is not available")
    for name in ("pause", "pause_stream"):
        fn = getattr(pytg, name, None)
        if fn is None:
            continue
        try:
            result = await fn(chat_id)
        except TypeError:
            result = await fn()
        return result
    raise RuntimeError("This PyTgCalls version exposes no pause() method")

async def pytg_resume(pytg, chat_id):
    """Resumes the stream."""
    if pytg is None:
        raise RuntimeError("PyTgCalls client is not available")
    for name in ("resume", "resume_stream"):
        fn = getattr(pytg, name, None)
        if fn is None:
            continue
        try:
            result = await fn(chat_id)
        except TypeError:
            result = await fn()
        return result
    raise RuntimeError("This PyTgCalls version exposes no resume() method")



EMOJI_TO_SYMBOL = {
    '🟢': '[+]',
    '🔴': '[-]',
    '❌': '[x]',
    '✅': '[✓]',
    '⚠️': '[!]',
    '💡': '[*]',
    '⚡': '[>>]',
    '🚀': '[>>]',
    '⏱️': '[~]',
    '⏱': '[~]',
    '⏳': '[..]',
    '🔍': '[?]',
    '📊': '[#]',
    '⚙️': '[*]',
    '⚙': '[*]',
    '👑': '[#]',
    '💎': '[*]',
    '👤': '[::]',
    '👥': '[::]',
    '📱': '[#]',
    '🤖': '[=]',
    '🎧': '[~]',
    '🎵': '[~]',
    '🎙️': '[~]',
    '🎙': '[~]',
    '🎬': '[~]',
    '▶️': '[>]',
    '⏸️': '[||]',
    '⏹️': '[■]',
    '⏭️': '[>>]',
    '🔇': '[-]',
    '🔊': '[+]',
    '➕': '[+]',
    '➖': '[-]',
    '🔙': '[<<]',
    '🚪': '[x]',
    '🗑️': '[-]',
    '🗑': '[-]',
    '🔄': '[~]',
    '✉️': '[#]',
    '✉': '[#]',
    '👋': '[~]',
    '💬': '[~]',
    '📢': '[!]',
    '📌': '[•]',
    '🎁': '[*]',
    '🎉': '[*]',
    '💰': '[$]',
    '💸': '[$]',
    '💳': '[$]',
    '🏦': '[$]',
    '🪙': '[$]',
    '🔐': '[*]',
    '🔒': '[*]',
    '📜': '[#]',
    '🧾': '[#]',
    '🛡️': '[#]',
    '🛡': '[#]',
    '🧪': '[*]',
    '🖥️': '[#]',
    '🖥': '[#]',
    '🌐': '[*]',
    '📡': '[*]',
    '🖼️': '[#]',
    '🖼': '[#]',
    '✏️': '[/]',
    '✏': '[/]',
    '📝': '[/]',
    '✍️': '[/]',
    '✍': '[/]',
    '🎮': '[*]',
    '🧙': '[*]',
    '🎟️': '[*]',
    '🎟': '[*]',
    '💾': '[#]',
    '📂': '[#]',
    '📤': '[^]',
    '📥': '[v]',
    '🔗': '[&]',
    '🧘': '[::]',
}

def is_emoji_mode_enabled() -> bool:
    """Returns True if emojis are enabled globally (default True)."""
    try:
        settings = database.get_global_settings() or {}
        return settings.get("emoji_mode", True)
    except Exception:
        return True

def convert_emojis_to_symbols(text: str) -> str:
    """Replaces emojis with stylish ascii and unicode tech symbols and text badges."""
    if not text or not isinstance(text, str):
        return text
    
    # Contextual ON/OFF and status badges
    text = re.sub(r'🟢\s*(?:On|ON|on)\b', '[ON]', text)
    text = re.sub(r'🔴\s*(?:Off|OFF|off)\b', '[OFF]', text)
    text = re.sub(r'🟢\s*(?:Online|ONLINE|online)\b', '[ONLINE]', text)
    text = re.sub(r'🔴\s*(?:Offline|OFFLINE|offline|Stopped|stopped)\b', '[OFFLINE]', text)

    for em, sym in EMOJI_TO_SYMBOL.items():
        text = text.replace(em, sym)
    # Generic replacement for any remaining emojis
    text = re.sub(r'[\U00010000-\U0010ffff]', '•', text)
    return text

def apply_emoji_filter(text: str) -> str:
    """Applies symbol conversion if emoji mode is toggled off."""
    if not is_emoji_mode_enabled():
        return convert_emojis_to_symbols(text)
    return text

def styled_button(text: str, callback_data: str, style: str = "primary"):
    """
    Creates a standard inline button, respecting emoji toggle.
    """
    final_text = apply_emoji_filter(text)
    try:
        return Button.inline(final_text, data=callback_data, style=style)
    except TypeError:
        # Standard Telethon fallback
        btn = Button.inline(final_text, data=callback_data)
        setattr(btn, "style", style)
        return btn


import re

def format_html_message(text: str) -> str:
    """
    Formats text into valid Telegram HTML entities with <blockquote> for card UI.
    Converts markdown blockquotes ('> ') to native Telegram <blockquote>...</blockquote> tags.
    Converts <b>bold</b> to <b>, `code` to <code>, and _italic_ to <i>.
    Respects global emoji toggle mode.
    """
    if not text or not isinstance(text, str):
        return text

    # Filter emojis if disabled
    text = apply_emoji_filter(text)

    # Check for markdown blockquote lines
    has_md_quote = bool(re.search(r'(^|\n)>\s*', text))
    
    if has_md_quote:
        lines = text.split('\n')
        new_lines = []
        in_quote = False
        for line in lines:
            if line.startswith('> '):
                content = line[2:]
                if not in_quote:
                    new_lines.append('<blockquote>' + content)
                    in_quote = True
                else:
                    new_lines.append(content)
            elif line.strip() == '>':
                if not in_quote:
                    new_lines.append('<blockquote>')
                    in_quote = True
                else:
                    new_lines.append('')
            else:
                if in_quote:
                    new_lines[-1] += '</blockquote>'
                    in_quote = False
                new_lines.append(line)
        if in_quote:
            new_lines[-1] += '</blockquote>'
        text = '\n'.join(new_lines)

    # Convert markdown formatting to HTML tags
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<i>\1</i>', text)
    
    # Format blockquotes so that each line gets its own separate <blockquote>...</blockquote>
    if "<blockquote>" in text:
        def _per_line_bq(match):
            inner = match.group(1)
            lines = inner.split('\n')
            res = []
            for l in lines:
                s = l.strip()
                if s:
                    res.append(f"<blockquote>{l}</blockquote>")
                else:
                    res.append("")
            return '\n'.join(res)
        text = re.sub(r'<blockquote>([\s\S]*?)</blockquote>', _per_line_bq, text)

    return text

def get_text(key: str, lang: Optional[str] = "en", **kwargs) -> str:
    """
    Retrieves the localized text for the given key and language.
    Falls back to English if the translation is missing.
    Automatically ensures Telegram HTML blockquote card formatting.
    """
    lang = lang or "en"
    if lang not in TRANSLATIONS:
        lang = "en"

    # The premium interface has its own catalogue.
    text = None
    try:
        from villain_ads_bot.premium_translations import get_premium_text
        text = get_premium_text(key, lang)
    except Exception:
        text = None

    if text is None:
        text = TRANSLATIONS[lang].get(key) or TRANSLATIONS["en"].get(key)
    if text is None:
        return f"[{key}]"
        
    try:
        formatted = text.format(**kwargs)
        return format_html_message(formatted)
    except Exception as e:
        logger.error(f"Formatting error for translation key '{key}': {e}")
        return format_html_message(text)

async def show_help(event, action_key: str, user_id: int) -> bool:
    """
    Displays the 'How to Use' hint for a button click.
    Uses event.answer(text, alert=...) to present a popup to the user.
    """
    user = database.get_user(user_id)
    lang = user.get("language") if user else "en"
    
    # Form the translation key
    help_key = f"help_{action_key}"
    hint = get_text(help_key, lang)
    
    # Fallback to generic message if missing
    if hint.startswith(f"[help_"):
        return False
        
    # Determine alert level: alert=True for risky/important actions, False for subtle
    show_alert = False
    risky_keywords = ["delete", "stop", "reject", "remove", "danger", "cancel"]
    if any(k in action_key.lower() for k in risky_keywords):
        show_alert = True
        
    try:
        await event.answer(hint, alert=show_alert)
        return True
    except Exception as e:
        logger.error(f"Failed to show callback help alert: {e}")
        return False

def ensure_user_dir(user_id: int) -> str:
    """
    Ensures that the user's sessions directory exists locally and returns the path.
    """
    path = os.path.join(config.USER_DATA_DIR, str(user_id), "sessions")
    os.makedirs(path, exist_ok=True)
    return path

import time

# In-memory cache for force subscribe checks
# Format: {user_id: (expiry_timestamp, not_joined_list)}
_force_sub_cache = {}

async def check_force_sub(client, user_id: int) -> list:
    """
    Checks if a user is subscribed to all force subscribe channels defined by admin.
    Caches successful checks to make the bot faster and prevent rate limits.
    """
    now = time.time()
    
    # Check cache
    if user_id in _force_sub_cache:
        expiry, cached_not_joined = _force_sub_cache[user_id]
        if now < expiry:
            return cached_not_joined
            
    from telethon.tl.functions.channels import GetParticipantRequest
    from telethon.errors import UserNotParticipantError
    
    not_joined = []
    channels = database.get_force_channels()
    for ch in channels:
        ch_id = ch["channel_id"]
        try:
            peer = int(ch_id) if (ch_id.startswith("-") or ch_id.isdigit()) else ch_id
            await asyncio.wait_for(client(GetParticipantRequest(
                channel=peer,
                participant=user_id
            )), timeout=2.5)
        except UserNotParticipantError:
            not_joined.append(ch)
        except Exception as e:
            # Under flood wait, timeout, or network glitch, fail open so users are not blocked from /start
            logger.debug(f"User {user_id} channel check {ch_id} skipped due to: {e}")
            
    # Cache the result to reduce Telegram API calls and improve latency.
    # If they joined everything, cache it for 300 seconds (5 minutes).
    # If they are missing some channels, cache it for 60 seconds (1 minute).
    cache_duration = 300 if not not_joined else 60
    _force_sub_cache[user_id] = (now + cache_duration, not_joined)
    
    return not_joined

async def send_force_sub_msg(event, not_joined: list, lang: str):
    """
    Sends a restricted-access message with URLs to join and a verify button.
    """
    buttons = []
    for i, ch in enumerate(not_joined, 1):
        label = ch.get("channel_name") or f"Channel {i}"
        link = ch.get("channel_link")
        buttons.append([Button.url(f"➕ Join {label}", url=link)])
        
    buttons.append([styled_button("✅ I've Joined — Verify", "verify_sub", style="success")])
    
    lines = [
        "⚠️ <b>Access Restricted</b>\n━━━━━━━━━━━━━━━━━━━━\nJoin these channels to use the bot:\n"
    ]
    for ch in not_joined:
        lines.append(f"• {ch.get('channel_name') or ch.get('channel_id')}")
    lines.append("\n━━━━━━━━━━━━━━━━━━━━\n_Tap Verify after joining._")
    
    await event.respond("\n".join(lines), buttons=buttons)

async def guard(event, client) -> bool:
    """
    Applies security filters to commands/callbacks: bans, maintenance mode, and force subs.
    Returns True if execution should be blocked.
    """
    user_id = event.sender_id
    if not user_id:
        return False
        
    user = database.get_user(user_id)
    if not user:
        from villain_ads_bot.data import models
        user = models.create_default_user(user_id)
        database.save_user(user)
        
    # 1. Ban Guard
    if user.get("is_banned", False):
        txt = "🚫 You are banned from using this bot."
        if hasattr(event, "answer"):
            try:
                await event.answer(txt, alert=True)
            except Exception:
                await event.respond(txt)
        else:
            await event.respond(txt)
        return True
        
    # 2. Maintenance Guard
    global_settings = database.get_global_settings()
    admins = global_settings.get("admins", [])
    is_admin = user_id in admins or user_id in config.ORIGINAL_ADMIN_IDS
    
    if global_settings.get("maintenance_mode", False) and not is_admin:
        txt = "🔧 Bot is under maintenance. Please try again later."
        if hasattr(event, "answer"):
            try:
                await event.answer(txt, alert=True)
            except Exception:
                await event.respond(txt)
        else:
            await event.respond(txt)
        return True
        
    # 2.b Agreement Guard - a user that DECLINED the agreement may not use
    # the bot. They can accept it again at any time.
    if not user.get("tos_accepted") and user.get("tos_declined"):
        lang = user.get("language", "en") or "en"
        txt = get_text("tos_declined_msg", lang)
        buttons = [[styled_button(get_text("tos_accept_btn", lang), "accept_tos", style="success")]]
        try:
            if hasattr(event, "answer"):
                await event.answer(get_text("tos_declined_alert", lang), alert=True)
        except Exception:
            pass
        try:
            if hasattr(event, "edit"):
                await event.edit(txt, buttons=buttons)
            else:
                await event.respond(txt, buttons=buttons)
        except Exception:
            try:
                await event.respond(txt, buttons=buttons)
            except Exception:
                pass
        return True
        
    # 3. Force Subscribe Guard (Only enforce in Private chats)
    if event.is_private and not is_admin:
        not_joined = await check_force_sub(client, user_id)
        if not_joined:
            await send_force_sub_msg(event, not_joined, user.get("language", "en") or "en")
            return True
            
    return False

# A very large number that is used to represent "no limit at all".
UNLIMITED_SLOTS = 999999


def is_unlimited(user_id: int) -> bool:
    """
    Returns True when the user must NOT be limited at all.

    This can come from:
      * the global "unlimited slots" switch (admin)
      * a per user override (admin gives a VIP user unlimited slots)
      * an active subscription on an "unlimited" plan
    """
    try:
        settings = database.get_global_settings() or {}
    except Exception:
        settings = {}

    if settings.get("unlimited_slots"):
        return True

    user = database.get_user(user_id)
    if not user:
        return False

    override = user.get("slot_limit_override")
    if override is not None:
        try:
            return int(override) <= 0      # 0 or less = unlimited
        except (TypeError, ValueError):
            pass

    import time
    now = time.time()
    for sub in user.get("subscriptions", []) or []:
        if sub.get("expires_at", 0) > now and sub.get("unlimited"):
            return True
    return False


def format_slots(count) -> str:
    """Formats a slot count for the UI (unlimited -> infinity sign)."""
    try:
        count = int(count)
    except (TypeError, ValueError):
        return "∞"
    if count >= UNLIMITED_SLOTS:
        return "∞"
    return str(count)


def get_allowed_slots(user_id: int) -> int:
    """
    Calculates the user's allowed bot slots dynamically based on active subscriptions.
    Automatically cleans up expired subscriptions.

    The free slot is already stored inside "allowed_slots" (see
    models.create_default_user) so it must NOT be added a second time.

    Returns UNLIMITED_SLOTS when no limit applies at all.
    """
    try:
        settings = database.get_global_settings() or {}
    except Exception:
        settings = {}

    # The global switch: nobody is limited.
    if settings.get("unlimited_slots"):
        return UNLIMITED_SLOTS

    user = database.get_user(user_id)
    if not user:
        # A new user gets the configured free allowance.
        try:
            default_limit = int(settings.get("default_slot_limit", 1) or 1)
        except (TypeError, ValueError):
            default_limit = 1
        return UNLIMITED_SLOTS if default_limit <= 0 else default_limit

    # A per user override (the admin can lift the limit for one user).
    override = user.get("slot_limit_override")
    if override is not None:
        try:
            override = int(override)
            if override <= 0:
                return UNLIMITED_SLOTS
            return max(1, override)
        except (TypeError, ValueError):
            pass

    import time
    now = time.time()
    subs = user.get("subscriptions", [])
    active_subs = []
    # "allowed_slots" already contains the free slot + every gifted slot.
    total_slots = int(user.get("allowed_slots", 1) or 1)
    unlimited = False

    for sub in subs:
        if sub.get("expires_at", 0) > now:
            if sub.get("unlimited"):
                unlimited = True
            else:
                total_slots += int(sub.get("qty", 0) or 0)
            active_subs.append(sub)

    # Save cleaned up list to DB if changes occurred
    if len(active_subs) != len(subs):
        user["subscriptions"] = active_subs
        database.save_user(user)

    if unlimited:
        return UNLIMITED_SLOTS
    return max(1, total_slots)

def allocate_slots_subscription(user_id: int, qty: int, days: int, plan_name: str,
                                payment_id: str, unlimited: bool = False) -> float:
    """
    Allocates slot subscription to a user and returns the expires_at timestamp.

    With `unlimited=True` the subscription lifts the slot limit completely -
    no ID limit applies while it is active.
    """
    user = database.get_user(user_id)
    if not user:
        return 0.0
        
    import time
    subs = user.setdefault("subscriptions", [])
    expires_at = time.time() + (days * 86400)
    
    subs.append({
        "subscription_id": payment_id,
        "qty": qty,
        "expires_at": expires_at,
        "plan_name": plan_name,
        "unlimited": bool(unlimited)
    })
    
    database.save_user(user)
    return expires_at

# ─── TELETHON PATTERN MATCH MONKEYPATCH ──────────────────────────────────
# Intercepts Telethon's CallbackQuery.Event pattern_match and wraps it
# so that all matched groups are automatically decoded from bytes to str.
# This prevents binary/bytes data from corrupting JSON database files.
from telethon import events

class DecodedMatch:
    def __init__(self, original_match):
        self._match = original_match
        
    def group(self, *args):
        res = self._match.group(*args)
        if isinstance(res, bytes):
            return res.decode("utf-8")
        if isinstance(res, tuple):
            return tuple(x.decode("utf-8") if isinstance(x, bytes) else x for x in res)
        return res
        
    def groups(self, default=None):
        res = self._match.groups(default)
        return tuple(x.decode("utf-8") if isinstance(x, bytes) else x for x in res)
        
    def __getattr__(self, name):
        return getattr(self._match, name)

def get_pattern_match(self):
    orig = self.__dict__.get("pattern_match")
    if orig is not None:
        return DecodedMatch(orig)
    return None

def set_pattern_match(self, value):
    self.__dict__["pattern_match"] = value

events.CallbackQuery.Event.pattern_match = property(get_pattern_match, set_pattern_match)


# ─── BUTTON STYLING MONKEYPATCH ──────────────────────────────────────────
# Intercepts all inline keyboards sent by the bot (via send_message, edit_message, or send_file)
# and automatically styles inline buttons in a repeating pattern (danger -> primary -> success).
# Destructive buttons (stop, delete, cancel, reject) remain danger (red), and
# constructive buttons (start, approve, accept) remain success (green).

def style_keyboard(buttons):
    if not buttons:
        return buttons
        
    # Standardize buttons list structure (Telethon accepts single button, 1D row, or 2D grid)
    is_2d = True
    if not isinstance(buttons, (list, tuple)):
        grid = [[buttons]]
        is_2d = False
    elif len(buttons) > 0 and not isinstance(buttons[0], (list, tuple)):
        grid = [list(buttons)]
        is_2d = False
    else:
        grid = [list(row) for row in buttons]
        
    # Style pattern loop: danger -> primary -> success
    styles = ["danger", "primary", "success"]
    
    for row_idx, row in enumerate(grid):
        # Determine the color style for this row based on its row index
        style = styles[row_idx % len(styles)]
        
        for btn in row:
            # Check if it is a button (has text attribute)
            if hasattr(btn, "text"):
                # Assign the style using KeyboardButtonStyle if supported by Telethon
                try:
                    from telethon.tl.types import KeyboardButtonStyle
                    # Preserve existing icon if present
                    icon = None
                    if hasattr(btn, "style") and btn.style and hasattr(btn.style, "icon"):
                        icon = btn.style.icon
                    
                    if style == "primary":
                        btn.style = KeyboardButtonStyle(bg_primary=True, icon=icon)
                    elif style == "danger":
                        btn.style = KeyboardButtonStyle(bg_danger=True, icon=icon)
                    elif style == "success":
                        btn.style = KeyboardButtonStyle(bg_success=True, icon=icon)
                    else:
                        btn.style = None
                except (ImportError, AttributeError, TypeError):
                    # Backward compatibility for older Telethon versions where style is a custom attribute string
                    try:
                        btn.style = style
                    except AttributeError:
                        setattr(btn, "style", style)
                        
    if not is_2d:
        if not isinstance(buttons, (list, tuple)):
            return grid[0][0]
        else:
            return grid[0]
    return grid

from telethon import TelegramClient

orig_send_message = TelegramClient.send_message
orig_edit_message = TelegramClient.edit_message
orig_send_file = TelegramClient.send_file

async def patched_send_message(self, entity, message=None, *args, **kwargs):
    if "buttons" in kwargs:
        kwargs["buttons"] = style_keyboard(kwargs["buttons"])
    return await orig_send_message(self, entity, message, *args, **kwargs)

async def patched_edit_message(self, entity, message=None, *args, **kwargs):
    if "buttons" in kwargs:
        kwargs["buttons"] = style_keyboard(kwargs["buttons"])
    return await orig_edit_message(self, entity, message, *args, **kwargs)

async def patched_send_file(self, entity, file, *args, **kwargs):
    if "buttons" in kwargs:
        kwargs["buttons"] = style_keyboard(kwargs["buttons"])
    return await orig_send_file(self, entity, file, *args, **kwargs)

TelegramClient.send_message = patched_send_message
TelegramClient.edit_message = patched_edit_message
TelegramClient.send_file = patched_send_file

def normalize_text(text: str) -> str:
    """
    Normalizes compatibility characters (such as mathematical bold/italic styled fonts)
    to their standard representation to bypass anti-spam filters.
    """
    return text

def parse_spintax(text: str) -> str:
    """
    Randomly chooses options in curly braces separated by vertical bars.
    Example: "{Hello|Hi|Hey} there" -> "Hi there"
    """
    import re
    import random
    if not isinstance(text, str) or not text:
        return text
    pattern = re.compile(r'\{([^{}]+)\}')
    while True:
        match = pattern.search(text)
        if not match:
            break
        choices = match.group(1).split('|')
        text = text[:match.start()] + random.choice(choices) + text[match.end():]
    return text

def make_message_unique(text: str) -> str:
    """
    Appends a random combination of zero-width unicode control characters 
    to make the message hash unique on Telegram's servers and bypass anti-spam filters.
    """
    import random
    if not isinstance(text, str) or not text:
        return text
    # Remove any existing zero-width characters first
    for zw in ['\u200b', '\u200c', '\u200d', '\ufeff']:
        text = text.replace(zw, '')
        
    # Append random invisible characters (up to 8 characters) to make string unique
    zw_chars = ['\u200b', '\u200c', '\u200d']
    random_suffix = "".join(random.choices(zw_chars, k=random.randint(3, 8)))
    return text + random_suffix


AUTHENTIC_DEVICE_PROFILES = [
    {
        "device_model": "Samsung Galaxy S24 Ultra",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.14.5 (4892)"
    },
    {
        "device_model": "Google Pixel 8 Pro",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.14.0 (4852)"
    },
    {
        "device_model": "Xiaomi 13 Pro",
        "system_version": "Android 13 (SDK 33)",
        "app_version": "10.13.1 (4781)"
    },
    {
        "device_model": "OnePlus 12",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.12.0 (4720)"
    },
    {
        "device_model": "Samsung Galaxy S23+",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.14.2 (4870)"
    },
    {
        "device_model": "Redmi Note 13 Pro+",
        "system_version": "Android 13 (SDK 33)",
        "app_version": "10.11.2 (4673)"
    },
    {
        "device_model": "Vivo X100 Pro",
        "system_version": "Android 14 (SDK 34)",
        "app_version": "10.13.0 (4790)"
    }
]

def get_device_profile(session_id: Optional[str] = None, phone: str = None) -> dict:
    """
    Returns a UNIQUE but realistic device profile for one account.

    The old implementation picked from a list of SEVEN phones - with hundreds
    of accounts that is a textbook botnet signature ("30 accounts with the
    same Samsung Galaxy S24 Ultra") and one of the main reasons Telegram
    freezes the accounts.

    Now the profile is derived from the account id, so it is stable for that
    account (it never looks like a new phone) but unique across accounts.
    """
    from villain_ads_bot.core.fingerprint import get_device_profile as _fp
    return _fp(session_id, phone)


_handled_commands = {}

def check_and_mark_command(chat_id: int, text: str, msg_id: int = 0, ttl: float = 1.0) -> bool:
    """
    Checks if a command in a given chat was already handled in the last ttl seconds.
    If msg_id is provided, keys by (chat_id, msg_id) to prevent duplicate processing
    of the exact same message across bot and userbot, while allowing rapid sequential commands.
    Returns True if this is a new command (and marks it), False if duplicate.
    """
    import time
    now = time.time()
    # Clean old entries
    for k in list(_handled_commands.keys()):
        if now - _handled_commands[k] > 10.0:
            _handled_commands.pop(k, None)
            
    if msg_id:
        key = (chat_id, msg_id)
    else:
        key = (chat_id, (text or "").strip().lower())
        
    last_t = _handled_commands.get(key, 0.0)
    if now - last_t < ttl:
        return False
        
    _handled_commands[key] = now
    return True

