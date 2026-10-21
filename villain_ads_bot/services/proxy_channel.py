"""
Reading the proxy source channel.

The owner does NOT have a channel of their own. The proxies are dropped in
a public channel (e.g. @ProxyMTProto) and the BOT cannot join a channel as
a bot. But a USERBOT can.

So the flow is:

    1. the admin registers the source channel (changeable at any time)
    2. the admin picks one of their accounts as the "reader"
    3. that account JOINS the channel
    4. the bot reads the last messages THROUGH that account
    5. every proxy link in those messages is extracted and put into the pool
    6. the other accounts get one proxy each (one IP per account)

No mirror channel and no copying needed.
"""

import asyncio
import logging
import re
import time

logger = logging.getLogger(__name__)

READER_KEY = "proxy_reader_account"
LAST_SCAN_KEY = "proxy_last_scan"


def get_reader_account() -> str:
    """The account (session id) that reads the proxy channel."""
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        return (settings.get(READER_KEY) or "").strip()
    except Exception:
        return ""


def set_reader_account(session_id: str) -> tuple:
    """Sets the account that reads the proxy channel."""
    value = (session_id or "").strip()
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        settings[READER_KEY] = value
        database.save_global_settings(settings)
        return True, f"✅ The reader account is now {value}" if value else "✅ Reader account removed"
    except Exception as e:
        return False, f"❌ {e}"


def get_last_scan() -> float:
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        return float(settings.get(LAST_SCAN_KEY, 0) or 0)
    except Exception:
        return 0.0


def _extract_proxies(text: str) -> list:
    """Returns every proxy link of a message (in the order they appear)."""
    from villain_ads_bot.core import proxy as proxy_tool
    found = []
    seen = set()
    for match in re.finditer(r"https?://t\.me/proxy\?[^\s\"'<>]+", text or ""):
        proxy = proxy_tool.parse_mtproto_link(match.group(0))
        if proxy:
            key = (proxy["host"], proxy["port"], proxy["secret"])
            if key not in seen:
                seen.add(key)
                found.append(proxy)
    for match in re.finditer(r"tg://proxy\?[^\s\"'<>]+", text or ""):
        proxy = proxy_tool.parse_mtproto_link(match.group(0))
        if proxy:
            key = (proxy["host"], proxy["port"], proxy["secret"])
            if key not in seen:
                seen.add(key)
                found.append(proxy)
    return found


async def join_channel(client, channel: str) -> tuple:
    """
    Makes an account JOIN the proxy channel.

    Returns (ok, message).
    """
    value = (channel or "").strip()
    if not value:
        return False, "No channel is set."

    invite_hash = None
    username = None
    m = re.search(r"(?:t\.me|telegram\.(?:me|dog|org))/\+([A-Za-z0-9_\-]+)", value)
    if m:
        invite_hash = m.group(1)
    else:
        cleaned = re.sub(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.(?:me|dog|org))/", "", value)
        cleaned = cleaned.lstrip("@").split("/")[0].split("?")[0]
        username = cleaned or None

    try:
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        if invite_hash:
            await client(ImportChatInviteRequest(invite_hash))
        elif username:
            await client(JoinChannelRequest(username))
        else:
            return False, "Could not understand the channel link."
        return True, f"✅ Joined @{username}" if username else "✅ Joined the channel"
    except Exception as e:
        err = str(e).lower()
        if "already" in err or "participant" in err:
            return True, "✅ The account is already a member of that channel."
        if "invite" in err and "hash" in err:
            return False, "❌ The invite link is invalid or expired."
        return False, f"❌ Could not join: {e}"


async def read_channel_proxies(reader_client, channel: str = None, limit: int = 40) -> tuple:
    """
    Reads the proxy channel through a userbot and returns the proxies.

    Returns (proxies, error).
    """
    from villain_ads_bot.core import proxy as proxy_tool

    channel = (channel or proxy_tool.get_channel() or "").strip()
    if not channel:
        return [], "No proxy channel is set."
    if reader_client is None:
        return [], "No reader account is available."

    try:
        entity = await reader_client.get_entity(channel)
    except Exception as e:
        return [], f"Could not open the channel: {e}"

    try:
        messages = await reader_client.get_messages(entity, limit=limit)
    except Exception as e:
        return [], f"Could not read the channel: {e}"

    proxies = []
    seen = set()
    for message in messages or []:
        text = getattr(message, "text", None) or getattr(message, "message", None) or ""
        for proxy in _extract_proxies(text):
            key = (proxy["host"], proxy["port"], proxy["secret"])
            if key in seen:
                continue
            seen.add(key)
            proxies.append(proxy)
    return proxies, None


async def refresh_from_channel(reader_client, channel: str = None, limit: int = 40) -> dict:
    """
    Reads the channel and puts the fresh proxies into the pool.

    Returns {"ok", "found", "added", "total", "error"}.
    """
    from villain_ads_bot.core import proxy as proxy_tool

    result = {"ok": False, "found": 0, "added": 0, "total": 0, "error": None}
    try:
        proxies, error = await read_channel_proxies(reader_client, channel, limit)
        if error:
            result["error"] = error
            return result
        result["found"] = len(proxies)
        if proxies:
            added, total = proxy_tool.merge_pool(proxies)
            result["added"] = added
            result["total"] = total
            result["ok"] = True
        else:
            result["error"] = "No proxy link found in that channel."
        try:
            from villain_ads_bot.data import database
            settings = database.get_global_settings() or {}
            settings[LAST_SCAN_KEY] = time.time()
            database.save_global_settings(settings)
        except Exception:
            pass
    except Exception as e:
        result["error"] = str(e)
    return result


def pick_reader_account(sessions) -> str:
    """
    Picks the account that should read the channel.

    The one that is already running is preferred - it is connected and can
    read immediately.
    """
    from villain_ads_bot.services import userbot_manager
    running = [s for s in sessions if userbot_manager.is_bot_running(s.get("phone") or s.get("session_id"))]
    pool = running or list(sessions)
    if not pool:
        return ""
    return pool[0].get("session_id") or pool[0].get("phone") or ""
