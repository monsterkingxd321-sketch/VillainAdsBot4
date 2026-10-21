"""
Proxy support - the key to running an unlimited number of Telegram accounts.

Telegram limits the number of accounts that may be active from the same IP
address and the number of sessions that may be created per IP. Every
userbot that shares one IP is counted together, so the limit is reached
after only a handful of accounts.

With one proxy per userbot every account gets its own IP address, so the
limit practically disappears.

Supported formats (SOCKS5 is recommended):

    socks5://user:password@host:port
    socks5://host:port
    http://user:password@host:port
    host:port                (defaults to socks5)
"""

import logging
import random
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PROXY_KEY = "proxies"          # the pool inside the global settings
DEFAULT_PROXY_KEY = "default_proxy"


# ── parsing ───────────────────────────────────────────────────────────────
def parse_proxy(text: str) -> Optional[dict]:
    """
    Parses a proxy string into a dictionary.

        {"type": "socks5", "host": "...", "port": 1080,
         "username": "", "password": "", "raw": "..."}

    Returns None when the string cannot be parsed.
    """
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None

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
    # strip any path
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

        (proxy_type, addr, port, rdns, username, password)
    """
    if not proxy:
        return None
    try:
        ptype = proxy.get("type", "socks5")
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


def redact(proxy: dict) -> str:
    """Returns a displayable version of the proxy (the password is hidden)."""
    if not proxy:
        return "—"
    host = proxy.get("host", "?")
    port = proxy.get("port", "?")
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
        return False, "❌ Invalid proxy. Use socks5://user:pass@host:port"
    pool = get_pool()
    for existing in pool:
        if (existing.get("host") == proxy["host"]
                and int(existing.get("port", 0)) == int(proxy["port"])):
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


def set_session_proxy(session_id: str, proxy_text: str) -> Tuple[bool, str]:
    """
    Sets (or clears) the proxy of one account.

    An empty value removes the proxy.
    """
    try:
        from villain_ads_bot.data import database
        sess = database.get_session(session_id)
        if not sess:
            return False, "❌ Session not found."
        text = (proxy_text or "").strip()
        if not text:
            sess.pop("proxy", None)
            database.save_session(sess)
            return True, "✅ Proxy removed from this account."
        proxy = parse_proxy(text)
        if not proxy:
            return False, "❌ Invalid proxy. Use socks5://user:pass@host:port"
        sess["proxy"] = proxy
        database.save_session(sess)
        return True, f"✅ Proxy saved ({redact(proxy)}). Restart the account to apply it."
    except Exception as e:
        return False, f"❌ {e}"


def remove_session_proxy(session_id: str) -> bool:
    return set_session_proxy(session_id, "")[0]
