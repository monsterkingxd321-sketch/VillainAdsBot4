import os
import glob
import logging
import asyncio
from typing import Dict, Optional, Any, List, Union
from telethon import TelegramClient
from villain_ads_bot.data import database
from villain_ads_bot import config
from villain_ads_bot.core import utils
from villain_ads_bot.services.userbot import UserBot

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
    Before that, every session file found on disk is registered so that accounts
    are never lost (e.g. after a database wipe).
    """
    logger.info("Resuming previously active userbots...")
    try:
        imported, skipped, _records = import_sessions_from_disk()
        if imported:
            logger.info(f"Registered {imported} account(s) discovered in the session files on disk.")
    except Exception as e:
        logger.warning(f"Could not import sessions from disk on startup: {e}")
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

    # 1. Disconnect running client cleanly without invalidating Telegram account
    bot = find_running_bot(session_id)
    if bot and getattr(bot, "client", None):
        try:
            if bot.client.is_connected():
                await bot.client.disconnect()
        except Exception as e:
            logger.debug(f"Error disconnecting running userbot {session_id}: {e}")

    # 2. Retrieve session record to locate session file
    sess = database.get_session(session_id)
    session_file = None
    if sess:
        session_file = sess.get("session_file")
        if not session_file:
            user_id = sess.get("user_id", "")
            phone = sess.get("phone", session_id)
            session_file = f"{config.USER_DATA_DIR}/{user_id}/sessions/{phone}.session"

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

async def clone_profile(session_id: str, target: Any, clone_type: str = "complete", fallback_client=None, target_entity=None) -> tuple:
    """
    Clones target profile (first_name, last_name, about/bio, and profile photos/videos) 
    to the userbot instance associated with session_id based on clone_type.
    Supports usernames, tg://openmessage?user_id= links, tg://user?id= links, numeric user IDs, and direct User entities.
    Supports both static photos and animated video profile avatars.
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
        
        # 1. Parse target input
        target_uid = None
        target_username = None
        target_str = ""
        entity = None
        source_client = client
        
        if target_entity:
            entity = target_entity
            source_client = fallback_client or client
        elif hasattr(target, 'id') and not isinstance(target, (str, int)):
            entity = target
            source_client = fallback_client or client
        else:
            target_str = (str(target) or "").strip()
            target_str = re.sub(r'<[^>]+>', '', target_str).strip().strip('\'"`()[]{}<> \t\n\r')
            
            # Extract user_id from query strings like tg://openmessage?user_id=123, tg://user?id=123, t.me/openmessage?user_id=123
            uid_match = re.search(r'(?:user_id|user\?id|id)\s*[:=]\s*(\d+)', target_str, re.IGNORECASE)
            if uid_match:
                target_uid = int(uid_match.group(1))
            else:
                clean_num = target_str.lstrip('+')
                if clean_num.isdigit() or (clean_num.startswith('-') and clean_num[1:].isdigit()):
                    target_uid = int(clean_num)
                else:
                    # Clean username patterns: t.me/..., tg://..., @...
                    clean_un = re.sub(r'^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.(?:me|dog|org))/', '', target_str)
                    clean_un = re.sub(r'^tg://(?:resolve\?domain=)?', '', clean_un)
                    clean_un = clean_un.lstrip('@').strip().split('/')[0].split('?')[0].split('&')[0]
                    if clean_un.isdigit():
                        target_uid = int(clean_un)
                    elif clean_un:
                        target_username = clean_un
                        
            if not target_uid and not target_username:
                return False, "Target cannot be empty. Please enter a valid username, user ID, or link."
            
        # Backup original profile details if not already backed up
        sess_data = database.get_session(session_id)
        if sess_data and "original_first_name" not in sess_data:
            try:
                me = await client.get_me()
                me_full = await client(GetFullUserRequest(me))
                me_bio = me_full.full_user.about or ""
                
                os.makedirs(config.USER_DATA_DIR, exist_ok=True)
                orig_path_base = os.path.join(config.USER_DATA_DIR, f"original_avatar_{session_id}")
                downloaded_orig = None
                if getattr(me, 'photo', None):
                    try:
                        downloaded_orig = await client.download_media(me.photo, file=orig_path_base)
                    except Exception:
                        pass
                if not downloaded_orig or not os.path.exists(downloaded_orig):
                    try:
                        downloaded_orig = await client.download_profile_photo(me, file=f"{orig_path_base}.jpg")
                    except Exception:
                        pass
                
                sess_data["original_first_name"] = me.first_name or ""
                sess_data["original_last_name"] = me.last_name or ""
                sess_data["original_about"] = me_bio
                sess_data["has_original_photo"] = bool(downloaded_orig and os.path.exists(downloaded_orig))
                if downloaded_orig and os.path.exists(downloaded_orig):
                    sess_data["original_photo_path"] = downloaded_orig
                database.save_session(sess_data)
            except Exception as backup_err:
                logger.warning(f"Failed to backup original profile for {session_id}: {backup_err}")
                
        # 2. Resolve target entity across multiple clients and strategies
        if not entity:
            if target_uid:
                # Strategy A: Try the MANAGER BOT first.
                # The bot knows every user that ever pressed /start, and its
                # entity cache contains the access_hash that is required to
                # resolve a numeric user id.
                if fallback_client:
                    try:
                        fb_ent = await fallback_client.get_entity(target_uid)
                        if fb_ent:
                            # If the target has a username, resolve it on the
                            # userbot client so the profile data comes from a
                            # user account (bots get BOT_METHOD_INVALID).
                            if getattr(fb_ent, 'username', None):
                                try:
                                    entity = await client.get_entity(fb_ent.username)
                                except Exception:
                                    pass
                            if not entity:
                                entity = fb_ent
                                source_client = fallback_client
                    except Exception as fb_err:
                        logger.debug(f"Fallback client failed for user ID {target_uid}: {fb_err}")

                # Strategy A2: The manager bot's DIALOGS - the bot has a
                # conversation with every user that ever used the bot, so the
                # numeric id can be resolved here even when the entity cache
                # has no entry for it.
                if not entity and fallback_client:
                    try:
                        async for dialog in fallback_client.iter_dialogs(limit=500):
                            if dialog.id == target_uid:
                                entity = dialog.entity
                                source_client = fallback_client
                                if getattr(entity, 'username', None):
                                    try:
                                        entity = await client.get_entity(entity.username)
                                        source_client = client
                                    except Exception:
                                        pass
                                break
                    except Exception as dlg_err:
                        logger.debug(f"Bot dialog scan failed for user ID {target_uid}: {dlg_err}")

                # Strategy B: Try the userbot client directly
                if not entity:
                    try:
                        entity = await client.get_entity(target_uid)
                    except Exception:
                        try:
                            entity = await client.get_entity(PeerUser(target_uid))
                        except Exception:
                            pass
                        
                # Strategy C: Check other running userbots
                if not entity:
                    for other_phone, other_bot in _running_bots.items():
                        if other_bot and other_bot.client and other_bot.client.is_connected() and other_bot.client != client:
                            try:
                                other_ent = await other_bot.client.get_entity(target_uid)
                                if other_ent:
                                    if getattr(other_ent, 'username', None):
                                        try:
                                            entity = await client.get_entity(other_ent.username)
                                        except Exception:
                                            pass
                                    if not entity:
                                        entity = other_ent
                                        source_client = other_bot.client
                                    break
                            except Exception:
                                pass
                                
                # Strategy D: Check database for known user record
                if not entity:
                    db_u = database.get_user(target_uid)
                    if db_u and db_u.get("username"):
                        try:
                            entity = await client.get_entity(db_u["username"])
                        except Exception:
                            pass
                            
                # Strategy E: Search userbot dialogs (direct chats)
                if not entity:
                    try:
                        async for dialog in client.iter_dialogs(limit=300):
                            if dialog.id == target_uid:
                                entity = dialog.entity
                                source_client = client
                                break
                    except Exception:
                        pass
                        
                # Strategy F: Check contacts of this client AND of every other running userbot
                if not entity:
                    from telethon.tl.functions.contacts import GetContactsRequest
                    contact_clients = [client]
                    for _p, _b in list(_running_bots.items()):
                        if _b and _b.client and _b.client.is_connected() and _b.client is not client:
                            contact_clients.append(_b.client)
                    for c_client in contact_clients:
                        try:
                            contacts = await c_client(GetContactsRequest(hash=0))
                            for u in getattr(contacts, 'users', []):
                                if u.id == target_uid:
                                    entity = u
                                    source_client = c_client
                                    break
                        except Exception:
                            pass
                        if entity:
                            break
                        
                # Strategy G: Search group participants in userbot's joined chats
                # (participants must be enumerated - Telegram's `search` filter
                #  only matches names/usernames, never numeric IDs)
                if not entity:
                    try:
                        async for dialog in client.iter_dialogs(limit=35):
                            if dialog.is_group or dialog.is_channel:
                                try:
                                    participants = await client.get_participants(dialog, limit=200)
                                    for u in (participants or []):
                                        if u.id == target_uid:
                                            entity = u
                                            source_client = client
                                            break
                                except Exception:
                                    pass
                            if entity:
                                break
                    except Exception:
                        pass
            elif target_username:
                try:
                    entity = await client.get_entity(target_username)
                except Exception:
                    try:
                        entity = await client.get_entity(f"@{target_username}")
                    except Exception:
                        if fallback_client:
                            try:
                                entity = await fallback_client.get_entity(target_username)
                                source_client = fallback_client
                            except Exception:
                                pass
                        if not entity:
                            for other_phone, other_bot in _running_bots.items():
                                if other_bot and other_bot.client and other_bot.client.is_connected() and other_bot.client != client:
                                    try:
                                        entity = await other_bot.client.get_entity(target_username)
                                        source_client = other_bot.client
                                        break
                                    except Exception:
                                        pass
                                        
        # If entity has username and source_client != client, attempt resolving on client
        if entity and getattr(entity, 'username', None) and source_client != client:
            try:
                ub_ent = await client.get_entity(entity.username)
                if ub_ent:
                    entity = ub_ent
                    source_client = client
            except Exception:
                pass
                
        if not entity:
            target_display = target_username or (f"User ID {target_uid}" if target_uid else target_str)
            return False, (
                f"Could not find <b>{target_display}</b>.\n\n"
                f"💡 <i>A numeric user id can only be resolved when the target is known to the bot "
                f"(e.g. they used /start before) or when they are in your contacts / groups.\n\n"
                f"<b>Fastest way :</b> forward any message from that person into this chat — "
                f"the profile is then cloned exactly.</i>"
            )
            
        # 3. Get full user details (including bio)
        try:
            full_user = await source_client(GetFullUserRequest(entity))
        except Exception as e_full:
            if fallback_client and source_client != fallback_client:
                try:
                    full_user = await fallback_client(GetFullUserRequest(entity))
                    source_client = fallback_client
                except Exception:
                    return False, f"Could not fetch full user details: {e_full}"
            else:
                return False, f"Could not fetch full user details: {e_full}"
                
        user = full_user.users[0] if getattr(full_user, "users", None) else None
        if not user:
            return False, "Could not read the target's profile information. Please forward a message from them instead."
        bio = (full_user.full_user.about or "")[:70]
        first_name = (user.first_name or "")[:64]
        last_name = (user.last_name or "")[:64]
            
        # 4. Download and clone target's profile photo(s) & video(s)
        media_cloned = 0
        if clone_type in ("photo", "complete"):
            os.makedirs(config.USER_DATA_DIR, exist_ok=True)
            
            # Select client capable of downloading profile media (user accounts required, bots get BOT_METHOD_INVALID)
            photo_client = source_client
            if getattr(photo_client, 'is_bot', False) or (fallback_client and photo_client == fallback_client):
                photo_client = client
                
            def _best_static_size(photo_obj):
                """Returns the largest STILL image size of a photo (never the video)."""
                try:
                    sizes = [s for s in (getattr(photo_obj, "sizes", None) or [])
                             if getattr(s, "type", "") != "i"]
                    if not sizes:
                        return None
                    def _weight(s):
                        if getattr(s, "size", None):
                            return s.size
                        return (getattr(s, "w", 0) or 0) * (getattr(s, "h", 0) or 0)
                    return max(sizes, key=_weight)
                except Exception:
                    return None
                
            photos = []
            try:
                photos = await photo_client.get_profile_photos(entity, limit=10)
            except Exception as e_p:
                logger.debug(f"Failed to fetch profile photos with photo_client: {e_p}")
                if photo_client != client:
                    try:
                        photos = await client.get_profile_photos(entity, limit=10)
                        photo_client = client
                    except Exception:
                        pass
                        
            if photos:
                # Upload in reverse order so the main (newest) avatar is uploaded last and stays on top
                for idx, p in enumerate(reversed(photos)):
                    temp_base = os.path.join(config.USER_DATA_DIR, f"clone_{session_id}_{idx}")
                    # Clean previous temp files matching this index
                    try:
                        for existing_file in os.listdir(config.USER_DATA_DIR):
                            if existing_file.startswith(f"clone_{session_id}_{idx}"):
                                try:
                                    os.remove(os.path.join(config.USER_DATA_DIR, existing_file))
                                except Exception:
                                    pass
                    except Exception:
                        pass
                        
                    dl_path = None
                    has_video = bool(getattr(p, 'video_sizes', None))
                    static_size = _best_static_size(p)
                    
                    try:
                        if has_video:
                            # Animated / video avatar -> download the MP4
                            dl_path = await photo_client.download_media(p, file=f"{temp_base}.mp4")
                        else:
                            dl_path = await photo_client.download_media(p, file=f"{temp_base}.jpg")
                            
                        if not dl_path or not os.path.exists(dl_path):
                            dl_path = await photo_client.download_media(p, file=temp_base)
                            
                        if dl_path and os.path.exists(dl_path):
                            # Verify if downloaded file is an MP4 video avatar
                            is_video = dl_path.lower().endswith(('.mp4', '.mov', '.webm', '.mkv'))
                            if not is_video:
                                try:
                                    with open(dl_path, 'rb') as f_check:
                                        if b'ftyp' in f_check.read(16):
                                            is_video = True
                                except Exception:
                                    pass
                                    
                            uploaded = await client.upload_file(dl_path)
                            if is_video:
                                try:
                                    await client(UploadProfilePhotoRequest(video=uploaded, video_start_ts=0.0))
                                    media_cloned += 1
                                    logger.info(f"Successfully cloned profile video {dl_path} for {session_id}")
                                except Exception as vid_err:
                                    logger.warning(f"Video profile upload failed: {vid_err}, falling back to static thumbnail")
                                    # Fall back to the largest STILL image of this avatar.
                                    # (Passing the concrete PhotoSize avoids picking the VideoSize again.)
                                    static_path = None
                                    try:
                                        if static_size is not None:
                                            static_path = await photo_client.download_media(
                                                p, file=f"{temp_base}_static.jpg", thumb=static_size)
                                        if not static_path or not os.path.exists(static_path):
                                            static_path = await photo_client.download_media(
                                                p, file=f"{temp_base}_static.jpg")
                                    except Exception as st_err:
                                        logger.warning(f"Static thumbnail download failed: {st_err}")
                                    
                                    if static_path and os.path.exists(static_path):
                                        try:
                                            up_thumb = await client.upload_file(static_path)
                                            await client(UploadProfilePhotoRequest(file=up_thumb))
                                            media_cloned += 1
                                        except Exception as th_err:
                                            logger.warning(f"Thumbnail fallback failed: {th_err}")
                                        finally:
                                            if os.path.exists(static_path):
                                                try:
                                                    os.remove(static_path)
                                                except Exception:
                                                    pass
                            else:
                                await client(UploadProfilePhotoRequest(file=uploaded))
                                media_cloned += 1
                                logger.info(f"Successfully cloned profile photo {dl_path} for {session_id}")
                    except Exception as up_err:
                        logger.warning(f"Failed to clone avatar photo/video {idx}: {up_err}")
                    finally:
                        if dl_path and os.path.exists(dl_path):
                            try:
                                os.remove(dl_path)
                            except Exception:
                                pass
                    await asyncio.sleep(0.8)
                    
            # Fallback single avatar download if get_profile_photos had 0 or failed
            if media_cloned == 0:
                single_base = os.path.join(config.USER_DATA_DIR, f"temp_clone_{session_id}")
                dl_single = None
                
                # 1. Try the explicit profile photo (this can be a VIDEO avatar)
                if getattr(entity, 'photo', None):
                    try:
                        dl_single = await photo_client.download_media(entity.photo, file=single_base)
                    except Exception:
                        pass
                        
                # 2. Try download_profile_photo (static JPEG only)
                if not dl_single or not os.path.exists(dl_single):
                    try:
                        dl_single = await photo_client.download_profile_photo(entity, file=f"{single_base}.jpg")
                    except Exception:
                        if photo_client != source_client:
                            try:
                                dl_single = await source_client.download_profile_photo(entity, file=f"{single_base}.jpg")
                            except Exception:
                                pass
                                
                if dl_single and os.path.exists(dl_single):
                    try:
                        is_vid = dl_single.lower().endswith(('.mp4', '.mov', '.webm', '.mkv'))
                        if not is_vid:
                            try:
                                with open(dl_single, 'rb') as sf:
                                    if b'ftyp' in sf.read(16):
                                        is_vid = True
                            except Exception:
                                pass
                                
                        uploaded = await client.upload_file(dl_single)
                        if is_vid:
                            try:
                                await client(UploadProfilePhotoRequest(video=uploaded, video_start_ts=0.0))
                                media_cloned += 1
                            except Exception as s_vid_err:
                                logger.warning(f"Single video upload failed: {s_vid_err}")
                        else:
                            await client(UploadProfilePhotoRequest(file=uploaded))
                            media_cloned += 1
                    except Exception as s_err:
                        logger.warning(f"Failed single avatar upload: {s_err}")
                    finally:
                        if os.path.exists(dl_single):
                            try:
                                os.remove(dl_single)
                            except Exception:
                                pass
            
            if media_cloned == 0:
                logger.info(f"No profile photo could be cloned for {session_id} (target may have no avatar).")
            
        # 5. Update name and bio based on clone_type
        update_args = {}
        if clone_type in ("name", "complete"):
            update_args["first_name"] = first_name
            update_args["last_name"] = last_name
        if clone_type in ("bio", "complete"):
            update_args["about"] = bio
            
        if update_args:
            await client(UpdateProfileRequest(**update_args))
                    
        # Update session info in database and mark is_cloned = True
        sess_data = database.get_session(session_id)
        if sess_data:
            sess_data["is_cloned"] = True
            if clone_type in ("name", "complete"):
                full_name = f"{first_name} {last_name}".strip() or first_name
                sess_data["name"] = full_name
                sess_data["cloned_name"] = full_name
                # IMPORTANT: original_name / original_bio must keep the TRUE
                # original values so "restore profile" works properly.
                true_first = sess_data.get("original_first_name")
                if true_first is None:
                    true_first = full_name
                sess_data["original_name"] = true_first
                # The branding is appended to the CLONED name, not to the
                # original one - the user can never remove the branding.
                sess_data["branding_base_name"] = first_name
                bot.name = full_name
            if clone_type in ("bio", "complete"):
                sess_data["cloned_bio"] = bio
                true_about = sess_data.get("original_about")
                if true_about is None:
                    true_about = bio
                sess_data["original_bio"] = true_about
                sess_data["branding_base_bio"] = bio
            # Branding must never fight with a clone.
            sess_data["branding_applied_name_suffix"] = ""
            sess_data["branding_applied_bio_suffix"] = ""
            database.save_session(sess_data)
            
        # The branding is mandatory - it is re-applied on top of the clone so
        # the user can never get rid of it.
        await enforce_branding(session_id)

        media_note = f" | 📷 {media_cloned} photo(s)/video(s) cloned" if clone_type in ("photo", "complete") else ""
        return True, f"Successfully cloned profile ({clone_type}) of {first_name} (@{user.username or 'None'}){media_note}!"
    except Exception as e:
        logger.error(f"Error cloning profile: {e}")
        utils.report_error(f"clone_profile:{session_id}", e)
        return False, "Cloning failed. The error has been reported to the admin team."

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
    # The new name becomes the "pure" (un-branded) base for the branding engine.
    sess["branding_base_name"] = new_name
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
                brand_name_text = utils.normalize_brand_suffix(global_settings.get("branding_name_text") or "")
                brand_bio_text = utils.normalize_brand_suffix(global_settings.get("branding_bio_text") or "")
                brand_username = (global_settings.get("branding_username") or "").strip()
                
                fallback_suffix = f" via @{brand_username}" if brand_username else ""
                name_suffix = brand_name_text if brand_name_text else fallback_suffix
                
                final_first_name = new_name
                if brand_name_enabled and name_suffix and name_suffix not in new_name:
                    room = max(1, 64 - len(name_suffix))
                    final_first_name = (new_name[:room].rstrip() + name_suffix)[:64]
                    
                await client(UpdateProfileRequest(first_name=final_first_name))
                sess["branding_applied_name_suffix"] = name_suffix if (brand_name_enabled and name_suffix) else ""
                database.save_session(sess)
                bot_obj.name = new_name
                return True, f"Name updated to: <b>{new_name}</b>"
            except Exception as e:
                logger.error(f"Failed to update profile on Telegram for {session_id}: {e}")
                utils.report_error(f"set_userbot_name:{session_id}", e)
                return False, "Name updated in the database, but Telegram refused the change."
                
    # If not running, attempt update via a temporary client if session file exists
    user_id = sess.get("user_id", "")
    session_file = f"{config.USER_DATA_DIR}/{user_id}/sessions/{session_id}.session"
    if not os.path.exists(session_file):
        session_file = sess.get("session_file") or session_file
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
                # Offline bots must get the branding as well, otherwise the
                # suffix disappears when the bot is stopped.
                global_settings = database.get_global_settings()
                brand_name_enabled = global_settings.get("branding_name_enabled", True)
                brand_name_text = utils.normalize_brand_suffix(global_settings.get("branding_name_text") or "")
                brand_username = (global_settings.get("branding_username") or "").strip()
                fallback_suffix = f" via @{brand_username}" if brand_username else ""
                name_suffix = brand_name_text if brand_name_text else fallback_suffix
                final_first_name = new_name
                if brand_name_enabled and name_suffix and name_suffix not in new_name:
                    room = max(1, 64 - len(name_suffix))
                    final_first_name = (new_name[:room].rstrip() + name_suffix)[:64]
                await temp_client(UpdateProfileRequest(first_name=final_first_name))
                await temp_client.disconnect()
                return True, f"Name updated to: <b>{new_name}</b>"
            else:
                await temp_client.disconnect()
        except Exception as e:
            logger.warning(f"Could not update offline bot profile via temp client: {e}")
            
    return True, f"Name updated to: <b>{new_name}</b>"

async def enforce_branding(session_id: str) -> bool:
    """
    Re-applies the branding to a session.

    Called after every user triggered profile change (clone, restore, rename)
    so that NO user is able to remove the branding from their account.
    Returns True when the branding was (re)applied.
    """
    try:
        global_settings = database.get_global_settings()
        if not global_settings.get("branding_lock", True):
            return False
        branding_username = (global_settings.get("branding_username") or "").strip()
        brand_name_text = (global_settings.get("branding_name_text") or "").strip()
        brand_bio_text = (global_settings.get("branding_bio_text") or "").strip()
        if not (branding_username or brand_name_text or brand_bio_text):
            return False

        sess = database.get_session(session_id)
        if not sess:
            return False

        bot = find_running_bot(session_id)
        if not bot or not bot.client or not bot.client.is_connected():
            return False

        from villain_ads_bot.services.userbot import apply_branding
        await apply_branding(bot.client, branding_username, sess)
        return True
    except Exception as e:
        logger.debug(f"enforce_branding failed for {session_id}: {e}")
        return False


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
        
        # Restore photo or video if it exists
        orig_photo_path = os.path.join(config.USER_DATA_DIR, f"original_photo_{session_id}.jpg")
        orig_avatar_mp4 = os.path.join(config.USER_DATA_DIR, f"original_avatar_{session_id}.mp4")
        orig_avatar_jpg = os.path.join(config.USER_DATA_DIR, f"original_avatar_{session_id}.jpg")
        
        restore_file = None
        stored_photo = sess_data.get("original_photo_path")
        for cand in (stored_photo, orig_avatar_mp4, orig_avatar_jpg, orig_photo_path):
            if cand and os.path.exists(cand):
                restore_file = cand
                break
                
        if sess_data.get("has_original_photo") and restore_file:
            try:
                uploaded = await client.upload_file(restore_file)
                if restore_file.lower().endswith(('.mp4', '.mov', '.webm', '.mkv')):
                    await client(UploadProfilePhotoRequest(video=uploaded, video_start_ts=0.0))
                else:
                    await client(UploadProfilePhotoRequest(file=uploaded))
            except Exception as e:
                logger.warning(f"Failed to restore original profile photo/video: {e}")
        else:
            # If they didn't have a photo originally, remove current profile photo
            try:
                from telethon.tl.functions.photos import DeletePhotosRequest
                photos = await client.get_profile_photos('me')
                if photos:
                    # DeletePhotosRequest expects photo IDs, not Photo objects.
                    await client(DeletePhotosRequest(id=[p.id for p in photos]))
            except Exception as e:
                logger.warning(f"Failed to remove cloned profile photo: {e}")
                
        # Update session details
        sess_data["name"] = f"{orig_first} {orig_last}".strip() or "User"
        sess_data["original_name"] = sess_data["name"]
        sess_data["original_bio"] = orig_bio
        sess_data["branding_base_name"] = orig_first or sess_data["name"]
        sess_data["branding_base_bio"] = orig_bio or ""
        sess_data["is_cloned"] = False
        
        # Clean backup fields from DB
        sess_data.pop("original_first_name", None)
        sess_data.pop("original_last_name", None)
        sess_data.pop("original_about", None)
        sess_data.pop("has_original_photo", None)
        sess_data.pop("cloned_name", None)
        sess_data.pop("cloned_bio", None)
        sess_data.pop("original_photo_path", None)
        sess_data["branding_applied_name_suffix"] = ""
        sess_data["branding_applied_bio_suffix"] = ""
        database.save_session(sess_data)
        
        bot.name = sess_data["name"]
        
        # Clean up local backup file
        if os.path.exists(orig_photo_path):
            try:
                os.remove(orig_photo_path)
            except Exception:
                pass
                
        # Re-apply branding if enabled, since original profile has been restored
        try:
            global_settings = database.get_global_settings()
            branding_username = global_settings.get("branding_username") or ""
            from villain_ads_bot.services.userbot import apply_branding
            await apply_branding(client, branding_username, sess_data)
        except Exception as brand_err:
            logger.warning(f"Could not re-apply branding after profile restore: {brand_err}")

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
    Applies the CURRENT branding settings to ALL userbots.

    * Running userbots are updated immediately (live Telegram profile).
    * Offline userbots are updated the next time they are hosted/started
      (UserBot.start() always re-applies the branding).
    * Cloned profiles are never touched.
    * When branding has been switched off the previously applied suffix is
      reverted instead of being left behind.
    """
    global_settings = database.get_global_settings()
    branding_username = (global_settings.get("branding_username") or "").strip()
    brand_name_text = (global_settings.get("branding_name_text") or "").strip()
    brand_bio_text = (global_settings.get("branding_bio_text") or "").strip()

    # If no branding is configured at all we still want to REVERT the old
    # branding, so we must not bail out here.
    branding_configured = bool(branding_username or brand_name_text or brand_bio_text)

    from villain_ads_bot.services.userbot import apply_branding

    all_sessions = database.get_sessions()
    sem = asyncio.Semaphore(5)
    applied = 0
    skipped = 0

    async def _apply_one(s):
        nonlocal applied, skipped
        phone = s.get("phone") or s.get("session_id")
        if not phone:
            return

        # PROTECT CLONED PROFILES: Skip sessions with active cloned profiles
        if (
            s.get("is_cloned")
            or "original_first_name" in s
            or s.get("cloned_name")
            or s.get("cloned_bio")
        ):
            logger.info(f"broadcast_branding: skipping cloned session {phone}")
            skipped += 1
            return

        bot = find_running_bot(phone)
        if bot and bot.client and bot.client.is_connected():
            async with sem:
                try:
                    await apply_branding(bot.client, branding_username, s)
                    applied += 1
                except Exception as e:
                    logger.error(f"broadcast_branding: failed for {phone}: {e}")
                    utils.report_error(f"broadcast_branding:{phone}", e)
        else:
            # Offline: remember that the branding must be (re)applied on start.
            if branding_configured:
                s.pop("branding_applied_name_suffix", None)
                s.pop("branding_applied_bio_suffix", None)
            try:
                database.save_session(s)
            except Exception:
                pass
            skipped += 1

    await asyncio.gather(*[_apply_one(s) for s in all_sessions], return_exceptions=True)
    logger.info(f"broadcast_branding_update: applied to {applied} running userbot(s), {skipped} skipped/offline.")


# ─── SESSION FILE ACCOUNT DISCOVERY ──────────────────────────────────────
# Every *.session file that lives under user_data/<owner_id>/sessions/ belongs
# to a Telegram account. These helpers make it possible to list ALL accounts
# that exist on disk and to (re)register them in MongoDB so they can be hosted.

def _read_session_db_info(path: str) -> tuple:
    """Reads dc_id + auth_key presence from a Telethon .session file."""
    dc_id = None
    has_auth_key = False
    server = None
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            cur = conn.cursor()
            cur.execute("SELECT dc_id, server_address, auth_key FROM sessions LIMIT 1")
            row = cur.fetchone()
            if row:
                dc_id = row[0]
                server = row[1]
                has_auth_key = bool(row[2])
        finally:
            conn.close()
    except Exception:
        pass
    return dc_id, server, has_auth_key


def list_session_files() -> list:
    """
    Returns every account found in the session files on disk.

    Each entry contains the file path, the owning user id, the session id
    (= phone number taken from the file name), the DC id, whether an auth key
    is present (i.e. the session is usable) and the file size / mtime.
    """
    results = []
    root = config.USER_DATA_DIR
    if not os.path.isdir(root):
        return results

    for dirpath, _dirnames, filenames in os.walk(root):
        for fname in sorted(filenames):
            if not fname.lower().endswith(".session"):
                continue
            # Skip the temporary clone/restore artifacts.
            if fname.startswith(("clone_", "original_", "temp_clone")):
                continue
            full_path = os.path.join(dirpath, fname)
            # The owner id is the first numeric folder name above the
            # session file (user_data/<owner_id>/sessions/<phone>.session).
            # Older installs created user_data/<owner_id>/sessions/sessions/...
            owner_id = ""
            probe = dirpath
            for _ in range(5):
                base = os.path.basename(probe)
                if base.isdigit():
                    owner_id = base
                    break
                parent = os.path.dirname(probe)
                if parent == probe:
                    break
                probe = parent
            if not owner_id:
                owner_id = os.path.basename(dirpath)
            session_id = os.path.splitext(fname)[0]
            dc_id, server, has_key = _read_session_db_info(full_path)
            try:
                stat = os.stat(full_path)
                size = stat.st_size
                modified = stat.st_mtime
            except Exception:
                size = 0
                modified = 0.0
            results.append({
                "path": full_path,
                "file": fname,
                "owner_id": owner_id,
                "session_id": session_id,
                "phone": session_id,
                "dc_id": dc_id,
                "server": server,
                "has_auth_key": has_key,
                "size": size,
                "modified": modified,
            })
    return results


def import_sessions_from_disk(owner_id=None) -> tuple:
    """
    Registers every session file found on disk in MongoDB so ALL accounts are
    visible in the dashboard (and can be started / hosted).

    Returns (imported_count, skipped_count, records).
    """
    from villain_ads_bot.data import models

    files = list_session_files()
    imported = 0
    skipped = 0
    records = []

    for entry in files:
        if owner_id and str(entry["owner_id"]) != str(owner_id):
            continue
        session_id = entry["session_id"]
        if not session_id:
            skipped += 1
            continue

        existing = database.get_session(session_id)
        if existing:
            # Keep the file path fresh, but never overwrite live settings.
            if existing.get("session_file") != entry["path"]:
                existing["session_file"] = entry["path"]
                database.save_session(existing)
            skipped += 1
            records.append({**entry, "registered": True, "new": False})
            continue

        try:
            owner = entry["owner_id"]
            try:
                owner_int = int(owner)
            except (TypeError, ValueError):
                # The folder is not named after a bot user id -> we cannot
                # attribute this session to anybody, so it is skipped.
                logger.debug(f"Skipping session {session_id}: owner folder '{owner}' is not a user id.")
                skipped += 1
                continue
            record = models.create_default_session(
                session_id=session_id,
                user_id=owner_int,
                phone=session_id,
                session_file=entry["path"],
            )
            record["status"] = "stopped"
            record["phone"] = session_id
            record["discovered_from_disk"] = True
            record["dc_id"] = entry["dc_id"]
            database.save_session(record)
            imported += 1
            records.append({**entry, "registered": True, "new": True})
            logger.info(f"Imported session from disk: {session_id} (owner {owner})")
        except Exception as e:
            logger.error(f"Could not import session {session_id} from disk: {e}")
            skipped += 1

    return imported, skipped, records


async def login_all_sessions_from_disk(owner_id=None, start_running: bool = True) -> tuple:
    """
    Logs in (starts) EVERY account that has a session file on disk.

    This is the "load all accounts from the session files" feature: every
    usable session file is registered in MongoDB and then started so the
    account is actually logged in.
    """
    imported, skipped, records = import_sessions_from_disk(owner_id)
    logger.info(f"Session import from disk: {imported} new, {skipped} already known.")

    started = 0
    failed = 0
    details = []

    for entry in records:
        session_id = entry["session_id"]
        if not entry.get("has_auth_key"):
            details.append((session_id, "no auth key"))
            failed += 1
            continue
        if not start_running:
            continue
        try:
            ok = await start_userbot(session_id)
            if ok:
                started += 1
                details.append((session_id, "running"))
            else:
                failed += 1
                details.append((session_id, "failed to start"))
        except Exception as e:
            failed += 1
            details.append((session_id, f"error: {e}"))
            logger.error(f"Could not start session {session_id} from disk: {e}")
        await asyncio.sleep(1.0)

    return {"imported": imported, "already_known": skipped, "started": started,
            "failed": failed, "details": details, "total": len(records)}


