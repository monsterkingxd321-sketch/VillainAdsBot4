"""
The proxy mirror.

The owner has a big "proxy drop" channel. Every 30 minutes fresh proxies
are posted there - sometimes one, sometimes two, sometimes three, each with
a "Connect 1 / Connect 2 / Connect 3" button.

This service mirrors those messages into the owner's OWN channel and rebuilds
the buttons with our own callback data, so that a click on "Connect 2"
reaches THIS bot. The bot then extracts the second proxy of that message
and hands it to the user (or moves the user's account onto it).

    @bigproxychannel  --->  @yourchannel   (the mirror)
                              "🔗 Connect 1" "🔗 Connect 2"
                                   |
                                   v
                        the proxy of the message is used
"""

import asyncio
import logging
import re
import time

logger = logging.getLogger(__name__)

MIRROR_CHANNEL_KEY = "proxy_mirror_channel"
LAST_MIRROR_KEY = "proxy_last_mirror"

# The callback data of a mirrored connect button.
CONNECT_PREFIX = "proxy_connect_"


def get_mirror_channel() -> str:
    """The channel the proxies are mirrored INTO."""
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        return (settings.get(MIRROR_CHANNEL_KEY) or "").strip()
    except Exception:
        return ""


def set_mirror_channel(channel: str) -> tuple:
    """Sets the mirror channel."""
    value = (channel or "").strip()
    if value:
        value = value.lstrip("@")
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if value.lower().startswith(prefix):
                value = value[len(prefix):]
                break
        value = value.split("/")[0].split("?")[0]
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        settings[MIRROR_CHANNEL_KEY] = value
        database.save_global_settings(settings)
        return True, f"✅ Mirror channel set to @{value}" if value else "✅ Mirror channel removed"
    except Exception as e:
        return False, f"❌ {e}"


def get_last_mirror() -> float:
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        return float(settings.get(LAST_MIRROR_KEY, 0) or 0)
    except Exception:
        return 0.0


def _extract_proxies(text: str) -> list:
    """Returns every proxy of a message (in the order they appear)."""
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


def build_connect_buttons(message_id: int, count: int):
    """Builds the 'Connect N' buttons for a mirrored message."""
    from telethon import Button
    buttons = []
    row = []
    for i in range(1, max(1, count) + 1):
        row.append(Button.inline(f"🔗 Connect {i}", f"{CONNECT_PREFIX}{message_id}_{i}"))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons


async def mirror_once(client, source: str = None, target: str = None, limit: int = 25) -> dict:
    """
    Mirrors the latest messages of the source channel into the target
    channel, rebuilding the connect buttons.

    Returns {"ok", "mirrored", "proxies", "error"}.
    """
    from villain_ads_bot.core import proxy as proxy_tool

    result = {"ok": False, "mirrored": 0, "proxies": 0, "error": None}

    source = (source or proxy_tool.get_channel() or "").strip()
    target = (target or get_mirror_channel() or "").strip()
    if not source:
        result["error"] = "No source channel is set."
        return result
    if not target:
        result["error"] = "No mirror channel is set."
        return result

    try:
        source_entity = await client.get_entity(source)
        target_entity = await client.get_entity(target)
    except Exception as e:
        result["error"] = f"Could not open the channel: {e}"
        return result

    try:
        messages = await client.get_messages(source_entity, limit=limit)
    except Exception as e:
        result["error"] = f"Could not read the source channel: {e}"
        return result

    for message in reversed(messages or []):
        text = getattr(message, "text", None) or getattr(message, "message", None) or ""
        proxies = _extract_proxies(text)
        if not proxies:
            continue
        try:
            posted = await client.send_message(
                target_entity,
                text,
                parse_mode=None,
            )
        except Exception as e:
            result["error"] = f"Could not post into the mirror channel: {e}"
            continue

        try:
            buttons = build_connect_buttons(posted.id, len(proxies))
            if buttons:
                await client.edit_message(target_entity, posted.id, text,
                                          buttons=buttons, parse_mode=None)
        except Exception:
            pass

        result["mirrored"] += 1
        result["proxies"] += len(proxies)

    # The fresh proxies also go into the pool, so the accounts can use them.
    try:
        all_proxies = []
        for message in messages or []:
            text = getattr(message, "text", None) or getattr(message, "message", None) or ""
            all_proxies.extend(_extract_proxies(text))
        if all_proxies:
            proxy_tool.merge_pool(all_proxies)
    except Exception:
        pass

    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        settings[LAST_MIRROR_KEY] = time.time()
        database.save_global_settings(settings)
    except Exception:
        pass

    result["ok"] = True
    return result


async def resolve_connect(client, mirror_channel: str, message_id: int, index: int) -> tuple:
    """
    Reads a mirrored message and returns the requested proxy.

    Returns (proxy, error).
    """
    from villain_ads_bot.core import proxy as proxy_tool

    if not mirror_channel:
        return None, "The mirror channel is not set."
    try:
        entity = await client.get_entity(mirror_channel)
        message = await client.get_messages(entity, ids=message_id)
    except Exception as e:
        return None, f"Could not read the message: {e}"
    if not message:
        return None, "The message no longer exists."

    text = getattr(message, "text", None) or getattr(message, "message", None) or ""
    proxies = _extract_proxies(text)
    if not proxies:
        return None, "This message does not contain a proxy."
    index = max(1, int(index or 1))
    if index > len(proxies):
        return None, f"This message only has {len(proxies)} proxy/proxies."
    return proxies[index - 1], None


async def auto_mirror_loop(client, interval: int = 600):
    """
    Mirrors the proxy channel into the mirror channel every `interval`
    seconds (the drop channel posts every 30 minutes, so 10 minutes is
    enough).
    """
    while True:
        try:
            await asyncio.sleep(interval)
            if not get_mirror_channel() or not get_source_channel():
                continue
            result = await mirror_once(client)
            if result.get("ok") and result.get("mirrored"):
                logger.info(
                    f"Proxy mirror: {result['mirrored']} message(s), "
                    f"{result['proxies']} proxy/proxies."
                )
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Proxy mirror error: {e}")
            await asyncio.sleep(60)


def get_source_channel() -> str:
    """The channel the proxies are dropped in."""
    try:
        from villain_ads_bot.core import proxy as proxy_tool
        return proxy_tool.get_channel()
    except Exception:
        return ""
