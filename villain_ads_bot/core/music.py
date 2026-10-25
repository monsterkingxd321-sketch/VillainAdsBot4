"""
Music gateway configuration.

`villain_ads_bot/services/youtube.py` is kept EXACTLY as the API owner
publishes it - this module is the only place that pushes a runtime
configuration (API key / gateway url) into it.

The values configured in the admin panel win over the `.env` defaults.
"""

import logging

logger = logging.getLogger(__name__)


def get_configured_credentials() -> tuple:
    """
    Returns the (url, key, backup_url) that should be used.

    Priority: admin panel (MongoDB) -> .env / environment -> defaults.
    """
    url = "https://api.shrutibots.in"
    key = None
    backup = "https://music.yukiapi.site"

    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        url = (settings.get("music_api_url") or url).strip()
        key = (settings.get("music_api_key") or "").strip() or None
        backup = (settings.get("music_backup_api_url") or backup).strip()
    except Exception as e:
        logger.debug(f"Could not read the music settings: {e}")

    if not key:
        try:
            import os
            key = (os.environ.get("SHRUTI_API_KEY") or os.environ.get("MEOW_API_KEY") or "").strip() or "ShrutiBotsdvUpVh4n66hhD1iQ5fm0"
        except Exception:
            key = "ShrutiBotsdvUpVh4n66hhD1iQ5fm0"

    return url.rstrip("/"), key, backup.rstrip("/")


def apply_configured_credentials() -> tuple:
    """
    Pushes the configured credentials into the youtube module.

    Returns the (url, key, backup_url) that are now in use.
    """
    url, key, backup = get_configured_credentials()
    try:
        from villain_ads_bot.services import youtube as yt
        if url:
            yt.API_URL = url
        if key:
            yt.API_KEY = key
    except Exception as e:
        logger.warning(f"Could not apply the music credentials: {e}")
    return url, key, backup


def mask_key(key) -> str:
    """Masks a key for display purposes."""
    if not key:
        return "Not set"
    key = str(key)
    if len(key) <= 14:
        return key
    return f"{key[:10]}{'•' * 6}{key[-4:]}"


async def download_with_failover(kind: str, link: str) -> str:
    """
    Downloads a track from the music gateway.

    The primary gateway is tried first, then the backup gateway (high
    availability). The `youtube` module itself is never modified - only the
    gateway url that it reads is switched for the duration of the call.
    """
    import os

    primary, _key, backup = apply_configured_credentials()
    gateways = [primary]
    if backup and backup != primary:
        gateways.append(backup)

    try:
        from villain_ads_bot.services import youtube as yt
    except Exception:
        return None

    fn = yt.download_video if kind == "video" else yt.download_song

    for gateway in gateways:
        try:
            yt.API_URL = gateway
            result = await fn(link)
            if result and os.path.exists(result):
                return result
        except Exception as e:
            logger.warning(f"Music gateway {gateway} failed: {e}")

    # Always restore the primary gateway.
    try:
        yt.API_URL = primary
    except Exception:
        pass
    return None
