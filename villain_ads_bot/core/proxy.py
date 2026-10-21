"""
Proxy support - the key to running an unlimited number of Telegram accounts.

Telegram limits the number of accounts that may be active from the same IP
address and the number of sessions that may be created per IP. Every
userbot that shares one IP is counted together, so the limit is reached
after only a handful of accounts.

With one proxy per userbot every account gets its own IP address, so the
limit practically disappears.

TWO KINDS OF PROXIES ARE SUPPORTED

1. SOCKS5 / HTTP  (classic web proxies)
       socks5://user:password@host:port
       http://user:password@host:port
       host:port                (defaults to socks5)

2. MTProto       (Telegram's own proxy - what the free proxy channels drop)
       https://t.me/proxy?server=mtproxygram.lol&port=853&secret=ee60...
       tg://proxy?server=...&port=...&secret=...
       server:port:secret

   Telethon connects to an MTProto proxy with
   `ConnectionTcpMTProxyRandomizedIntermediate` and the tuple
   ``(host, port, secret)``.

PROXY CHANNEL
A Telegram channel that drops fresh proxies (usually every 30 minutes) can
be registered in the admin panel. The bot scans it automatically, extracts
every proxy link and keeps the pool up to date - so the accounts never run
out of working proxies.
"""

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PROXY_KEY = "proxies"          # the pool inside the global settings
DEFAULT_PROXY_KEY = "default_proxy"
CHANNEL_KEY = "proxy_channel"  # the channel that drops the proxies
LAST_SCAN_KEY = "proxy_last_scan"

MTPROTO = "mtproto"


# ── parsing ───────────────────────────────────────────────────────────────
def parse_mtproto_link(text: str) -> Optional[dict]:
    """
    Extracts an MTProto proxy out of a proxy link.

    Handles:
        https://t.me/proxy?server=host&port=853&secret=ee60...
        tg://proxy?server=host&port=853&secret=ee60...
        host:853:ee60...

    Returns {"type": "mtproto", "host", "port", "secret", "raw"}.
    """
    if not text:
        return None
    raw = str(text).strip()

    server = port = secret = None

    m = re.search(r"server=([^&\s]+)", raw)
    if m:
        server = m.group(1)
    m = re.search(r"port=(\d+)", raw)
    if m:
        port = int(m.group(1))
    m = re.search(r"secret=([0-9a-fA-F]+)", raw)
    if m:
        secret = m.group(1)

    if not (server and port and secret):
        # the bare "host:port:secret" form
        parts = raw.split(":")
        if len(parts) >= 3:
            try:
                port = int(parts[1])
            except (TypeError, ValueError):
                port = None
            server = parts[0]
            secret = parts[2]
            if not secret or not re.fullmatch(r"[0-9a-fA-F]{2,}", secret):
                return None

    if not server or not port or not secret:
        return None
    if port <= 0 or port > 65535:
        return None
    # a valid secret is at least 32 hex chars (16 bytes)
    if len(secret) < 32:
        return None

    return {
        "type": MTPROTO,
        "host": server,
        "port": port,
        "secret": secret,
        "raw": raw,
    }


def parse_proxy(text: str) -> Optional[dict]:
    """
    Parses a proxy string into a dictionary.

        {"type": "socks5" | "mtproto", "host", "port", ...}

    Returns None when the string cannot be parsed.
    """
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None

    # An MTProto link always carries a "secret" parameter.
    if "secret=" in raw or raw.startswith("tg://proxy"):
        return parse_mtproto_link(raw)

    # The bare "host:port:secret" form of an MTProto proxy.
    parts = raw.split(":")
    if len(parts) == 3 and re.fullmatch(r"[0-9a-fA-F]{16,}", parts[2] or ""):
        return parse_mtproto_link(raw)

    proxy_type = "socks5"
    rest = raw
    if "://" in rest:
        scheme, rest = rest.split("://", 1)
        scheme = scheme.lower()
        if scheme in ("socks5", "socks", "socks5h"):
            proxy_type = "socks5"
        elif scheme in ("http", "https"):
            proxy_type = "http"
        else:
            logger.warning(f"Unknown proxy scheme '{scheme}' - using socks5.")
            proxy_type = "socks5"

    username = ""
    password = ""
    if "@" in rest:
        creds, rest = rest.rsplit("@", 1)
        if ":" in creds:
            username, password = creds.split(":", 1)
        else:
            username = creds
    rest = rest.split("/")[0]

    if ":" not in rest:
        return None
    host, port_str = rest.rsplit(":", 1)
    host = host.strip().strip("[]")
    try:
        port = int(port_str.strip())
    except (TypeError, ValueError):
        return None
    if not host or port <= 0 or port > 65535:
        return None

    return {
        "type": proxy_type,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "raw": raw,
    }


def build_telethon_proxy(proxy: dict) -> Optional[tuple]:
    """
    Builds the proxy argument that Telethon expects.

    * MTProto -> ``(host, port, secret)``
    * SOCKS5  -> ``(type, addr, port, rdns, username, password)``
    """
    if not proxy:
        return None
    try:
        ptype = proxy.get("type", "socks5")
        if ptype == MTPROTO:
            return (
                proxy.get("host"),
                int(proxy.get("port")),
                proxy.get("secret"),
            )
        if ptype == "http":
            # Telethon only supports socks5 for the MTProto connection.
            ptype = "socks5"
        return (
            ptype,
            proxy.get("host"),
            int(proxy.get("port")),
            True,
            proxy.get("username") or None,
            proxy.get("password") or None,
        )
    except Exception as e:
        logger.warning(f"Could not build the telethon proxy: {e}")
        return None


def get_connection_class(proxy: dict):
    """
    Returns the Telethon connection class that fits the proxy.

    An MTProto proxy needs `ConnectionTcpMTProxyRandomizedIntermediate`,
    everything else uses the normal obfuscated TCP connection.
    """
    if proxy and proxy.get("type") == MTPROTO:
        try:
            from telethon.network.connection.tcpmtproxy import (
                ConnectionTcpMTProxyRandomizedIntermediate,
            )
            return ConnectionTcpMTProxyRandomizedIntermediate
        except Exception as e:
            logger.warning(f"MTProto is not available in this telethon: {e}")
    from telethon.network.connection import ConnectionTcpObfuscated
    return ConnectionTcpObfuscated


def redact(proxy: dict) -> str:
    """Returns a displayable version of the proxy (the password is hidden)."""
    if not proxy:
        return "—"
    host = proxy.get("host", "?")
    port = proxy.get("port", "?")
    if proxy.get("type") == MTPROTO:
        secret = str(proxy.get("secret", ""))
        return f"mtproto://{host}:{port} (…{secret[-6:]})"
    user = proxy.get("username") or ""
    auth = f"{user}:•••@" if user else ""
    return f"{proxy.get('type', 'socks5')}://{auth}{host}:{port}"


# ── the admin managed proxy pool ──────────────────────────────────────────
def get_pool() -> list:
    """Returns every proxy of the admin managed pool."""
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        pool = settings.get(PROXY_KEY, []) or []
        return [p for p in pool if isinstance(p, dict) and p.get("host")]
    except Exception:
        return []


def save_pool(pool: list) -> bool:
    """Saves the proxy pool."""
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        settings[PROXY_KEY] = pool
        database.save_global_settings(settings)
        return True
    except Exception as e:
        logger.warning(f"Could not save the proxy pool: {e}")
        return False


def add_proxy(text: str) -> Tuple[bool, str]:
    """Adds a proxy to the pool. Returns (ok, message)."""
    proxy = parse_proxy(text)
    if not proxy:
        return False, "❌ Invalid proxy. Use socks5://user:pass@host:port or a t.me/proxy link"
    pool = get_pool()
    for existing in pool:
        if (existing.get("host") == proxy["host"]
                and int(existing.get("port", 0)) == int(proxy["port"])
                and existing.get("secret") == proxy.get("secret")):
            return False, "⚠️ This proxy is already in the pool."
    pool.append(proxy)
    save_pool(pool)
    return True, f"✅ Proxy added ({redact(proxy)})"


def remove_proxy(index: int) -> Tuple[bool, str]:
    """Removes a proxy from the pool."""
    pool = get_pool()
    try:
        index = int(index)
    except (TypeError, ValueError):
        return False, "❌ Invalid index."
    if index < 0 or index >= len(pool):
        return False, "❌ No proxy with that index."
    removed = pool.pop(index)
    save_pool(pool)
    return True, f"🗑 Removed {redact(removed)}"


def set_default_proxy(index: int) -> Tuple[bool, str]:
    """Sets the proxy that is auto-assigned to new accounts."""
    pool = get_pool()
    try:
        index = int(index)
    except (TypeError, ValueError):
        return False, "❌ Invalid index."
    if index < 0 or index >= len(pool):
        return False, "❌ No proxy with that index."
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        settings[DEFAULT_PROXY_KEY] = index
        database.save_global_settings(settings)
        return True, f"✅ New accounts will automatically use {redact(pool[index])}"
    except Exception as e:
        return False, f"❌ {e}"


def get_default_proxy() -> Optional[dict]:
    """Returns the proxy that should be auto-assigned to a new account."""
    pool = get_pool()
    if not pool:
        return None
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        index = int(settings.get(DEFAULT_PROXY_KEY, -1))
    except Exception:
        index = -1
    if 0 <= index < len(pool):
        return pool[index]
    return None


def assign_round_robin(session_id: str) -> Optional[dict]:
    """
    Picks a proxy for an account in a round robin way, so every account gets
    a different IP address.
    """
    pool = get_pool()
    if not pool:
        return get_default_proxy()
    # A plain sum of the digits is NOT enough: phone numbers like 111, 222
    # and 333 all have the same remainder and would all get the same proxy.
    import hashlib
    h = int(hashlib.md5(str(session_id or "").encode("utf-8")).hexdigest(), 16)
    return pool[h % len(pool)]


# ── the proxy channel ────────────────────────────────────────────────────
def get_channel() -> str:
    """Returns the channel that drops the proxies (empty when not set)."""
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        return (settings.get(CHANNEL_KEY) or "").strip()
    except Exception:
        return ""


def set_channel(channel: str) -> Tuple[bool, str]:
    """Sets the proxy drop channel."""
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
        settings[CHANNEL_KEY] = value
        database.save_global_settings(settings)
        return True, f"✅ Proxy channel set to @{value}" if value else "✅ Proxy channel removed"
    except Exception as e:
        return False, f"❌ {e}"


def extract_proxies_from_text(text: str) -> list:
    """Extracts every proxy link out of a message."""
    found = []
    if not text:
        return found
    for match in re.finditer(r"https?://t\.me/proxy\?[^\s\"'<>]+", text):
        proxy = parse_mtproto_link(match.group(0))
        if proxy:
            found.append(proxy)
    for match in re.finditer(r"tg://proxy\?[^\s\"'<>]+", text):
        proxy = parse_mtproto_link(match.group(0))
        if proxy:
            found.append(proxy)
    return found


async def scan_channel(client, channel: str = None, limit: int = 40) -> list:
    """
    Reads the last messages of the proxy channel and returns every proxy
    that was found (newest first).

    The bot itself must be a member of that channel.
    """
    channel = (channel or get_channel() or "").strip()
    if not channel:
        return []
    proxies = []
    seen = set()
    try:
        entity = await client.get_entity(channel)
    except Exception as e:
        logger.warning(f"Could not open the proxy channel '{channel}': {e}")
        return []

    try:
        async for message in client.iter_messages(entity, limit=limit):
            text = getattr(message, "text", None) or getattr(message, "message", None) or ""
            for proxy in extract_proxies_from_text(text):
                key = (proxy["host"], proxy["port"], proxy["secret"])
                if key in seen:
                    continue
                seen.add(key)
                proxies.append(proxy)
    except Exception as e:
        logger.warning(f"Could not read the proxy channel '{channel}': {e}")
    return proxies


def merge_pool(proxies: list) -> Tuple[int, int]:
    """
    Puts the freshly scanned proxies into the pool (newest first).

    Returns (added, total).
    """
    pool = get_pool()
    existing = {(p.get("host"), p.get("port"), p.get("secret")) for p in pool}
    fresh = []
    for proxy in proxies or []:
        key = (proxy.get("host"), proxy.get("port"), proxy.get("secret"))
        if key in existing:
            continue
        existing.add(key)
        fresh.append(proxy)
    if fresh:
        # The newest proxies come first - a restart then picks a fresh one.
        save_pool(fresh + pool)
    return len(fresh), len(get_pool())


async def refresh_pool_from_channel(client, channel: str = None, limit: int = 40) -> dict:
    """
    Scans the proxy channel and updates the pool.

    Returns {"ok", "found", "added", "total", "error"}.
    """
    result = {"ok": False, "found": 0, "added": 0, "total": 0, "error": None}
    try:
        proxies = await scan_channel(client, channel, limit)
        result["found"] = len(proxies)
        if proxies:
            added, total = merge_pool(proxies)
            result["added"] = added
            result["total"] = total
            result["ok"] = True
        else:
            result["error"] = "No proxy link found in that channel."
        try:
            from villain_ads_bot.data import database
            settings = database.get_global_settings() or {}
            settings[LAST_SCAN_KEY] = __import__("time").time()
            database.save_global_settings(settings)
        except Exception:
            pass
    except Exception as e:
        result["error"] = str(e)
    return result


def get_last_scan() -> float:
    """Returns the timestamp of the last channel scan."""
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        return float(settings.get(LAST_SCAN_KEY, 0) or 0)
    except Exception:
        return 0.0


# ── the per account proxy ────────────────────────────────────────────────
def get_session_proxy(session_id: str) -> Optional[dict]:
    """Returns the proxy that is configured for one account."""
    try:
        from villain_ads_bot.data import database
        sess = database.get_session(session_id) or {}
        proxy = sess.get("proxy")
        return proxy if isinstance(proxy, dict) and proxy.get("host") else None
    except Exception:
        return None


def is_auto(session_id: str) -> bool:
    """Returns True when the account picks its proxy automatically."""
    try:
        from villain_ads_bot.data import database
        sess = database.get_session(session_id) or {}
        return bool(sess.get("proxy_auto"))
    except Exception:
        return False


def set_session_proxy(session_id: str, proxy_text: str) -> Tuple[bool, str]:
    """
    Sets (or clears) the proxy of one account.

    An empty value removes the proxy. The word "auto" switches the account
    to the automatic pool (it always uses the freshest proxy).
    """
    try:
        from villain_ads_bot.data import database
        sess = database.get_session(session_id)
        if not sess:
            return False, "❌ Session not found."
        text = (proxy_text or "").strip()
        if not text:
            sess.pop("proxy", None)
            sess["proxy_auto"] = False
            database.save_session(sess)
            return True, "✅ Proxy removed from this account."
        if text.lower() in ("auto", "automatic"):
            sess.pop("proxy", None)
            sess["proxy_auto"] = True
            database.save_session(sess)
            return True, "✅ This account will now use the freshest proxy from the pool."
        proxy = parse_proxy(text)
        if not proxy:
            return False, "❌ Invalid proxy. Use socks5://user:pass@host:port or a t.me/proxy link"
        sess["proxy"] = proxy
        sess["proxy_auto"] = False
        database.save_session(sess)
        return True, f"✅ Proxy saved ({redact(proxy)}). Restart the account to apply it."
    except Exception as e:
        return False, f"❌ {e}"


def remove_session_proxy(session_id: str) -> bool:
    return set_session_proxy(session_id, "")[0]


def resolve_session_proxy(session_id: str) -> Optional[dict]:
    """
    Returns the proxy an account must use right now.

    * a fixed proxy is used as it is (the IP stays stable!)
    * an "auto" account gets a proxy from the pool that no other account
      uses - it is then PERSISTED, so the IP does not change on its own
    """
    fixed = get_session_proxy(session_id)
    if fixed:
        return fixed
    try:
        from villain_ads_bot.data import database
        sess = database.get_session(session_id) or {}
        if sess.get("proxy_auto"):
            from villain_ads_bot.services import proxy_rotation
            assigned = proxy_rotation.assign_fresh_proxies([session_id])
            if assigned.get(session_id):
                return assigned[session_id]
    except Exception as e:
        logger.warning(f"Could not resolve the proxy of {session_id}: {e}")
    return None
