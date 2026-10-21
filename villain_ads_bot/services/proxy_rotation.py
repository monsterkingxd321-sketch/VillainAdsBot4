"""
Safe proxy rotation.

THE PROBLEM
Telegram watches the IP of an account. An account whose IP changes every
30 minutes looks like a hijacked account and can trigger
``AUTH_KEY_DUPLICATED`` - the session is then revoked and the user has to
log in again. Sharing one proxy between many accounts is just as bad as
having no proxy at all.

THE SAFE MODEL
* Every account gets a FIXED proxy. It keeps that proxy until the owner
  deliberately rotates it.
* The 30 minute pool refresh only ADDS new proxies - it never changes the
  proxy of a running account.
* Rotating an account is a deliberate admin action: the account is fully
  DISCONNECTED first, the new proxy is written, and only then the account
  starts again. The old transport is therefore always closed before the new
  IP is used - no auth key is ever used from two IPs at the same time.
"""
import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# The gap between the disconnect and the reconnect. The old transport needs
# a moment to close, otherwise Telegram can see the same auth key from two
# IPs and kills the session.
RECONNECT_DELAY = 8


def get_assigned_proxies() -> dict:
    """Returns {session_id: proxy} of every account that has a proxy."""
    assigned = {}
    try:
        from villain_ads_bot.data import database
        for sess in database.get_sessions():
            sid = sess.get("session_id") or sess.get("phone")
            proxy = sess.get("proxy")
            if sid and isinstance(proxy, dict) and proxy.get("host"):
                assigned[sid] = proxy
    except Exception as e:
        logger.warning(f"Could not read the assigned proxies: {e}")
    return assigned


def get_unused_proxies() -> list:
    """Returns the pool proxies that no account uses right now (newest first)."""
    from villain_ads_bot.core import proxy as proxy_tool
    pool = proxy_tool.get_pool()
    used = set()
    for proxy in get_assigned_proxies().values():
        used.add((proxy.get("host"), proxy.get("port"), proxy.get("secret")))
    return [p for p in pool
            if (p.get("host"), p.get("port"), p.get("secret")) not in used]


def assign_fresh_proxies(session_ids) -> dict:
    """
    Assigns a fresh (unused) proxy to every account in `session_ids`.

    Accounts that already have a proxy keep it - only the accounts WITHOUT
    one get a new proxy.

    Returns {session_id: proxy}.
    """
    from villain_ads_bot.core import proxy as proxy_tool
    from villain_ads_bot.data import database

    assigned = {}
    free = get_unused_proxies()
    for sid in session_ids or []:
        try:
            sess = database.get_session(sid)
            if not sess:
                continue
            if isinstance(sess.get("proxy"), dict) and sess["proxy"].get("host"):
                continue          # it already has one - keep it
            if not free:
                break             # nothing left to assign
            proxy = free.pop(0)
            sess["proxy"] = proxy
            sess["proxy_auto"] = False
            database.save_session(sess)
            assigned[sid] = proxy
        except Exception as e:
            logger.warning(f"Could not assign a proxy to {sid}: {e}")
    return assigned


async def rotate_account_proxies(session_ids, restart: bool = True) -> dict:
    """
    Rotates the proxy of the given accounts - SAFELY.

    For every account:
      1. the userbot is STOPPED (the transport is closed completely)
      2. a fresh, unused proxy is written
      3. the account is started again with the new IP

    The delay between the stop and the start is what keeps Telegram from
    seeing the same auth key on two IPs.

    Returns {"ok", "rotated", "failed", "details"}.
    """
    from villain_ads_bot.core import proxy as proxy_tool
    from villain_ads_bot.data import database
    from villain_ads_bot.services import userbot_manager

    result = {"ok": True, "rotated": 0, "failed": 0, "details": []}

    free = get_unused_proxies()
    if not free:
        result["ok"] = False
        result["details"].append("No unused proxy is available in the pool.")
        return result

    for sid in session_ids or []:
        try:
            sess = database.get_session(sid)
            if not sess:
                continue

            # 1. stop the account completely
            was_running = userbot_manager.is_bot_running(sid)
            if was_running:
                await userbot_manager.stop_userbot(sid)
                await asyncio.sleep(RECONNECT_DELAY)

            # 2. a fresh proxy
            if not free:
                result["failed"] += 1
                result["details"].append(f"{sid}: no free proxy left")
                continue
            proxy = free.pop(0)
            sess["proxy"] = proxy
            sess["proxy_auto"] = False
            database.save_session(sess)
            result["rotated"] += 1
            result["details"].append(f"{sid} -> {proxy_tool.redact(proxy)}")

            # 3. start again on the new IP
            if restart and was_running:
                await asyncio.sleep(1)
                await userbot_manager.start_userbot(sid)

        except Exception as e:
            result["failed"] += 1
            result["details"].append(f"{sid}: {e}")
            logger.warning(f"Could not rotate the proxy of {sid}: {e}")

    return result
