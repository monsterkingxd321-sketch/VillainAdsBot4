import os
import re
import asyncio
import logging
import time
from typing import Optional, Tuple, Union
from telethon import events, Button
from telethon.tl import types
from telethon.tl.types import Channel, Chat, User

from villain_ads_bot import config
from villain_ads_bot.data import database
from villain_ads_bot.core import utils
from villain_ads_bot.services import userbot_manager
from villain_ads_bot.services.youtube import YouTube
from villain_ads_bot.services.userbot import download_media, extract_media_info, generate_silence

logger = logging.getLogger(__name__)

# Track active playing sessions per chat: { chat_id: { "bot": UserBot, "song_info": dict, "msg_id": int } }
_active_chat_players = {}
_chat_queues = {}
_track_timer_tasks = {}
_main_bot = None
_preparing_chats = set()
_chat_locks = {}

def get_chat_lock(chat_id: int) -> asyncio.Lock:
    if chat_id not in _chat_locks:
        _chat_locks[chat_id] = asyncio.Lock()
    return _chat_locks[chat_id]


async def delete_now_playing_card(chat_id: int, bot_obj=None) -> bool:
    """
    Deletes the "Now Playing" card of a chat (if there is one).

    The card is always removed from the chat when the song is skipped, ends
    or the playback is stopped - the user never gets a chat full of stale
    player cards.

    Returns True when a card was deleted.
    """
    session = _active_chat_players.get(chat_id)
    msg_id = session.get("msg_id") if session else None
    if not msg_id:
        return False

    # Remember the id before the session is removed.
    try:
        client = _main_bot
        if client is None and bot_obj is not None:
            client = getattr(bot_obj, "client", None)
        if client is None:
            for _p, _b in list(_running_bots_snapshot().items()):
                if getattr(_b, "client", None):
                    client = _b.client
                    break
        if client is not None:
            await client.delete_messages(chat_id, [msg_id])
        return True
    except Exception as e:
        logger.debug(f"Could not delete now-playing card in {chat_id}: {e}")
        return False
    finally:
        if session is not None:
            session["msg_id"] = None


def _running_bots_snapshot() -> dict:
    """Returns a shallow copy of the running userbot registry (safe to iterate)."""
    try:
        from villain_ads_bot.services import userbot_manager
        return dict(userbot_manager._running_bots)
    except Exception:
        return {}

# Rate limit for download progress edits
_last_progress_updates = {}

def download_progress_sync(current, total, msg_to_edit, operation_name="DOWNLOADING"):
    now = time.time()
    msg_id = id(msg_to_edit)
    last_time = _last_progress_updates.get(msg_id, 0.0)
    
    percent = (current / total) * 100 if total else 0.0
    if now - last_time < 3.0 and percent < 100.0:
        return
        
    _last_progress_updates[msg_id] = now
    filled_blocks = int(percent // 10)
    bar = "█" * filled_blocks + "░" * (10 - filled_blocks)
    
    mb_curr = current / (1024 * 1024)
    mb_tot = total / (1024 * 1024)
    
    txt = (
        f"<blockquote><b>» 📥 {operation_name}</b>\n\n"
        f"📊 <b>Progress :</b> <code>[{bar}] {percent:.1f}%</code>\n"
        f"💾 <b>Size :</b> <code>{mb_curr:.2f} MB / {mb_tot:.2f} MB</code></blockquote>"
    )
    try:
        asyncio.create_task(msg_to_edit.edit(txt))
    except Exception:
        pass


async def _delete_card_by_session(session: dict, chat_id: int) -> bool:
    """
    Deletes the "Now Playing" card that belongs to the given player session.
    Falls back to the main bot / the userbot client for the delete call.
    """
    if not session:
        return False
    msg_id = session.get("msg_id")
    if not msg_id:
        return False

    clients = []
    if _main_bot is not None:
        clients.append(_main_bot)
    bot = session.get("bot")
    if bot is not None and getattr(bot, "client", None) is not None:
        clients.append(bot.client)
    for _p, _b in list(_running_bots_snapshot().items()):
        c = getattr(_b, "client", None)
        if c is not None:
            clients.append(c)

    for client in clients:
        try:
            await client.delete_messages(chat_id, [msg_id])
            session["msg_id"] = None
            return True
        except Exception:
            continue
    return False


async def get_or_start_userbot_for_chat(user_id: int, chat_id: Optional[int] = None) -> Tuple[Optional[object], str]:
    """
    Finds or automatically starts a suitable UserBot instance for the user/chat.
    Ensures that active voice chats can be commanded and users can play songs.
    Returns (userbot_instance, error_message).
    """
    # 0. If in a group/channel, check if ANY running userbot is ALREADY inside this chat's VC
    if chat_id:
        for phone, b in list(userbot_manager._running_bots.items()):
            if b and b.is_running and b.client and b.client.is_connected():
                in_vc = (getattr(b, "current_vc_chat_id", None) == chat_id or 
                         chat_id in getattr(b, "joined_vcs", set()))
                if in_vc:
                    return b, ""

    # 1. Check user's own connected accounts first
    user_sessions = database.get_sessions(user_id) if user_id else []
    if user_sessions:
        # Prioritize any running userbot that is ALREADY inside this chat / VC
        if chat_id:
            for s in user_sessions:
                for ident in (s.get("phone"), s.get("session_id")):
                    if ident:
                        b = userbot_manager.find_running_bot(ident)
                        if b and b.is_running and b.client and b.client.is_connected():
                            in_vc = (getattr(b, "current_vc_chat_id", None) == chat_id or 
                                     chat_id in getattr(b, "joined_vcs", set()))
                            if in_vc:
                                return b, ""

        # Pick any running userbot belonging to this user
        for s in user_sessions:
            for ident in (s.get("phone"), s.get("session_id")):
                if ident:
                    b = userbot_manager.find_running_bot(ident)
                    if b and b.is_running and b.client and b.client.is_connected():
                        return b, ""
                
        # If not running in memory, auto-start the user's first available account
        first_session = user_sessions[0]
        phone = first_session["phone"]
        logger.info(f"Auto-starting UserBot {phone} for user {user_id} on-demand...")
        started = await userbot_manager.start_userbot(phone)
        if started and phone in userbot_manager._running_bots:
            return userbot_manager._running_bots[phone], ""

    # 2. Check if requester is a bot admin / system owner (admins can command active chat bots)
    global_settings = database.get_global_settings()
    admins_list = global_settings.get("admins", [])
    is_admin = user_id in admins_list or user_id in config.ORIGINAL_ADMIN_IDS
    
    if is_admin:
        if chat_id:
            for phone, bot in list(userbot_manager._running_bots.items()):
                if bot.is_running and bot.client and bot.client.is_connected():
                    if getattr(bot, "current_vc_chat_id", None) == chat_id or chat_id in getattr(bot, "joined_vcs", set()):
                        return bot, ""
                        
        for phone, bot in list(userbot_manager._running_bots.items()):
            if bot.is_running and bot.client and bot.client.is_connected():
                return bot, ""

    # 3. In group/channel, if any userbot is active, let group members play
    if chat_id:
        for phone, bot in list(userbot_manager._running_bots.items()):
            if bot.is_running and bot.client and bot.client.is_connected():
                return bot, ""

    err_txt = utils.format_html_message(
        "<blockquote><b>» ⚠️ No Connected Userbot Found</b>\n\n"
        "You Have Not Connected A Userbot To Your Account.\n\n"
        "💡 <b>Action :</b> Please Connect Your Own Userbot Using /start In Private Chat To Play Songs!</blockquote>"
    )
    return None, err_txt


def build_now_playing_markup(chat_id: int, is_paused: bool = False, is_muted: bool = False):
    """
    Generates inline control buttons for Now Playing cards.
    """
    pause_btn = utils.styled_button("▶️ Resume", f"player_resume_{chat_id}", style="success") if is_paused else utils.styled_button("⏸️ Pause", f"player_pause_{chat_id}", style="primary")
    mute_btn = utils.styled_button("🔊 Unmute", f"player_unmute_{chat_id}", style="success") if is_muted else utils.styled_button("🔇 Mute", f"player_mute_{chat_id}", style="primary")
    
    return [
        [
            pause_btn,
            utils.styled_button("⏹️ Stop", f"player_stop_{chat_id}", style="danger"),
            mute_btn
        ],
        [
            utils.styled_button("⏭️ Skip", f"player_skip_{chat_id}", style="primary"),
            utils.styled_button("📜 Queue", f"player_queue_{chat_id}", style="primary"),
            utils.styled_button("🖼️ Thumb", f"player_thumb_toggle_{chat_id}", style="primary")
        ]
    ]


def format_now_playing_text(song_info: dict, requester_name: str, requester_id: int, stream_type: str = "Audio", bot_count: int = 1) -> str:
    """
    Renders the rich Now Playing markdown text with Telegram blockquotes.
    """
    import html
    title_safe = html.escape(song_info.get("title", "Unknown Track"))
    duration = song_info.get("duration", 0)
    mins, secs = divmod(duration, 60)
    dur_str = f"{mins:02d}:{secs:02d}" if duration > 0 else "Live Stream"
    
    ub_name = html.escape(song_info.get("userbot_name") or "UserBot")
    ub_username = song_info.get("username")
    ub_display = f"@{ub_username}" if ub_username else f"`{song_info.get('userbot_id', '')}`"
    
    mode_emoji = "🎬 Video Stream" if stream_type.lower() == "video" else "🎙️ Audio Stream"
    bot_header_extra = f" ({bot_count} Userbots)" if bot_count > 1 else ""
    
    req_name_safe = html.escape(requester_name)
    text = (
        f"<blockquote><b>» 🎵 Now Streaming{bot_header_extra}</b>\n\n"
        f"<b>📌 Title :</b> <b>{title_safe}</b>\n"
        f"<b>⏱️ Duration :</b> <code>{dur_str}</code>\n"
        f"<b>🎧 Mode :</b> <b>{mode_emoji}</b>\n"
        f"<b>👤 Requested By :</b> <a href=\"tg://user?id={requester_id}\">{req_name_safe}</a>\n"
        f"<b>🤖 Stream Source :</b> <b>{ub_name}</b> ({ub_display})</blockquote>"
    )
    from villain_ads_bot.core import utils
    return utils.format_html_message(text)


async def play_next_in_queue(chat_id: int, client=None):
    """
    Dequeues and plays the next scheduled track for the given chat.
    """
    # 4.4 - the lock makes sure that only ONE track is started per chat. Two
    # concurrent calls (the skip command, the StreamEnded handler and the
    # auto advance timer) popped two tracks and started two streams.
    lock = get_chat_lock(chat_id)
    if lock.locked():
        logger.debug(f"A play is already running in chat {chat_id} - skipping the advance.")
        return
    async with lock:
        await _play_next_in_queue_locked(chat_id, client)


async def _play_next_in_queue_locked(chat_id: int, client=None):
    """The actual queue advance. Must be called with the chat lock held."""
    # Cancel previous timer if still alive and not current task
    prev_timer = _track_timer_tasks.pop(chat_id, None)
    if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
        prev_timer.cancel()
        
    queue = _chat_queues.get(chat_id, [])
    if not queue:
        # No more songs in queue — cleanly stop the stream while keeping userbot in VC
        _chat_queues.pop(chat_id, None)
        active_sess = _active_chat_players.pop(chat_id, None)
        # The finished song's card must not stay in the chat.
        if active_sess:
            await _delete_card_by_session(active_sess, chat_id)
            for cb in active_sess.get("companion_bots", []):
                try:
                    await cb.stop_song(chat_id, leave_vc=False)
                except Exception:
                    pass
            if active_sess.get("bot"):
                bot = active_sess["bot"]
                try:
                    await bot.stop_song(chat_id, leave_vc=False)
                except Exception:
                    pass
        return
        
    next_item = queue.pop(0)
    query = next_item.get("query", "")
    play_type = next_item.get("play_type", "audio")
    requester_name = next_item.get("requester_name", "User")
    requester_id = next_item.get("requester_id", 0)
    local_file_path = next_item.get("local_file")
    audio_title = next_item.get("title")
    audio_duration = next_item.get("duration", 30)
    
    active_sess = _active_chat_players.get(chat_id)
    if active_sess and active_sess.get("bot"):
        bot_obj = active_sess["bot"]
    else:
        bot_obj, err = await get_or_start_userbot_for_chat(requester_id, chat_id)

    if not bot_obj:
        logger.error(f"Cannot auto-advance queue: {err}")
        return
        
    success, msg, song_info = await bot_obj.play_song(
        query=query,
        play_type=play_type,
        chat_id=chat_id,
        local_file=local_file_path,
        title=audio_title,
        duration=audio_duration
    )
    
    if not success or not song_info:
        # 4.5 - the track was popped BEFORE it was played, so a failure
        # silently lost it. Put it back at the front of the queue.
        logger.warning(f"Failed to play next queued track: {msg}, advancing...")
        try:
            _chat_queues.setdefault(chat_id, []).insert(0, next_item)
        except Exception:
            pass
        return
        
    companion_bots = []
    try:
        prev_companions = active_sess.get("companion_bots", []) if active_sess else []
        if prev_companions:
            companion_bots = [cb for cb in prev_companions if cb.is_running]
        else:
            owner_uid = getattr(bot_obj, "owner_id", None) or requester_id
            if owner_uid:
                companion_bots = userbot_manager.get_running_userbots_for_owner(
                    owner_uid, chat_id=chat_id, exclude_bot=bot_obj
                )
    except Exception as comp_err:
        logger.debug(f"Companion bot lookup error in queue advance: {comp_err}")

    if companion_bots and song_info.get("file_path"):
        local_fp = song_info["file_path"]
        async def _stream_companion_next(c_bot):
            try:
                await c_bot.play_song(
                    query="",
                    play_type=play_type,
                    chat_id=chat_id,
                    local_file=local_fp,
                    title=song_info.get("title"),
                    duration=song_info.get("duration", 30)
                )
                logger.info(f"Companion userbot {c_bot.session_id} streaming next track in VC {chat_id}")
            except Exception as c_err:
                logger.warning(f"Companion userbot {c_bot.session_id} next stream error: {c_err}")

        asyncio.create_task(asyncio.gather(*[_stream_companion_next(cb) for cb in companion_bots], return_exceptions=True))

    thumb_enabled = database.get_thumbnail_setting(chat_id)
    np_text = format_now_playing_text(song_info, requester_name, requester_id, stream_type=play_type, bot_count=len(companion_bots) + 1)
    buttons = build_now_playing_markup(chat_id, is_paused=False, is_muted=False)
    thumb_file = song_info.get("thumb")

    # The previous song's card is removed before the new one is posted so the
    # chat never accumulates stale player cards.
    previous = _active_chat_players.get(chat_id)
    if previous:
        await _delete_card_by_session(previous, chat_id)

    # Accept both local file paths and HTTP URLs for thumbnail
    def _thumb_valid(tf):
        if not tf:
            return False
        s = str(tf)
        if s.startswith("http"):
            return True
        return os.path.exists(s) and os.path.getsize(s) > 0

    sent_msg = None
    if _main_bot:
        try:
            if thumb_enabled and _thumb_valid(thumb_file):
                sent_msg = await _main_bot.send_message(chat_id, np_text, file=thumb_file, buttons=buttons, parse_mode="html")
            else:
                sent_msg = await _main_bot.send_message(chat_id, np_text, buttons=buttons, parse_mode="html")
        except Exception as send_err:
            logger.warning(f"Failed to send queue now playing with thumb using main_bot: {send_err}")
            
    # Fallback to userbot if main bot fails or is unavailable
    ub_client = getattr(bot_obj, "client", None)
    if not sent_msg and ub_client:
        try:
            if thumb_enabled and _thumb_valid(thumb_file):
                sent_msg = await ub_client.send_message(chat_id, np_text, file=thumb_file, parse_mode="html")
            else:
                sent_msg = await ub_client.send_message(chat_id, np_text, parse_mode="html")
        except Exception as ub_err:
            logger.warning(f"Userbot also failed to send queue np: {ub_err}")
                
    # 4.1 - the previous card must be removed BEFORE the session is
    # overwritten, otherwise its message id is lost and the card stays in
    # the chat forever.
    try:
        previous = _active_chat_players.get(chat_id)
        if previous:
            await _delete_card_by_session(previous, chat_id)
    except Exception:
        pass

    _active_chat_players[chat_id] = {
        "bot": bot_obj,
        "companion_bots": companion_bots,
        "song_info": song_info,
        "msg_id": sent_msg.id if sent_msg else None,
        "is_paused": False,
        "is_muted": False
    }
    
    dur = song_info.get("duration", 30) or 30
    async def track_auto_timer():
        await asyncio.sleep(dur + 2)
        await play_next_in_queue(chat_id, client)
        
    _track_timer_tasks[chat_id] = asyncio.create_task(track_auto_timer())


def register_handlers(client):
    global _main_bot
    _main_bot = client
    """
    Registers all music player and voice chat handlers onto the Telethon client.
    """

    # ------------------ /play, .play, /vplay, .vplay, .playforce, /playforce commands ------------------
    @client.on(events.NewMessage(pattern=r"(?i)^[./!?](play|vplay|cplay|stream|vstream|playforce|vplayforce|cplayforce|forceplay|vforceplay)(?:@\w+)?(?:\s+([\s\S]*))?$"))
    async def play_command_handler(event):
        if await utils.guard(event, client):
            return
            
        try:
            await event.delete()
        except Exception:
            pass
            
        cmd = event.pattern_match.group(1).lower()
        raw_query = event.pattern_match.group(2)
        query = raw_query.strip() if raw_query else ""
        play_type = "video" if ("vplay" in cmd or "vstream" in cmd) else "audio"
        is_force = any(k in cmd for k in ["force", "cplayforce"])
        
        chat_id = event.chat_id
        user_id = event.sender_id
        
        if not utils.check_and_mark_command(chat_id, getattr(event, "raw_text", ""), msg_id=getattr(event, "id", 0)):
            return
            
        sender = await event.get_sender()
        requester_name = getattr(sender, "first_name", None) or "User"
        
        reply_msg = await event.get_reply_message() if event.is_reply else None
        local_file_path = None
        audio_title = None
        audio_duration = 0
        
        display_query = audio_title or query or "Replied Media"
        progress_msg = await event.respond(f"<b>» 🎧 Playing :</b> <b>{display_query}</b>")
        
        # 1. Handle replied media (Audio, Video, Voice, Document)
        if reply_msg and (reply_msg.audio or reply_msg.video or reply_msg.voice or reply_msg.document):
            media_obj, audio_title, audio_duration = extract_media_info(reply_msg)
            if media_obj:
                os.makedirs(config.DOWNLOADS_DIR, exist_ok=True)
                try:
                    local_file_path = await client.download_media(
                        reply_msg,
                        file="downloads/",
                        progress_callback=lambda c, t: download_progress_sync(c, t, progress_msg, "DOWNLOADING MEDIA")
                    )
                except Exception as dl_err:
                    logger.error(f"Failed to download replied media: {dl_err}")
                    await progress_msg.edit(
                        utils.format_html_message(
                            f"<blockquote><b>» ❌ Failed To Download Media</b>\n\n"
                            f"⚠️ <b>Error :</b> <code>{dl_err}</code></blockquote>"
                        )
                    )
                    try:
                        await asyncio.sleep(5)
                        await progress_msg.delete()
                    except Exception:
                        pass
                    return
        elif not query:
            await progress_msg.edit(
                utils.format_html_message(
                    f"<blockquote><b>» 💡 Music Player Usage Guide</b>\n\n"
                    f"• <code>.{cmd} &lt;song name&gt;</code> (E.G. <code>.{cmd} Faded Alan Walker</code>)\n"
                    f"• <code>/{cmd} &lt;song name&gt;</code>\n"
                    f"• <code>.playforce &lt;song name&gt;</code> (Force Play Now)\n"
                    f"• <code>.queue</code> / <code>.skip</code>\n"
                    f"• <b>Thumbnail Mode :</b> <code>.thumb on</code> Or <code>.thumb off</code>\n"
                    f"• Reply To Any Audio/Video File With <code>.{cmd}</code>\n\n"
                    f"⚡ <i>Start Group Voice Chat Before Streaming.</i></blockquote>"
                )
            )
            return

        # Fetch metadata if it's a query and not a local file
        if query and not local_file_path:
            try:
                t, m, s, thumb, vid = await YouTube.details(query)
                if t:
                    audio_title = t
                    try:
                        await progress_msg.edit(f"<b>» 🎧 Playing :</b> <b>{audio_title}</b>")
                    except Exception:
                        pass
                if s:
                    audio_duration = s
            except Exception as yt_err:
                logger.warning(f"Error fetching YouTube details in player: {yt_err}")

        # 2. Pre-validate userbot BEFORE queue check — so accounts with no valid userbot
        #    get an error immediately instead of being silently queued and failing at playback.
        bot_obj, err = await get_or_start_userbot_for_chat(user_id, chat_id)
        if not bot_obj:
            await progress_msg.edit(err)
            try:
                await asyncio.sleep(5)
                await progress_msg.delete()
            except Exception:
                pass
            return

        # 3. Queue logic: if already playing or currently preparing, or queue has tracks, and NOT a force play -> add to queue
        async with get_chat_lock(chat_id):
            is_busy = (
                (chat_id in _active_chat_players and _active_chat_players[chat_id].get("bot")) or
                (chat_id in _preparing_chats) or
                (len(_chat_queues.get(chat_id, [])) > 0)
            )
            if is_busy and not is_force:
                if chat_id not in _chat_queues:
                    _chat_queues[chat_id] = []
                    
                _chat_queues[chat_id].append({
                    "query": query,
                    "play_type": play_type,
                    "requester_name": requester_name,
                    "requester_id": user_id,
                    "local_file": local_file_path,
                    "title": audio_title or query,
                    "duration": audio_duration,
                    "thumb": None
                })
                pos = len(_chat_queues[chat_id])
                mins, secs = divmod(audio_duration or 0, 60)
                dur_str = f"{mins:02d}:{secs:02d}" if audio_duration else "03:00"
                mode_emoji = "🎬 Video" if play_type == "video" else "🎙️ Audio"
                
                await progress_msg.edit(
                    utils.format_html_message(
                        f"<blockquote><b>» 📋 Added To Queue : #{pos}</b>\n\n"
                        f"<b>📌 Title :</b> <b>{audio_title or query}</b>\n"
                        f"<b>⏱️ Duration :</b> <code>{dur_str}</code>\n"
                        f"<b>🎧 Mode :</b> <b>{mode_emoji}</b>\n"
                        f"<b>👤 Requested By :</b> <a href=\"tg://user?id={user_id}\">{requester_name}</a>\n\n"
                        f"💡 <i>Track Will Play Automatically After Current Song Finishes.</i></blockquote>"
                    )
                )
                # The queue notice is temporary - remove it again so the chat
                # stays clean.
                try:
                    await asyncio.sleep(4)
                    await progress_msg.delete()
                except Exception:
                    pass
                return
            else:
                _preparing_chats.add(chat_id)

        try:
            # 4. Ensure userbot has loaded group cache
            if (event.is_group or event.is_channel) and bot_obj and bot_obj.client:
                try:
                    await bot_obj.client.get_entity(chat_id)
                except Exception:
                    try:
                        await bot_obj.client.get_dialogs(limit=50)
                    except Exception:
                        pass
                    try:
                        from telethon.tl.functions.messages import ExportChatInviteRequest
                        from villain_ads_bot.services.userbot import join_channel_single
                        invite = await client(ExportChatInviteRequest(chat_id))
                        if hasattr(invite, "link") and invite.link:
                            logger.info(f"Auto-inviting userbot {bot_obj.session_id} to group {chat_id} via {invite.link}")
                            await join_channel_single(bot_obj.client, invite.link)
                            await asyncio.sleep(1.0)
                    except Exception as exp_err:
                        logger.debug(f"Could not auto-invite userbot to group {chat_id}: {exp_err}")

            # Cancel any previous track timer
            prev_timer = _track_timer_tasks.pop(chat_id, None)
            if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
                prev_timer.cancel()

            # 5. Stream media into Voice Chat
            success, msg, song_info = await bot_obj.play_song(
                query=query,
                play_type=play_type,
                chat_id=chat_id,
                local_file=local_file_path,
                title=audio_title,
                duration=audio_duration
            )
            
            if not success or not song_info:
                ub_handle = f"@{bot_obj.username}" if bot_obj.username else bot_obj.name
                utils.report_error(f"player.play_failed:{chat_id}", RuntimeError(str(msg)))
                await progress_msg.edit(
                    utils.format_html_message(
                        "<blockquote><b>» ❌ Playback Failed</b>\n\n"
                        "⚠️ <i>The track could not be played. The error was reported to the admin team.</i>\n\n"
                        f"💡 <b>Tip :</b> Make Sure Group Voice Chat Is Started And Userbot (<b>{ub_handle}</b>) Has Permission To Speak.</blockquote>"
                    )
                )
                try:
                    await asyncio.sleep(5)
                    await progress_msg.delete()
                except Exception:
                    pass
                return
        finally:
            # 4.6 - the chat must only become free again once the new
            # session is registered. Before this there was a window in
            # which a second .play could start a second stream.
            _preparing_chats.discard(chat_id)

        # Stream concurrently on all companion userbots of this owner that are joined in this voice chat
        companion_bots = []
        try:
            owner_uid = getattr(bot_obj, "owner_id", None) or user_id
            if owner_uid:
                companion_bots = userbot_manager.get_running_userbots_for_owner(
                    owner_uid, chat_id=chat_id, exclude_bot=bot_obj
                )
        except Exception as comp_err:
            logger.debug(f"Companion bot lookup error in play_command: {comp_err}")

        if companion_bots and song_info.get("file_path"):
            local_fp = song_info["file_path"]
            async def _stream_companion_play(c_bot):
                try:
                    await c_bot.play_song(
                        query="",
                        play_type=play_type,
                        chat_id=chat_id,
                        local_file=local_fp,
                        title=song_info.get("title"),
                        duration=song_info.get("duration", 30)
                    )
                    logger.info(f"Companion userbot {c_bot.session_id} streaming in VC {chat_id}")
                except Exception as c_err:
                    logger.warning(f"Companion userbot {c_bot.session_id} stream error: {c_err}")

            asyncio.create_task(asyncio.gather(*[_stream_companion_play(cb) for cb in companion_bots], return_exceptions=True))
            
        # 5. Render Now Playing Card
        thumb_enabled = database.get_thumbnail_setting(chat_id)
        np_text = format_now_playing_text(song_info, requester_name, user_id, stream_type=play_type, bot_count=len(companion_bots) + 1)
        buttons = build_now_playing_markup(chat_id, is_paused=False, is_muted=False)
        
        thumb_file = song_info.get("thumb")
        sent_msg = None
        
        if thumb_enabled and thumb_file:
            try:
                sent_msg = await event.respond(np_text, file=thumb_file, buttons=buttons)
                await progress_msg.delete()
            except Exception as send_err:
                logger.warning(f"Failed to send with thumb: {send_err}, editing text-only")
                try:
                    sent_msg = await progress_msg.edit(np_text, buttons=buttons)
                except Exception:
                    pass
        else:
            try:
                sent_msg = await progress_msg.edit(np_text, buttons=buttons)
            except Exception:
                try:
                    sent_msg = await event.respond(np_text, buttons=buttons)
                except Exception:
                    pass
                
        # Store active player session
        _active_chat_players[chat_id] = {
            "bot": bot_obj,
            "companion_bots": companion_bots,
            "song_info": song_info,
            "msg_id": sent_msg.id if sent_msg else None,
            "is_paused": False,
            "is_muted": False
        }
        
        # Start auto-advance queue timer as safety watchdog only (PyTgCalls StreamEnded handles true finish)
        dur = song_info.get("duration", 30) or 30
        async def track_auto_timer():
            await asyncio.sleep(max(dur + 30, 300))
            await play_next_in_queue(chat_id, client)
            
        _track_timer_tasks[chat_id] = asyncio.create_task(track_auto_timer())

    # ------------------ /skip, .skip, /next, .next commands ------------------
    @client.on(events.NewMessage(pattern=r"(?i)^[./!?](skip|next|cskip|cnext)(?:@\w+)?$"))
    async def skip_command_handler(event):
        if await utils.guard(event, client):
            return
            
        try:
            await event.delete()
        except Exception:
            pass
            
        chat_id = event.chat_id
        user_id = event.sender_id
        
        if not utils.check_and_mark_command(chat_id, getattr(event, "raw_text", "")):
            return
            
        queue = _chat_queues.get(chat_id, [])
        if not queue:
            stale = _active_chat_players.pop(chat_id, None)
            if stale:
                await _delete_card_by_session(stale, chat_id)
                for cb in stale.get("companion_bots", []):
                    try:
                        await cb.stop_song(chat_id, leave_vc=False)
                    except Exception:
                        pass
            bot_obj, _ = await get_or_start_userbot_for_chat(user_id, chat_id)
            if bot_obj:
                await bot_obj.stop_song(chat_id)
            
            empty_msg = await event.respond(
                utils.format_html_message(
                    "<blockquote><b>» ⏭️ Queue Empty</b>\n\n"
                    "No More Songs In Queue. Stream Has Been Stopped.</blockquote>"
                )
            )
            try:
                await asyncio.sleep(3)
                await empty_msg.delete()
            except Exception:
                pass
            return
            
        prog = await event.respond(
            utils.format_html_message(
                "<blockquote><b>» ⏭️ Skipping Track</b>\n\n"
                "Skipping To Next Song In Queue...</blockquote>"
            )
        )
        await play_next_in_queue(chat_id, client)
        await asyncio.sleep(2)
        try:
            await prog.delete()
        except Exception:
            pass

    # ------------------ /queue, .queue, /q, .q, /playlist commands ------------------
    @client.on(events.NewMessage(pattern=r"(?i)^[./!?](queue|q|playlist|cqueue)(?:@\w+)?$"))
    async def queue_command_handler(event):
        if await utils.guard(event, client):
            return
            
        chat_id = event.chat_id
        active = _active_chat_players.get(chat_id)
        queue = _chat_queues.get(chat_id, [])
        
        if not active and not queue:
            await event.respond(
                utils.format_html_message(
                    "<blockquote><b>» 📜 Queue Playlist</b>\n\n"
                    "<i>Queue Is Currently Empty. Use <code>.play &lt;song&gt;</code> To Add Tracks!</i></blockquote>"
                )
            )
            return
            
        np_info = active.get("song_info", {}) if active else {}
        np_title = np_info.get("title", "Unknown Track")
        np_dur = np_info.get("duration", 0)
        mins, secs = divmod(np_dur, 60)
        np_dur_str = f"{mins:02d}:{secs:02d}" if np_dur else "Live"
        
        text = (
            "<blockquote><b>» 📜 Queue Playlist</b>\n\n"
            f"🎵 <b>Now Playing :</b>\n"
            f"• <b>{np_title}</b> (<code>{np_dur_str}</code>)\n\n"
        )
        
        if queue:
            text += "📋 <b>Upcoming Queue :</b>\n"
            for idx, item in enumerate(queue[:10], 1):
                item_title = item.get("title") or item.get("query") or "Unknown"
                item_dur = item.get("duration", 0)
                m, s = divmod(item_dur, 60)
                d_str = f"{m:02d}:{s:02d}" if item_dur else "03:00"
                req = item.get("requester_name", "User")
                text += f"{idx}. <b>{item_title}</b> (<code>{d_str}</code>) | <i>{req}</i>\n"
                
            if len(queue) > 10:
                text += f"\n<i>...And {len(queue) - 10} More Tracks</i>\n"
        else:
            text += "📋 <b>Upcoming Queue :</b>\n<i>No Tracks In Queue.</i>\n"
            
        text += "\n💡 <i>Use <code>.skip</code> Or <code>/skip</code> To Play Next Track.</i></blockquote>"
        await event.reply(utils.format_html_message(text))

    # ------------------ Playback Control Commands (.pause, /pause, .resume, /resume, .stop, /stop, etc.) ------------------
    @client.on(events.NewMessage(pattern=r"(?i)^[./!?](pause|resume|stop|end|mute|unmute)(?:@\w+)?$"))
    async def player_control_commands(event):
        if await utils.guard(event, client):
            return
            
        try:
            await event.delete()
        except Exception:
            pass
            
        cmd = event.pattern_match.group(1).lower()
        chat_id = event.chat_id
        user_id = event.sender_id
        
        bot_obj, _ = await get_or_start_userbot_for_chat(user_id, chat_id)
        if not bot_obj:
            await event.respond(
                utils.format_html_message(
                    "<blockquote><b>» ⚠️ No Userbot Found</b>\n\n"
                    "No Active Userbot Found In This Chat Session.</blockquote>"
                )
            )
            return

        if cmd == "pause":
            try:
                pytg = await bot_obj.get_pytgcalls()
                await utils.pytg_pause(pytg, chat_id)
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ⏸️ Playback Paused</b>\n\n"
                        "Stream Is Paused. Use <code>.resume</code> Or <code>/resume</code> To Continue Playing.</blockquote>"
                    )
                )
            except Exception as e:
                utils.report_error("player.pause", e)
                await event.respond(utils.format_html_message("<blockquote><b>» ❌ Error Pausing</b>\n\n⚠️ <i>Could not pause the stream. The error was reported to the admin team.</i></blockquote>"))

        elif cmd == "resume":
            try:
                pytg = await bot_obj.get_pytgcalls()
                await utils.pytg_resume(pytg, chat_id)
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ▶️ Playback Resumed</b>\n\n"
                        "Stream Resumed Successfully.</blockquote>"
                    )
                )
            except Exception as e:
                utils.report_error("player.resume", e)
                await event.respond(utils.format_html_message("<blockquote><b>» ❌ Error Resuming</b>\n\n⚠️ <i>Could not resume the stream. The error was reported to the admin team.</i></blockquote>"))

        elif cmd in ("stop", "end"):
            _chat_queues.pop(chat_id, None)
            prev_timer = _track_timer_tasks.pop(chat_id, None)
            if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
                prev_timer.cancel()

            # Eagerly pop from active players to prevent race conditions with
            # new .play commands - the card is deleted first so it does not
            # stay behind in the chat.
            stale = _active_chat_players.pop(chat_id, None)
            if stale:
                await _delete_card_by_session(stale, chat_id)

            prog = await event.respond(
                utils.format_html_message(
                    "<blockquote><b>» ⏳ Stopping Playback...</b></blockquote>"
                )
            )
            success, msg = await bot_obj.stop_song(chat_id)
            if success:
                try:
                    await prog.edit(
                        utils.format_html_message(
                            "<blockquote><b>» ⏹️ Playback Stopped</b>\n\n"
                            "Voice Chat Stream Stopped, Ub Left Vc & Queue Cleared.</blockquote>"
                        )
                    )
                except Exception:
                    pass
                await asyncio.sleep(3)
                try:
                    await prog.delete()
                except Exception:
                    pass
            else:
                try:
                    await prog.edit(
                        utils.format_html_message(
                            f"<blockquote><b>» ❌ Failed To Stop</b>\n\n⚠️ <code>{msg}</code></blockquote>"
                        )
                    )
                except Exception:
                    pass

        elif cmd == "mute":
            success, msg = await bot_obj.mute_mic(chat_id)
            if success:
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» 🔇 Mic Muted</b>\n\n"
                        "Userbot Microphone Has Been Turned <b>Off</b>.</blockquote>"
                    )
                )
            else:
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ❌ Could Not Mute</b>\n\n"
                        "⚠️ <i>The microphone could not be muted. The error was reported to the admin team.</i></blockquote>"
                    )
                )

        elif cmd == "unmute":
            success, msg = await bot_obj.unmute_mic(chat_id)
            if success:
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» 🔊 Mic Unmuted</b>\n\n"
                        "Userbot Microphone Has Been Turned <b>On</b>.</blockquote>"
                    )
                )
            else:
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ❌ Could Not Unmute</b>\n\n"
                        "⚠️ <i>The microphone could not be unmuted. The error was reported to the admin team.</i></blockquote>"
                    )
                )

    # ------------------ /vc, .vc, /joinvc, .joinvc, /leavevc, .leavevc ------------------
    @client.on(events.NewMessage(pattern=r"(?i)^[./!?](vc|joinvc|leavevc|vcleft)(?:@\w+)?$"))
    async def vc_join_leave_commands(event):
        if await utils.guard(event, client):
            return
            
        try:
            await event.delete()
        except Exception:
            pass
            
        cmd = event.pattern_match.group(1).lower()
        chat_id = event.chat_id
        user_id = event.sender_id
        
        bot_obj, err = await get_or_start_userbot_for_chat(user_id, chat_id)
        if not bot_obj:
            await event.respond(err)
            return

        if cmd in ("vc", "joinvc"):
            prog = await event.respond(
                utils.format_html_message(
                    "<blockquote><b>» ⏳ Joining Voice Chat</b>\n\n"
                    "Connecting Userbot To Group Voice Chat...</blockquote>"
                )
            )
            success, msg = await bot_obj.join_voice_chat(str(chat_id))
            try:
                await prog.delete()
            except Exception:
                pass
            if success:
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» 🎙️ Connected To Voice Chat</b>\n\n"
                        "Userbot Successfully Connected To Voice Chat!\n\n"
                        "💡 <i>Use <code>.play &lt;song&gt;</code> Or <code>.vplay &lt;video&gt;</code> To Stream Media.</i></blockquote>"
                    )
                )
            else:
                await event.respond(
                    utils.format_html_message(
                        f"<blockquote><b>» ❌ Failed To Join Vc</b>\n\n⚠️ <code>{msg}</code></blockquote>"
                    )
                )

        elif cmd in ("leavevc", "vcleft"):
            prog = await event.respond(
                utils.format_html_message(
                    "<blockquote><b>» ⏳ Leaving Voice Chat</b>\n\n"
                    "Disconnecting Userbot From Voice Chat...</blockquote>"
                )
            )
            success, msg = await bot_obj.leave_voice_chat(chat_id)
            try:
                await prog.delete()
            except Exception:
                pass
            if success:
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» 👋 Left Voice Chat</b>\n\n"
                        "Userbot Has Disconnected From The Voice Chat.</blockquote>"
                    )
                )
            else:
                await event.respond(utils.format_html_message(f"<blockquote><b>» ❌ Failed To Leave Vc</b>\n\n⚠️ <code>{msg}</code></blockquote>"))

    # ------------------ /thumb, .thumb toggle command ------------------
    @client.on(events.NewMessage(pattern=r"(?i)^[./!?](thumb|thumbnail)(?:@\w+)?(?:\s+(on|off))?$"))
    async def thumbnail_toggle_cmd(event):
        if await utils.guard(event, client):
            return
            
        arg = event.pattern_match.group(2)
        chat_id = event.chat_id
        current = database.get_thumbnail_setting(chat_id)
        
        if arg:
            new_state = (arg.lower() == "on")
        else:
            new_state = not current
            
        database.set_thumbnail_setting(chat_id, new_state)
        status_text = "🟢 <b>Enabled</b>" if new_state else "🔴 <b>Disabled</b>"
        mode_desc = "Artwork Thumbnail Banner Will Be Displayed." if new_state else "Clean Text-Only Mode Active (No Thumbnail)."
        
        await event.reply(
            utils.format_html_message(
                f"<blockquote><b>» 🖼️ Thumbnail Setting Updated</b>\n\n"
                f"<b>• Status :</b> {status_text}\n"
                f"<b>• Display Mode :</b> <i>{mode_desc}</i></blockquote>"
            )
        )



    # ------------------ Interactive Player Callback Handlers ------------------
    @client.on(events.CallbackQuery(pattern=r"^player_(pause|resume|stop|skip|queue|mute|unmute|thumb_toggle)_(.+)$"))
    async def player_callback_handler(event):
        action = event.pattern_match.group(1)
        chat_id_str = event.pattern_match.group(2)
        try:
            chat_id = int(chat_id_str)
        except Exception:
            chat_id = event.chat_id
            
        user_id = event.sender_id
        bot_obj, _ = await get_or_start_userbot_for_chat(user_id, chat_id)
        if not bot_obj:
            await event.answer("⚠️ No Active Userbot Found For This Vc.", alert=True)
            return

        if action == "pause":
            try:
                pytg = await bot_obj.get_pytgcalls()
                await utils.pytg_pause(pytg, chat_id)
                active_info = _active_chat_players.get(chat_id, {})
                for cb in active_info.get("companion_bots", []):
                    try:
                        cb_pytg = await cb.get_pytgcalls()
                        await utils.pytg_pause(cb_pytg, chat_id)
                    except Exception:
                        pass
                await event.answer("⏸️ Playback Paused.")
                buttons = build_now_playing_markup(chat_id, is_paused=True, is_muted=bot_obj.is_muted)
                await event.edit(buttons=buttons)
            except Exception as e:
                utils.report_error("player_callback.pause", e)
                await event.answer("❌ Could not pause the stream.", alert=True)

        elif action == "resume":
            try:
                pytg = await bot_obj.get_pytgcalls()
                await utils.pytg_resume(pytg, chat_id)
                active_info = _active_chat_players.get(chat_id, {})
                for cb in active_info.get("companion_bots", []):
                    try:
                        cb_pytg = await cb.get_pytgcalls()
                        await utils.pytg_resume(cb_pytg, chat_id)
                    except Exception:
                        pass
                await event.answer("▶️ Playback Resumed.")
                buttons = build_now_playing_markup(chat_id, is_paused=False, is_muted=bot_obj.is_muted)
                await event.edit(buttons=buttons)
            except Exception as e:
                utils.report_error("player_callback.resume", e)
                await event.answer("❌ Could not resume the stream.", alert=True)

        elif action == "stop":
            _chat_queues.pop(chat_id, None)
            prev_timer = _track_timer_tasks.pop(chat_id, None)
            if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
                prev_timer.cancel()
            
            stale = _active_chat_players.pop(chat_id, None)
            if stale:
                await _delete_card_by_session(stale, chat_id)
                for cb in stale.get("companion_bots", []):
                    try:
                        await cb.stop_song(chat_id, leave_vc=False)
                    except Exception:
                        pass
            success, msg = await bot_obj.stop_song(chat_id)
            if success:
                await event.answer("⏹️ Playback Stopped.")
                try:
                    await event.edit(
                        utils.format_html_message(
                            "<blockquote><b>» ⏹️ Playback Stopped</b>\n\n"
                            "Stream Ended By User And Queue Cleared.</blockquote>"
                        ),
                        buttons=None
                    )
                except Exception:
                    pass
                try:
                    await event.delete()
                except Exception:
                    pass
            else:
                await event.answer("❌ Could not stop the stream.", alert=True)

        elif action == "skip":
            await event.answer("⏭️ Skipping to next song...")
            await play_next_in_queue(chat_id, client)
            try:
                await event.delete()
            except Exception:
                pass

        elif action == "queue":
            queue = _chat_queues.get(chat_id, [])
            if not queue:
                await event.answer("📜 Queue is currently empty.", alert=True)
            else:
                await event.answer(f"📜 {len(queue)} songs in upcoming queue!", alert=True)

        elif action == "mute":
            success, msg = await bot_obj.mute_mic(chat_id)
            active_info = _active_chat_players.get(chat_id, {})
            for cb in active_info.get("companion_bots", []):
                try:
                    await cb.mute_mic(chat_id)
                except Exception:
                    pass
            await event.answer(msg if success else "❌ Could not mute the microphone.", alert=not success)
            buttons = build_now_playing_markup(chat_id, is_paused=False, is_muted=True)
            try:
                await event.edit(buttons=buttons)
            except Exception:
                pass

        elif action == "unmute":
            success, msg = await bot_obj.unmute_mic(chat_id)
            active_info = _active_chat_players.get(chat_id, {})
            for cb in active_info.get("companion_bots", []):
                try:
                    await cb.unmute_mic(chat_id)
                except Exception:
                    pass
            await event.answer(msg if success else "❌ Could not unmute the microphone.", alert=not success)
            buttons = build_now_playing_markup(chat_id, is_paused=False, is_muted=False)
            try:
                await event.edit(buttons=buttons)
            except Exception:
                pass

        elif action == "thumb_toggle":
            curr = database.get_thumbnail_setting(chat_id)
            new_s = not curr
            database.set_thumbnail_setting(chat_id, new_s)
            state_txt = "ON" if new_s else "OFF"
            await event.answer(f"🖼️ Thumbnail display turned {state_txt} for this chat!", alert=True)
