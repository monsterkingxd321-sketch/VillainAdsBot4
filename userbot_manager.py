import os
import glob
import logging
import asyncio
from typing import Dict, Optional
import database
import config
from userbot import UserBot

logger = logging.getLogger(__name__)

# Dictionary containing active running UserBot instances
_running_bots: Dict[str, UserBot] = {}

def find_running_bot(identifier: str) -> Optional[UserBot]:
    """
    Finds a running UserBot instance by session_id, phone number, or alias.
    Handles matching with or without '+' prefix and database lookup fallbacks.
    """
    if not identifier:
        return None
    ident_str = str(identifier).strip()
    
    # 1. Direct dictionary key match
    if ident_str in _running_bots:
        return _running_bots[ident_str]
        
    # 2. Match with/without '+' prefix
    alt = ident_str.lstrip("+") if ident_str.startswith("+") else f"+{ident_str}"
    if alt in _running_bots:
        return _running_bots[alt]
        
    # 3. Search active bots by attributes (session_id, phone)
    for bot in list(_running_bots.values()):
        if getattr(bot, "session_id", None) in (ident_str, alt):
            return bot
        bot_phone = getattr(bot, "phone", None)
        if bot_phone and bot_phone in (ident_str, alt):
            return bot
            
    # 4. Fallback search via database session record
    try:
        sess = database.get_session(ident_str)
        if sess:
            s_id = sess.get("session_id")
            p_num = sess.get("phone")
            for candidate in (s_id, p_num):
                if candidate:
                    c_str = str(candidate).strip()
                    c_alt = c_str.lstrip("+") if c_str.startswith("+") else f"+{c_str}"
                    if c_str in _running_bots:
                        return _running_bots[c_str]
                    if c_alt in _running_bots:
                        return _running_bots[c_alt]
                    for bot in list(_running_bots.values()):
                        if getattr(bot, "session_id", None) in (c_str, c_alt):
                            return bot
                        if getattr(bot, "phone", None) in (c_str, c_alt):
                            return bot
    except Exception:
        pass
        
    return None

def can_start_more_bots() -> bool:
    """
    Returns True if starting another userbot would not exceed the concurrent limit.
    """
    max_running = getattr(config, "MAX_RUNNING_USERBOTS", 99999)
    active_count = len([b for b in set(_running_bots.values()) if b.is_running])
    return True

async def start_userbot(session_id: str) -> bool:
    """
    Starts a UserBot instance if not already running.
    """
    existing_bot = find_running_bot(session_id)
    if existing_bot and existing_bot.is_running:
        return True
            
    # Check concurrent limit to prevent OOM
    if not can_start_more_bots():
        logger.warning(f"Limit of active userbots reached ({config.MAX_RUNNING_USERBOTS}). Cannot start userbot {session_id}.")
        return False
            
    bot = UserBot(session_id)
    success = await bot.start()
    if success:
        _running_bots[session_id] = bot
        # Register aliases to ensure robust lookups
        try:
            sess = database.get_session(session_id)
            if sess:
                phone = sess.get("phone")
                db_sid = sess.get("session_id")
                if phone and phone not in _running_bots:
                    _running_bots[phone] = bot
                if db_sid and db_sid not in _running_bots:
                    _running_bots[db_sid] = bot
        except Exception:
            pass
        return True
    return False

async def stop_userbot(session_id: str):
    """
    Stops a running UserBot instance.
    """
    bot = find_running_bot(session_id)
    if bot:
        for k in list(_running_bots.keys()):
            if _running_bots[k] is bot:
                _running_bots.pop(k, None)
        await bot.stop()
    elif session_id in _running_bots:
        bot = _running_bots.pop(session_id)
        await bot.stop()

def is_bot_running(session_id: str) -> bool:
    """
    Returns True if the UserBot is currently active in memory.
    """
    bot = find_running_bot(session_id)
    if bot and bot.is_running:
        return True
    return False

async def start_all_running_bots():
    """
    Resumes all UserBots that are marked as 'running' in the database (e.g. after a manager reboot).
    """
    logger.info("Resuming previously active userbots...")
    sessions = database.get_sessions()
    
    running_count = 0
    for s in sessions:
        if s.get("status") in ("running", "authorized", "active", "started"):
            session_id = s["session_id"]
            try:
                # Check limit before trying to start
                if not can_start_more_bots():
                    logger.warning(f"Resuming aborted for userbot {session_id} because the limit of {config.MAX_RUNNING_USERBOTS} active bots was reached.")
                    s["status"] = "stopped"
                    database.save_session(s)
                    continue

                # Start userbots sequentially with a small delay to avoid network/loop congestion
                success = await start_userbot(session_id)
                if success:
                    running_count += 1
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.error(f"Error resuming userbot {session_id} on startup: {e}")
                
    logger.info(f"Resumed {running_count} userbot(s).")

async def remove_userbot(session_id: str):
    """
    Logs out the userbot session from Telegram servers (so the session is expired from active devices),
    stops the userbot, deletes its session file from disk, and removes its database entry.
    """
    if not session_id:
        return

    # 1. If currently running, call log_out() to invalidate Telegram cloud auth key
    bot = find_running_bot(session_id)
    logged_out = False
    if bot and getattr(bot, "client", None):
        try:
            if not bot.client.is_connected():
                await bot.client.connect()
            if await bot.client.is_user_authorized():
                await bot.client.log_out()
                logged_out = True
                logger.info(f"Running userbot {session_id} successfully logged out from Telegram.")
        except Exception as e:
            logger.warning(f"Error logging out running userbot {session_id}: {e}")

    # 2. Retrieve session record to locate session file
    sess = database.get_session(session_id)
    session_file = None
    if sess:
        session_file = sess.get("session_file")
        if not session_file:
            user_id = sess.get("user_id", "")
            phone = sess.get("phone", session_id)
            session_file = f"{config.USER_DATA_DIR}/{user_id}/sessions/{phone}.session"

    # If offline and not logged out yet, connect temporary client to call log_out()
    if not logged_out and session_file:
        raw_base = session_file.replace(".session", "")
        file_to_check = raw_base + ".session"
        if os.path.exists(file_to_check):
            try:
                from telethon import TelegramClient
                dev_prof = utils.get_device_profile(session_id)
                api_id, api_hash = config.get_random_api_id_hash()
                temp_client = TelegramClient(
                    raw_base,
                    api_id,
                    api_hash,
                    device_model=dev_prof["device_model"],
                    system_version=dev_prof["system_version"],
                    app_version=dev_prof["app_version"]
                )
                await temp_client.connect()
                if await temp_client.is_user_authorized():
                    await temp_client.log_out()
                    logger.info(f"Offline userbot {session_id} successfully logged out from Telegram.")
                else:
                    await temp_client.disconnect()
            except Exception as e:
                logger.warning(f"Could not log out offline userbot {session_id}: {e}")

    # 3. Stop running instance and clear memory references
    await stop_userbot(session_id)
    
    # 4. Delete DB record
    database.delete_session(session_id)
    if sess and sess.get("phone") and sess.get("phone") != session_id:
        database.delete_session(sess["phone"])
    
    # 5. Delete file(s) from disk
    if session_file:
        raw_prefix = session_file.replace(".session", "")
        for pattern in (raw_prefix + "*", session_file + "*"):
            for f in glob.glob(pattern):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                        logger.info(f"Deleted local session file: {f}")
                except Exception as e:
                    logger.warning(f"Could not delete session file {f}: {e}")
                
    logger.info(f"Userbot session {session_id} completely removed and session expired.")

async def stop_all_bots():
    """
    Stops all currently running userbots.
    """
    logger.info("Stopping all active userbots...")
    session_ids = list(_running_bots.keys())
    for s_id in session_ids:
        await stop_userbot(s_id)
    logger.info("All userbots stopped.")

async def clone_profile(session_id: str, target: str, clone_type: str = "complete", fallback_client=None) -> tuple:
    """
    Clones target profile (first_name, last_name, about/bio, and profile photos/videos) 
    to the userbot instance associated with session_id based on clone_type.
    Supports usernames, invite links, and numeric Telegram user IDs.
    """
    bot = find_running_bot(session_id)
    if not bot or not bot.is_running:
        return False, "Userbot is not running. Please start it first."
    client = bot.client
    if not client or not client.is_connected():
        return False, "Userbot client is not connected."
        
    try:
        from telethon.tl.functions.users import GetFullUserRequest
        from telethon.tl.functions.account import UpdateProfileRequest
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
        from telethon.tl.types import PeerUser
        import re
        
        # Clean target input
        target_str = (str(target) or "").strip()
        target_str = re.sub(r'<[^>]+>', '', target_str).strip().strip('\'"`()[]{}<> \t\n\r')
        target_str = re.sub(r'^(?:https?://)?(?:t\.me|telegram\.(?:me|dog|org))/', '', target_str)
        target_str = re.sub(r'^tg://user\?id=', '', target_str)
        
        if not target_str:
            return False, "Target cannot be empty."
            
        # Backup original profile details if not already backed up
        sess_data = database.get_session(session_id)
        if sess_data and "original_first_name" not in sess_data:
            try:
                me = await client.get_me()
                me_full = await client(GetFullUserRequest(me))
                me_bio = me_full.full_user.about or ""
                
                os.makedirs("user_data", exist_ok=True)
                orig_photo_path = f"user_data/original_photo_{session_id}.jpg"
                downloaded_orig = await client.download_profile_photo(me, file=orig_photo_path)
                
                sess_data["original_first_name"] = me.first_name or ""
                sess_data["original_last_name"] = me.last_name or ""
                sess_data["original_about"] = me_bio
                sess_data["has_original_photo"] = bool(downloaded_orig)
                database.save_session(sess_data)
            except Exception as backup_err:
                logger.warning(f"Failed to backup original profile for {session_id}: {backup_err}")
                
        # Resolve target entity across multiple clients and strategies
        entity = None
        source_client = client
        
        if target_str.isdigit() or (target_str.startswith("-") and target_str[1:].isdigit()):
            uid = int(target_str)
            # Try userbot client first
            try:
                entity = await client.get_entity(uid)
            except Exception:
                try:
                    entity = await client.get_entity(PeerUser(uid))
                except Exception:
                    pass
            
            # Check database for known username
            if not entity:
                db_u = database.get_user(uid)
                if db_u and db_u.get("username"):
                    try:
                        entity = await client.get_entity(db_u["username"])
                    except Exception:
                        pass
                        
            # Search userbot dialogs
            if not entity:
                try:
                    async for dialog in client.iter_dialogs(limit=200):
                        if dialog.id == uid:
                            entity = dialog.entity
                            break
                except Exception:
                    pass
                    
            # Fallback to main bot client if userbot doesn't have the entity in cache
            if not entity and fallback_client:
                try:
                    entity = await fallback_client.get_entity(uid)
                    source_client = fallback_client
                except Exception as fb_err:
                    logger.debug(f"Fallback client failed to get entity {uid}: {fb_err}")
        else:
            username = target_str.replace('@', '').strip()
            try:
                entity = await client.get_entity(username)
            except Exception:
                try:
                    entity = await client.get_entity(f"@{username}")
                except Exception:
                    if fallback_client:
                        try:
                            entity = await fallback_client.get_entity(username)
                            source_client = fallback_client
                        except Exception:
                            pass
                            
        if not entity:
            return False, f"Could not find target '{target_str}'. Please verify username or user ID."
            
        # Get full user details (including bio)
        try:
            full_user = await source_client(GetFullUserRequest(entity))
        except Exception as e_full:
            # If failed on source_client, try fallback
            if fallback_client and source_client != fallback_client:
                try:
                    full_user = await fallback_client(GetFullUserRequest(entity))
                    source_client = fallback_client
                except Exception:
                    return False, f"Could not fetch full user details: {e_full}"
            else:
                return False, f"Could not fetch full user details: {e_full}"
                
        user = full_user.users[0]
        bio = (full_user.full_user.about or "")[:70]
        first_name = (user.first_name or "")[:64]
        last_name = (user.last_name or "")[:64]
            
        # Download and clone target's profile photo(s) & video(s)
        if clone_type in ("photo", "complete"):
            try:
                photos = await source_client.get_profile_photos(entity, limit=10)
                if photos:
                    # Upload in reverse order so the main (newest) avatar is uploaded last and stays on top
                    for idx, p in enumerate(reversed(photos)):
                        has_video = bool(getattr(p, 'video_sizes', None))
                        ext = ".mp4" if has_video else ".jpg"
                        temp_path = f"user_data/clone_{session_id}_{idx}{ext}"
                        try:
                            dl_path = await source_client.download_media(p, file=temp_path)
                            if dl_path and os.path.exists(dl_path):
                                uploaded = await client.upload_file(dl_path)
                                if has_video:
                                    await client(UploadProfilePhotoRequest(video=uploaded, video_start_ts=0.0))
                                else:
                                    await client(UploadProfilePhotoRequest(file=uploaded))
                        except Exception as up_err:
                            logger.warning(f"Failed to clone avatar photo/video {idx}: {up_err}")
                        finally:
                            if os.path.exists(temp_path):
                                try:
                                    os.remove(temp_path)
                                except Exception:
                                    pass
                        await asyncio.sleep(0.8)
                else:
                    # Single photo fallback
                    temp_path = f"user_data/temp_clone_{session_id}.jpg"
                    try:
                        dl_path = await source_client.download_profile_photo(entity, file=temp_path)
                        if dl_path and os.path.exists(dl_path):
                            uploaded = await client.upload_file(dl_path)
                            await client(UploadProfilePhotoRequest(file=uploaded))
                    except Exception as single_err:
                        logger.warning(f"Failed single avatar download: {single_err}")
                    finally:
                        if os.path.exists(temp_path):
                            try:
                                os.remove(temp_path)
                            except Exception:
                                pass
            except Exception as e_media:
                logger.warning(f"Error during profile photo/video cloning: {e_media}")
            
        # Update name and bio based on clone_type
        update_args = {}
        if clone_type in ("name", "complete"):
            update_args["first_name"] = first_name
            update_args["last_name"] = last_name
        if clone_type in ("bio", "complete"):
            update_args["about"] = bio
            
        if update_args:
            await client(UpdateProfileRequest(**update_args))
                    
        # Update session info in database if name was changed
        if clone_type in ("name", "complete"):
            sess_data = database.get_session(session_id)
            if sess_data:
                sess_data["name"] = f"{first_name} {last_name}".strip()
                database.save_session(sess_data)
                bot.name = sess_data["name"]
            
        return True, f"Successfully cloned profile ({clone_type}) of {first_name} (@{user.username or 'None'})!"
    except Exception as e:
        logger.error(f"Error cloning profile: {e}")
        return False, f"Error: {e}"

async def set_userbot_name(session_id: str, new_name: str) -> tuple[bool, str]:
    """
    Updates the userbot's name both in the database and directly on the Telegram account profile.
    """
    if not new_name or not new_name.strip():
        return False, "Name cannot be empty."
        
    new_name = new_name.strip()
    sess = database.get_session(session_id)
    if not sess:
        return False, "Session not found."
        
    sess["name"] = new_name
    sess["original_name"] = new_name
    database.save_session(sess)
    
    # If running, update profile directly
    bot_obj = find_running_bot(session_id)
    if bot_obj and bot_obj.is_running:
        client = bot_obj.client
        if client and client.is_connected():
            try:
                from telethon.tl.functions.account import UpdateProfileRequest
                
                # Check branding settings
                global_settings = database.get_global_settings()
                brand_name_enabled = global_settings.get("branding_name_enabled", True)
                brand_name_text = global_settings.get("branding_name_text")
                brand_username = global_settings.get("branding_username")
                
                name_suffix = brand_name_text if brand_name_text else (f" via @{brand_username}" if brand_username else "")
                
                final_first_name = new_name
                if brand_name_enabled and name_suffix and name_suffix not in new_name:
                    final_first_name = (new_name + name_suffix)[:64]
                    
                await client(UpdateProfileRequest(first_name=final_first_name))
                bot_obj.name = new_name
                return True, f"Name updated to: <b>{new_name}</b>"
            except Exception as e:
                logger.error(f"Failed to update profile on Telegram for {session_id}: {e}")
                return False, f"Updated in database, but Telegram error: {e}"
                
    # If not running, attempt update via a temporary client if session file exists
    user_id = sess.get("user_id", "")
    session_file = f"{config.USER_DATA_DIR}/{user_id}/sessions/{session_id}.session"
    if os.path.exists(session_file):
        try:
            from telethon.tl.functions.account import UpdateProfileRequest
            dev_prof = utils.get_device_profile(session_id)
            api_id, api_hash = config.get_random_api_id_hash()
            temp_client = TelegramClient(
                session_file, 
                api_id, 
                api_hash,
                device_model=dev_prof["device_model"],
                system_version=dev_prof["system_version"],
                app_version=dev_prof["app_version"]
            )
            await temp_client.connect()
            if await temp_client.is_user_authorized():
                await temp_client(UpdateProfileRequest(first_name=new_name))
                await temp_client.disconnect()
                return True, f"Name updated to: <b>{new_name}</b>"
            else:
                await temp_client.disconnect()
        except Exception as e:
            logger.warning(f"Could not update offline bot profile via temp client: {e}")
            
    return True, f"Name updated to: <b>{new_name}</b>"

async def restore_original_profile(session_id: str) -> tuple:
    """
    Restores the userbot's original profile (first_name, last_name, about/bio, and photo).
    """
    bot = find_running_bot(session_id)
    if not bot or not bot.is_running:
        return False, "Userbot is not running. Please start it first."
    client = bot.client
    if not client or not client.is_connected():
        return False, "Userbot client is not connected."
        
    sess_data = database.get_session(session_id)
    if not sess_data or "original_first_name" not in sess_data:
        return False, "No original profile backup found. You must clone a profile first."
        
    try:
        from telethon.tl.functions.account import UpdateProfileRequest
        from telethon.tl.functions.photos import UploadProfilePhotoRequest
        
        orig_first = sess_data.get("original_first_name", "")
        orig_last = sess_data.get("original_last_name", "")
        orig_bio = sess_data.get("original_about", "")
        
        # Restore name and bio
        await client(UpdateProfileRequest(
            first_name=orig_first,
            last_name=orig_last,
            about=orig_bio
        ))
        
        # Restore photo if it exists
        orig_photo_path = f"user_data/original_photo_{session_id}.jpg"
        if sess_data.get("has_original_photo") and os.path.exists(orig_photo_path):
            try:
                uploaded = await client.upload_file(orig_photo_path)
                await client(UploadProfilePhotoRequest(file=uploaded))
            except Exception as e:
                logger.warning(f"Failed to restore original profile photo: {e}")
        else:
            # If they didn't have a photo originally, remove current profile photo
            try:
                from telethon.tl.functions.photos import DeletePhotosRequest
                photos = await client.get_profile_photos('me')
                if photos:
                    await client(DeletePhotosRequest(id=[photos[0]]))
            except Exception as e:
                logger.warning(f"Failed to remove cloned profile photo: {e}")
                
        # Update session details
        sess_data["name"] = f"{orig_first} {orig_last}".strip()
        
        # Clean backup fields from DB
        sess_data.pop("original_first_name", None)
        sess_data.pop("original_last_name", None)
        sess_data.pop("original_about", None)
        sess_data.pop("has_original_photo", None)
        database.save_session(sess_data)
        
        # Clean up local backup file
        if os.path.exists(orig_photo_path):
            try:
                os.remove(orig_photo_path)
            except Exception:
                pass
                
        return True, "Successfully restored original profile details!"
    except Exception as e:
        logger.error(f"Error restoring original profile: {e}")
        return False, f"Error: {e}"

def reload_bot_settings(session_id: str):
    """
    Reloads the in-memory settings of a running userbot.
    """
    bot = find_running_bot(session_id)
    if bot:
        bot.reload_settings()
        logger.info(f"Reloaded settings for running userbot: {session_id}")
    else:
        logger.debug(f"Userbot {session_id} not running to reload settings.")

def get_running_bot(session_id: str) -> Optional[UserBot]:
    """
    Returns the running UserBot instance for a session_id if it exists.
    """
    return find_running_bot(session_id)

async def broadcast_branding_update():
    """
    Applies or removes branding for all running userbots based on the latest global settings.
    """
    global_settings = database.get_global_settings()
    branding_username = global_settings.get("branding_username")
    
    from userbot import apply_branding
    
    all_sessions = database.get_sessions()
    for s in all_sessions:
        phone = s.get("phone")
        if not phone: continue
        
        bot = find_running_bot(phone)
        if bot and bot.client and bot.client.is_connected():
            try:
                # apply_branding handles both enabling and disabling because it reads from global_settings
                await apply_branding(bot.client, branding_username, s)
            except Exception as e:
                logger.error(f"Failed to broadcast branding to {phone}: {e}")
