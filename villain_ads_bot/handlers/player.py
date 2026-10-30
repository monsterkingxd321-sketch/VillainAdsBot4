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
from villain_ads_bot.services.userbot import download_media, extract_media_info, generate_silence, get_local_thumbnail

logger = logging.getLogger(__name__)

# Track active playing sessions per chat: { chat_id: { "bot": UserBot, "song_info": dict, "msg_id": int } }
_active_chat_players = {}
_chat_queues = {}
_track_timer_tasks = {}
_main_bot = None
_main_bot_username = "xtradsbot"
_preparing_chats = set()
_chat_locks = {}

def get_main_bot_username() -> str:
    global _main_bot_username
    if _main_bot_username:
        return _main_bot_username
    return getattr(config, "BOT_USERNAME", "xtradsbot") or "xtradsbot"

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

    # Prioritize userbot client, then main bot, then any running userbot
    try:
        clients = []
        if bot_obj is not None and getattr(bot_obj, "client", None) is not None:
            clients.append(bot_obj.client)
        bot = session.get("bot")
        if bot is not None and getattr(bot, "client", None) is not None and bot.client not in clients:
            clients.append(bot.client)
        if _main_bot is not None and _main_bot not in clients:
            clients.append(_main_bot)
        for _p, _b in list(_running_bots_snapshot().items()):
            c = getattr(_b, "client", None)
            if c is not None and c not in clients:
                clients.append(c)

        for client in clients:
            try:
                await client.delete_messages(chat_id, [msg_id])
                return True
            except Exception:
                pass
        return False
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
    bot = session.get("bot")
    if bot is not None and getattr(bot, "client", None) is not None:
        clients.append(bot.client)
    if _main_bot is not None and _main_bot not in clients:
        clients.append(_main_bot)
    for _p, _b in list(_running_bots_snapshot().items()):
        c = getattr(_b, "client", None)
        if c is not None and c not in clients:
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
        # No more songs in queue — cleanly stop stream and leave voice chat
        _chat_queues.pop(chat_id, None)
        active_sess = _active_chat_players.pop(chat_id, None)
        # The finished song's card must not stay in the chat.
        if active_sess:
            await _delete_card_by_session(active_sess, chat_id)
            for cb in active_sess.get("companion_bots", []):
                try:
                    await cb.stop_song(chat_id, leave_vc=True)
                except Exception:
                    pass
            if active_sess.get("bot"):
                bot = active_sess["bot"]
                try:
                    await bot.stop_song(chat_id, leave_vc=True)
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
            companion_bots = userbot_manager.get_running_userbots_in_vc(chat_id, exclude_bot=bot_obj)
            if not companion_bots:
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
                c_ok, c_msg, _ = await c_bot.play_song(
                    query="",
                    play_type=play_type,
                    chat_id=chat_id,
                    local_file=local_fp,
                    title=song_info.get("title"),
                    duration=song_info.get("duration", 30)
                )
                if c_ok:
                    await c_bot.unmute_mic(chat_id)
                    logger.info(f"Companion userbot {c_bot.session_id} streaming next track in VC {chat_id}")
                else:
                    logger.debug(f"Companion userbot {c_bot.session_id} next stream skipped: {c_msg}")
            except Exception as c_err:
                logger.warning(f"Companion userbot {c_bot.session_id} next stream error: {c_err}")

        async def _broadcast_companions_next():
            await asyncio.gather(*[_stream_companion_next(cb) for cb in companion_bots], return_exceptions=True)

        asyncio.create_task(_broadcast_companions_next())

    thumb_enabled = database.get_thumbnail_setting(chat_id)
    local_thumb = await get_local_thumbnail(song_info.get("thumb"), song_info.get("vid")) if thumb_enabled else None
    if local_thumb:
        song_info["thumb"] = local_thumb

    np_text = format_now_playing_text(song_info, requester_name, requester_id, stream_type=play_type, bot_count=len(companion_bots) + 1)
    buttons = build_now_playing_markup(chat_id, is_paused=False, is_muted=False)

    # The previous song's card is removed before the new one is posted so the
    # chat never accumulates stale player cards.
    previous = _active_chat_players.get(chat_id)
    if previous:
        await _delete_card_by_session(previous, chat_id)

    sent_msg = None
    if _main_bot:
        try:
            if thumb_enabled and local_thumb and os.path.exists(local_thumb):
                sent_msg = await asyncio.wait_for(
                    _main_bot.send_message(chat_id, np_text, file=local_thumb, buttons=buttons, parse_mode="html"),
                    timeout=3.0
                )
            else:
                sent_msg = await asyncio.wait_for(
                    _main_bot.send_message(chat_id, np_text, buttons=buttons, parse_mode="html"),
                    timeout=3.0
                )
        except Exception as send_err:
            logger.debug(f"Failed to send queue now playing via main_bot: {send_err}")
            
    # Fallback to userbot if main bot fails or is unavailable
    if not sent_msg and ub_client:
        try:
            if thumb_enabled and local_thumb and os.path.exists(local_thumb):
                sent_msg = await ub_client.send_file(chat_id, local_thumb, caption=np_text, parse_mode="html")
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
    global _main_bot, _main_bot_username
    _main_bot = client
    
    async def _resolve_bot_username():
        global _main_bot_username
        try:
            me = await client.get_me()
            if me and me.username:
                _main_bot_username = me.username
        except Exception:
            pass
    asyncio.create_task(_resolve_bot_username())

    # ------------------ Inline Query Handler for via @xtradsbot Player Cards ------------------
    @client.on(events.InlineQuery)
    async def player_inline_query_handler(event):
        query_text = (event.text or "").strip()
        if query_text.startswith("player:") or query_text.startswith("play:"):
            parts = query_text.split(":", 1)
            target_chat_str = parts[1].strip() if len(parts) > 1 else ""
            try:
                target_chat_id = int(target_chat_str)
            except Exception:
                target_chat_id = 0

            session = _active_chat_players.get(target_chat_id) or {}
            song_info = session.get("song_info") or {}
            bot_obj = session.get("bot")
            comp_bots = session.get("companion_bots") or []

            title = song_info.get("title", "Active Stream")
            duration = song_info.get("duration", 0)
            mins, secs = divmod(duration, 60)
            dur_str = f"{mins:02d}:{secs:02d}" if duration > 0 else "Live Stream"
            play_type = song_info.get("play_type", "audio")
            mode_emoji = "🎬 Video Stream" if play_type == "video" else "🎙️ Audio Stream"
            stream_src = getattr(bot_obj, "name", "UserBot") if bot_obj else "UserBot"
            total_bots = len(comp_bots) + 1
            bot_count_str = f" [{total_bots} Bots]" if total_bots > 1 else ""

            card_text = utils.format_html_message(
                f"<blockquote><b>» 🎵 Now Streaming{bot_count_str}</b>\n\n"
                f"<b>📌 Title :</b> <b>{title}</b>\n"
                f"<b>⏱️ Duration :</b> <code>{dur_str}</code>\n"
                f"<b>🎧 Mode :</b> <b>{mode_emoji}</b>\n"
                f"<b>🤖 Stream Source :</b> <b>{stream_src}</b>\n\n"
                f"⚡ <i>Controls : .pause, .resume, .skip, .queue, .stop, .vc</i></blockquote>"
            )

            buttons = build_now_playing_markup(target_chat_id, is_paused=False, is_muted=False)
            thumb_file = song_info.get("thumb")
            thumb_enabled = database.get_thumbnail_setting(target_chat_id)
            builder_res = None
            if thumb_enabled and thumb_file:
                local_thumb = await get_local_thumbnail(thumb_file, song_info.get("vid"))
                if local_thumb and os.path.exists(local_thumb) and os.path.getsize(local_thumb) > 1000:
                    try:
                        builder_res = await event.builder.photo(
                            file=local_thumb,
                            text=card_text,
                            parse_mode="html",
                            buttons=buttons
                        )
                    except Exception as p_err:
                        logger.warning(f"Could not build inline photo result: {p_err}")
                        builder_res = None

            if not builder_res:
                builder_res = event.builder.article(
                    title=f"Now Streaming: {title[:30]}",
                    description=f"Duration: {dur_str} | Mode: {mode_emoji}",
                    text=card_text,
                    parse_mode="html",
                    buttons=buttons
                )
            await event.answer([builder_res], cache_time=0)
            return

    """
    Registers all music player and voice chat handlers onto the Telethon client.
    """

    # ------------------ /play, .play, /vplay, .vplay, .playforce, /playforce commands ------------------
    @client.on(events.NewMessage(pattern=r"(?i)^[./!?](play|vplay|cplay|stream|vstream|playforce|vplayforce|cplayforce|forceplay|vforceplay)(?:@\w+)?(?:\s+([\s\S]*))?$"))
    async def play_command_handler(event):
        if await utils.guard(event, client):
            return

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

        # 0. Retrieve replied message BEFORE deleting event!
        reply_msg = None
        if event.is_reply:
            try:
                reply_msg = await event.get_reply_message()
            except Exception as e_reply:
                logger.debug(f"event.get_reply_message() exception: {e_reply}")
            if not reply_msg:
                r_id = getattr(getattr(event, "reply_to", None), "reply_to_msg_id", None)
                if r_id:
                    try:
                        reply_msg = await client.get_messages(chat_id, ids=r_id)
                    except Exception as e_get:
                        logger.debug(f"client.get_messages fallback exception: {e_get}")

        # Now safe to delete command event to keep chat clean
        try:
            await event.delete()
        except Exception:
            pass

        # Pre-resolve userbot early so we have it for fallback get_messages, download, and private chat routing
        bot_obj, err = await get_or_start_userbot_for_chat(user_id, chat_id)

        # Handle private chat ("in bot")
        if event.is_private:
            target_chat_id = getattr(bot_obj, "current_vc_chat_id", None) if bot_obj else None
            if not target_chat_id:
                running_vcs = [
                    getattr(b, "current_vc_chat_id", None)
                    for b in userbot_manager.get_running_userbots_for_owner(user_id)
                    if getattr(b, "current_vc_chat_id", None)
                ]
                if running_vcs:
                    target_chat_id = running_vcs[0]
            
            if not target_chat_id:
                await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ❌ No Active Voice Chat Found</b>\n\n"
                        "⚠️ <i>You sent this command in private chat, but your userbot is not currently connected to any Voice Chat in a group.</i>\n\n"
                        "💡 <b>Tip :</b> Join or start a Group Voice Chat with your userbot first, or send the command directly in the group!</blockquote>"
                    )
                )
                return
            chat_id = target_chat_id

        # If main bot couldn't read the replied message (e.g. Bot privacy in group), use userbot client!
        if event.is_reply and not reply_msg and bot_obj and bot_obj.client:
            r_id = getattr(getattr(event, "reply_to", None), "reply_to_msg_id", None)
            if r_id:
                try:
                    reply_msg = await bot_obj.client.get_messages(chat_id, ids=r_id)
                except Exception as ub_get_err:
                    logger.debug(f"bot_obj get_messages fallback exception: {ub_get_err}")

        local_file_path = None
        audio_title = None
        audio_duration = 0
        
        display_query = query or "Replied Media"
        progress_msg = await event.respond(f"<b>» 🎧 Playing :</b> <b>{display_query}</b>")
        
        # 1. Handle replied media (Audio, Video, Voice, Video Note, Document)
        if reply_msg and (
            reply_msg.audio or reply_msg.video or getattr(reply_msg, "video_note", None) or
            reply_msg.voice or reply_msg.document or getattr(reply_msg, "file", None)
        ):
            media_obj, audio_title, audio_duration = extract_media_info(reply_msg)
            if media_obj:
                os.makedirs(config.DOWNLOADS_DIR, exist_ok=True)
                # Auto-adjust play_type for audio/voice vs video
                is_voice_rec = bool(getattr(reply_msg, "voice", None))
                is_vid = bool(getattr(reply_msg, "video", None) or getattr(reply_msg, "video_note", None))
                if not is_vid and is_voice_rec:
                    play_type = "audio"
                elif is_vid and ("vplay" in cmd or "vstream" in cmd):
                    play_type = "video"

                try:
                    local_file_path = await client.download_media(
                        reply_msg,
                        file="downloads/",
                        progress_callback=lambda c, t: download_progress_sync(c, t, progress_msg, "DOWNLOADING MEDIA")
                    )
                except Exception as dl_err:
                    logger.warning(f"Main bot failed to download replied media: {dl_err}, trying userbot fallback...")
                    if bot_obj and bot_obj.client:
                        try:
                            local_file_path = await bot_obj.client.download_media(
                                reply_msg,
                                file="downloads/"
                            )
                        except Exception as ub_dl_err:
                            logger.error(f"Userbot download also failed: {ub_dl_err}")
                    if not local_file_path:
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

                if audio_title:
                    try:
                        await progress_msg.edit(f"<b>» 🎧 Playing :</b> <b>{audio_title}</b>")
                    except Exception:
                        pass
        elif not query:
            # If reply message couldn't be fetched by bot, try via userbot before giving up
            if event.is_reply and bot_obj and bot_obj.client:
                r_id = getattr(getattr(event, "reply_to", None), "reply_to_msg_id", None)
                if r_id:
                    try:
                        reply_msg = await bot_obj.client.get_messages(chat_id, ids=r_id)
                    except Exception:
                        pass
            if reply_msg and (
                reply_msg.audio or reply_msg.video or getattr(reply_msg, "video_note", None) or
                reply_msg.voice or reply_msg.document or getattr(reply_msg, "file", None)
            ):
                media_obj, audio_title, audio_duration = extract_media_info(reply_msg)
                if media_obj:
                    os.makedirs(config.DOWNLOADS_DIR, exist_ok=True)
                    is_voice_rec = bool(getattr(reply_msg, "voice", None))
                    is_vid = bool(getattr(reply_msg, "video", None) or getattr(reply_msg, "video_note", None))
                    if not is_vid and is_voice_rec:
                        play_type = "audio"
                    elif is_vid and ("vplay" in cmd or "vstream" in cmd):
                        play_type = "video"
                    try:
                        local_file_path = await bot_obj.client.download_media(
                            reply_msg,
                            file="downloads/"
                        )
                    except Exception as ub_dl_err:
                        logger.error(f"Userbot download failed: {ub_dl_err}")
                    if audio_title:
                        try:
                            await progress_msg.edit(f"<b>» 🎧 Playing :</b> <b>{audio_title}</b>")
                        except Exception:
                            pass

            if not local_file_path and not query:
                await progress_msg.edit(
                    utils.format_html_message(
                        f"<blockquote><b>» 💡 Music Player Usage Guide</b>\n\n"
                        f"• <code>.{cmd} &lt;song name&gt;</code> (E.G. <code>.{cmd} Faded Alan Walker</code>)\n"
                        f"• <code>/{cmd} &lt;song name&gt;</code>\n"
                        f"• <code>.playforce &lt;song name&gt;</code> (Force Play Now)\n"
                        f"• <code>.queue</code> / <code>.skip</code>\n"
                        f"• <b>Thumbnail Mode :</b> <code>.thumb on</code> Or <code>.thumb off</code>\n"
                        f"• Reply To Any Audio/Video/Voice File With <code>.{cmd}</code>\n\n"
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

        # 2. Pre-validate userbot BEFORE queue check
        if not bot_obj:
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
                    "thumb": (thumb if 'thumb' in locals() and thumb else None)
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
                if "peeruser" not in str(msg).lower() and "private user" not in str(msg).lower():
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
            companion_bots = userbot_manager.get_running_userbots_in_vc(chat_id, exclude_bot=bot_obj)
            if not companion_bots:
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
                    c_ok, c_msg, _ = await c_bot.play_song(
                        query="",
                        play_type=play_type,
                        chat_id=chat_id,
                        local_file=local_fp,
                        title=song_info.get("title"),
                        duration=song_info.get("duration", 30)
                    )
                    if c_ok:
                        await c_bot.unmute_mic(chat_id)
                        logger.info(f"Companion userbot {c_bot.session_id} streaming in VC {chat_id}")
                    else:
                        logger.debug(f"Companion userbot {c_bot.session_id} play skipped: {c_msg}")
                except Exception as c_err:
                    logger.warning(f"Companion userbot {c_bot.session_id} stream error: {c_err}")

            async def _broadcast_companions_play():
                await asyncio.gather(*[_stream_companion_play(cb) for cb in companion_bots], return_exceptions=True)

            asyncio.create_task(_broadcast_companions_play())
            
        # 5. Render Now Playing Card
        thumb_enabled = database.get_thumbnail_setting(chat_id)
        local_thumb = await get_local_thumbnail(song_info.get("thumb"), song_info.get("vid") or (vid if 'vid' in locals() else None)) if thumb_enabled else None
        if local_thumb:
            song_info["thumb"] = local_thumb

        np_text = format_now_playing_text(song_info, requester_name, user_id, stream_type=play_type, bot_count=len(companion_bots) + 1)
        buttons = build_now_playing_markup(chat_id, is_paused=False, is_muted=False)
        
        sent_msg = None
        # Remove temporary progress message
        try:
            await progress_msg.delete()
        except Exception:
            pass

        if thumb_enabled and local_thumb and os.path.exists(local_thumb):
            try:
                sent_msg = await event.respond(np_text, file=local_thumb, buttons=buttons, parse_mode="html")
            except Exception as send_err:
                logger.warning(f"Failed to send with thumb: {send_err}, sending text-only")
                try:
                    sent_msg = await event.respond(np_text, buttons=buttons, parse_mode="html")
                except Exception:
                    pass
        else:
            try:
                sent_msg = await event.respond(np_text, buttons=buttons, parse_mode="html")
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
                await bot_obj.stop_song(chat_id, leave_vc=False)
            
            empty_msg = await event.respond(
                utils.format_html_message(
                    "<blockquote><b>» ⏹️ Ending Song...</b></blockquote>"
                )
            )
            asyncio.create_task(utils.delayed_delete(empty_msg, 3))
            return
            
        prog = await event.respond(
            utils.format_html_message(
                "<blockquote><b>» ⏭️ Skipping Song...</b></blockquote>"
            )
        )
        await play_next_in_queue(chat_id, client)
        asyncio.create_task(utils.delayed_delete(prog, 3))

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
                msg = await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ⏸️ Pausing Song...</b></blockquote>"
                    )
                )
                asyncio.create_task(utils.delayed_delete(msg, 3))
            except Exception as e:
                utils.report_error("player.pause", e)
                msg = await event.respond(utils.format_html_message("<blockquote><b>» ❌ Error Pausing</b></blockquote>"))
                asyncio.create_task(utils.delayed_delete(msg, 3))

        elif cmd == "resume":
            try:
                pytg = await bot_obj.get_pytgcalls()
                await utils.pytg_resume(pytg, chat_id)
                msg = await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ▶️ Resuming Song...</b></blockquote>"
                    )
                )
                asyncio.create_task(utils.delayed_delete(msg, 3))
            except Exception as e:
                utils.report_error("player.resume", e)
                msg = await event.respond(utils.format_html_message("<blockquote><b>» ❌ Error Resuming</b></blockquote>"))
                asyncio.create_task(utils.delayed_delete(msg, 3))

        elif cmd in ("stop", "end"):
            _chat_queues.pop(chat_id, None)
            prev_timer = _track_timer_tasks.pop(chat_id, None)
            if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
                prev_timer.cancel()

            stale = _active_chat_players.pop(chat_id, None)
            if stale:
                await _delete_card_by_session(stale, chat_id)
                for cb in stale.get("companion_bots", []):
                    try:
                        await cb.stop_song(chat_id, leave_vc=True)
                    except Exception:
                        pass

            await bot_obj.stop_song(chat_id, leave_vc=True)
            msg = await event.respond(
                utils.format_html_message(
                    "<blockquote><b>» ⏹️ Ending Song...</b></blockquote>"
                )
            )
            asyncio.create_task(utils.delayed_delete(msg, 3))

        elif cmd == "mute":
            success, msg_err = await bot_obj.mute_mic(chat_id)
            if success:
                msg = await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» 🔇 Mic Muted</b></blockquote>"
                    )
                )
            else:
                msg = await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ❌ Failed To Mute</b></blockquote>"
                    )
                )
            asyncio.create_task(utils.delayed_delete(msg, 3))

        elif cmd == "unmute":
            success, msg_err = await bot_obj.unmute_mic(chat_id)
            if success:
                msg = await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» 🔊 Mic Unmuted</b></blockquote>"
                    )
                )
            else:
                msg = await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» ❌ Failed To Unmute</b></blockquote>"
                    )
                )
            asyncio.create_task(utils.delayed_delete(msg, 3))

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
            msg = await event.respond(f"<blockquote><b>{err}</b></blockquote>")
            asyncio.create_task(utils.delayed_delete(msg, 4))
            return

        if cmd in ("vc", "joinvc"):
            success, msg_text = await bot_obj.join_voice_chat(str(chat_id))
            if success:
                msg = await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» 🎙️ Connected To Voice Chat</b></blockquote>"
                    )
                )
            else:
                msg = await event.respond(
                    utils.format_html_message(
                        f"<blockquote><b>» ❌ Failed To Join Vc:</b> <code>{msg_text}</code></blockquote>"
                    )
                )
            asyncio.create_task(utils.delayed_delete(msg, 4))

        elif cmd in ("leavevc", "vcleft"):
            success, msg_text = await bot_obj.leave_voice_chat(chat_id)
            if success:
                msg = await event.respond(
                    utils.format_html_message(
                        "<blockquote><b>» 🔴 Left Voice Chat</b></blockquote>"
                    )
                )
            else:
                msg = await event.respond(
                    utils.format_html_message(
                        f"<blockquote><b>» ❌ Failed To Leave Vc:</b> <code>{msg_text}</code></blockquote>"
                    )
                )
            asyncio.create_task(utils.delayed_delete(msg, 4))

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
                        await cb.stop_song(chat_id, leave_vc=True)
                    except Exception:
                        pass
            success, msg = await bot_obj.stop_song(chat_id, leave_vc=True)
            if success:
                await event.answer("⏹️ Stream Ended.")
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
