"""
One-click backup & restore of the whole bot state.

A backup is a single ZIP that contains:

    villain_backup_2026-10-21_1430.zip
    ├── manifest.json        version, created_at, counts
    ├── global_settings.json branding, pricing, plans, music key, roles
    ├── users.json           every user with wallet + subscriptions
    ├── sessions.json        every session record
    ├── coupons.json         every coupon
    └── sessions/*.session   the real Telethon session files

Secrets that could hijack the bot (``bot_session_str`` / ``gpt_api_key``)
are never written into a backup.
"""

import io
import json
import logging
import os
import time
import zipfile
from datetime import datetime

logger = logging.getLogger(__name__)

# Never exported - a leaked backup must not be able to take over the bot.
REDACTED_KEYS = {"bot_session_str", "gpt_api_key"}


def _jsonable(data):
    """Converts Mongo documents into JSON serialisable structures."""
    if isinstance(data, dict):
        return {k: _jsonable(v) for k, v in data.items() if k not in REDACTED_KEYS}
    if isinstance(data, (list, tuple)):
        return [_jsonable(v) for v in data]
    if isinstance(data, (str, int, float, bool)) or data is None:
        return data
    if isinstance(data, bytes):
        return None
    # bson ObjectId / datetime etc.
    return str(data)


def build_backup(include_session_files: bool = True) -> bytes:
    """
    Builds the backup archive and returns its bytes.
    """
    from villain_ads_bot.data import database
    from villain_ads_bot import config

    settings = database.get_global_settings() or {}
    users = database.get_all_users()
    sessions = database.get_sessions()
    coupons = database.get_coupons()

    created = datetime.now()
    stamp = created.strftime("%Y-%m-%d_%H%M")

    manifest = {
        "format": "villain-ads-bot-backup",
        "version": 1,
        "created_at": created.isoformat(),
        "created_ts": time.time(),
        "bot_version": getattr(config, "__version__", None) or "4.1.0",
        "counts": {
            "users": len(users),
            "sessions": len(sessions),
            "coupons": len(coupons),
        },
        "includes_session_files": bool(include_session_files),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        zf.writestr("global_settings.json",
                    json.dumps(_jsonable(settings), indent=2, ensure_ascii=False))
        zf.writestr("users.json",
                    json.dumps(_jsonable(users), indent=2, ensure_ascii=False))
        zf.writestr("sessions.json",
                    json.dumps(_jsonable(sessions), indent=2, ensure_ascii=False))
        zf.writestr("coupons.json",
                    json.dumps(_jsonable(coupons), indent=2, ensure_ascii=False))

        if include_session_files:
            for sess in sessions:
                path = sess.get("session_file")
                if not path or not os.path.exists(path):
                    continue
                try:
                    name = os.path.basename(path)
                    if not name.endswith(".session"):
                        name += ".session"
                    with open(path, "rb") as fh:
                        zf.writestr(f"sessions/{name}", fh.read())
                except Exception as e:
                    logger.warning(f"Could not add {path} to the backup: {e}")

    return buffer.getvalue()


def inspect_backup(data: bytes) -> dict:
    """
    Reads a backup archive WITHOUT restoring it and returns a preview.
    """
    preview = {"ok": False, "error": None, "manifest": None, "counts": {}}
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            names = zf.namelist()
            if "manifest.json" in names:
                preview["manifest"] = json.loads(zf.read("manifest.json").decode("utf-8"))
            for key in ("users", "sessions", "coupons"):
                if f"{key}.json" in names:
                    try:
                        preview["counts"][key] = len(json.loads(zf.read(f"{key}.json").decode("utf-8")))
                    except Exception:
                        preview["counts"][key] = 0
            preview["counts"]["session_files"] = len([n for n in names if n.startswith("sessions/")])
            preview["counts"]["has_settings"] = "global_settings.json" in names
            preview["ok"] = preview["manifest"] is not None
            if not preview["ok"]:
                preview["error"] = "This file is not a valid bot backup."
    except Exception as e:
        preview["error"] = f"Could not read the backup: {e}"
    return preview


def restore_backup(data: bytes, restore_users: bool = True, restore_sessions: bool = True,
                   restore_settings: bool = True, restore_coupons: bool = True,
                   write_session_files: bool = True) -> dict:
    """
    Restores a backup archive.

    Returns a dict with the counters of what was restored.
    """
    from villain_ads_bot.data import database
    from villain_ads_bot import config

    result = {"ok": False, "error": None, "users": 0, "sessions": 0,
              "coupons": 0, "settings": False, "session_files": 0}

    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            names = zf.namelist()

            if "global_settings.json" in names and restore_settings:
                data_obj = json.loads(zf.read("global_settings.json").decode("utf-8"))
                data_obj.pop("id", None)
                data_obj.pop("_id", None)
                for key in REDACTED_KEYS:
                    data_obj.pop(key, None)
                current = database.get_global_settings() or {}
                current.update(data_obj)
                current["id"] = "global"
                database.save_global_settings(current)
                result["settings"] = True

            if "users.json" in names and restore_users:
                for user in json.loads(zf.read("users.json").decode("utf-8")):
                    user.pop("_id", None)
                    try:
                        database.save_user(user)
                        result["users"] += 1
                    except Exception as e:
                        logger.warning(f"Could not restore a user: {e}")

            if "sessions.json" in names and restore_sessions:
                for sess in json.loads(zf.read("sessions.json").decode("utf-8")):
                    sess.pop("_id", None)
                    try:
                        database.save_session(sess)
                        result["sessions"] += 1
                    except Exception as e:
                        logger.warning(f"Could not restore a session: {e}")

            if "coupons.json" in names and restore_coupons:
                for coupon in json.loads(zf.read("coupons.json").decode("utf-8")):
                    coupon.pop("_id", None)
                    try:
                        database.save_coupon(coupon)
                        result["coupons"] += 1
                    except Exception as e:
                        logger.warning(f"Could not restore a coupon: {e}")

            if write_session_files and restore_sessions:
                for name in names:
                    if not name.startswith("sessions/") or not name.endswith(".session"):
                        continue
                    phone = os.path.basename(name)[:-len(".session")]
                    # The owner folder is unknown -> put it into a restore folder
                    # and let the admin move it if needed.
                    target_dir = os.path.join(config.USER_DATA_DIR, "restored", "sessions")
                    os.makedirs(target_dir, exist_ok=True)
                    with open(os.path.join(target_dir, os.path.basename(name)), "wb") as fh:
                        fh.write(zf.read(name))
                    result["session_files"] += 1

        result["ok"] = True
    except Exception as e:
        result["error"] = str(e)
        logger.error(f"Restore failed: {e}")
    return result


def backup_filename() -> str:
    return f"villain_backup_{datetime.now().strftime('%Y-%m-%d_%H%M')}.zip"
