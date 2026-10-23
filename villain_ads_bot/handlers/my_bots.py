import logging
import asyncio
import os
import re
import random
from typing import Optional, Set
from telethon import events, Button
from villain_ads_bot.data import database
from villain_ads_bot.data import models
from villain_ads_bot.core import utils
from villain_ads_bot.core import antispam
from villain_ads_bot.core import antispam_guard
from villain_ads_bot.core import ui
from villain_ads_bot.core import joiner
from villain_ads_bot import config
from villain_ads_bot.services import userbot_manager
from villain_ads_bot.services.userbot import join_vc_by_link, leave_chat_single, join_channel_single

logger = logging.getLogger(__name__)

# Global in-memory autoplay state per user (also referenced in callback handlers)
_np_autoplay_state = {}


def extract_media_info(msg):
    """
    Extracts file path, title, and duration from a message containing audio/voice/video.
    """
    media = msg.audio or msg.voice or msg.video or getattr(msg, "gif", None)
    if not media and msg.document:
        mime = getattr(msg.document, "mime_type", "")
        if mime.startswith("audio/") or mime.startswith("video/"):
            media = msg.document
            
    if not media:
        return None, None, 30
        
    title = "Uploaded Media"
    duration = 30
    
    # Extract title
    if msg.audio:
        title = getattr(msg.audio, "title", None) or getattr(msg.audio, "file_name", None) or "Audio File"
    elif msg.voice:
        title = "Voice Note"
    elif msg.video:
        title = getattr(msg.video, "file_name", None) or "Video File"
    elif msg.document:
        title = getattr(msg.document, "file_name", None) or "Document Media"
        
    # Extract duration
    for attr in getattr(media, "attributes", []):
        if hasattr(attr, "duration"):
            duration = attr.duration
            break
            
    return media, title, duration

import time

_last_progress_updates = {}

def download_progress_sync(current, total, msg_to_edit, operation_name="Downloading"):
    now = time.time()
    msg_id = id(msg_to_edit)
    last_time = _last_progress_updates.get(msg_id, 0.0)
    
    percent = (current / total) * 100 if total else 0.0
    
    # Only update at most once every 3.0 seconds to prevent Telegram Flood Wait
    if now - last_time < 3.0 and percent < 100.0:
        return
        
    _last_progress_updates[msg_id] = now
    
    # Define an async task to edit the message safely on the main loop
    async def _do_edit():
        filled = int(percent / 10)
        bar = "█" * filled + "░" * (10 - filled)
        text = (
            f"📥 <b>{operation_name}...</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📁 Size: `{total / (1024*1024):.2f} MB`\n"
            f"📊 Progress: `[{bar}] {percent:.1f}%`"
        )
        try:
            await msg_to_edit.edit(text)
        except Exception:
            pass
            
    asyncio.create_task(_do_edit())

# In-memory dictionary containing active prompt states for user interaction
# Structure: { user_id: { "phone": str, "action": str } }
_bot_action_states = {}
_admin_impersonation = {}

def set_admin_impersonation(admin_id, target_id):
    _admin_impersonation[admin_id] = target_id
    _admin_impersonation[str(admin_id)] = target_id

def get_effective_sessions(sender_id):
    target = _admin_impersonation.get(sender_id)
    if target is None:
        try:
            target = _admin_impersonation.get(int(sender_id))
        except (ValueError, TypeError):
            pass
    if target is None:
        target = _admin_impersonation.get(str(sender_id), sender_id)
        
    if target == "__ALL__" or str(target) == "__ALL__":
        return database.get_sessions(None)
    return database.get_sessions(target)

def is_system_all_mode(sender_id):
    if sender_id == "__ALL__" or str(sender_id) == "__ALL__":
        return True
    target = _admin_impersonation.get(sender_id)
    if target is None:
        try:
            target = _admin_impersonation.get(int(sender_id))
        except (ValueError, TypeError):
            pass
    if target is None:
        target = _admin_impersonation.get(str(sender_id))
    return target == "__ALL__" or str(target) == "__ALL__"

def is_session_owner_or_admin(sess, sender_id):
    if not sess:
        return False
    if is_system_all_mode(sender_id):
        return True
    try:
        s_id_int = int(sender_id) if sender_id and sender_id != "__ALL__" else None
        if s_id_int and (s_id_int in config.ORIGINAL_ADMIN_IDS or s_id_int in database.get_global_settings().get("admins", [])):
            return True
    except Exception:
        pass
    target = _admin_impersonation.get(sender_id)
    if target is None:
        try:
            target = _admin_impersonation.get(int(sender_id))
        except (ValueError, TypeError):
            pass
    if target is None:
        target = _admin_impersonation.get(str(sender_id), sender_id)
        
    if target == "__ALL__" or str(target) == "__ALL__":
        return True
    return str(sess.get("user_id")) == str(target) or str(sess.get("user_id")) == str(sender_id)


async def show_mock_dashboard(event, user_id: int, flash_message: Optional[str] = None):
    """
    Renders a mock UserBot control dashboard for unlogged users.
    """
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    text = f"{flash_message}\n\n" if flash_message else ""
    text += (
        f"<blockquote><b>» 🤖 Userbot Control Panel</b>\n\n"
        f"⚡ <b>Mode :</b> <b>Demo / Unlinked Mode</b>\n"
        f"🔴 <b>Status :</b> <b>No Active Account Linked</b>\n"
        f"⚠️ <b>Notice :</b> <i>{utils.get_text('account_login_first', lang)}</i>\n\n"
        f"💡 <i>Tap <b>Add / Login Userbot</b> Below To Connect Your Telegram Account.</i></blockquote>"
    )
    
    buttons = [
        [
            utils.styled_button("➕ Add / Login Userbot", "menu_add_bot", style="success")
        ],
        [
            utils.styled_button(utils.get_text("btn_start_bot", lang), "no_login_start", style="success"),
            utils.styled_button(utils.get_text("btn_stop_bot", lang), "no_login_stop", style="danger")
        ],
        [
            utils.styled_button(utils.get_text("btn_set_broadcast", lang), "no_login_broadcast", style="primary"),
            utils.styled_button(utils.get_text("btn_set_welcome", lang), "no_login_welcome", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_toggle_spam", lang, state="🔴 Off"), "no_login_spam", style="primary"),
            utils.styled_button(utils.get_text("btn_toggle_welcome", lang, state="🔴 Off"), "no_login_welcome_toggle", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_clone_profile", lang), "no_login_clone", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_help", lang), "help_bot_no_login", style="primary"),
            utils.styled_button(utils.get_text("btn_how_to_use", lang), "how_to_use_no_login", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_change_name", lang), "no_login_name", style="primary"),
            utils.styled_button(utils.get_text("btn_set_interval", lang), "no_login_interval", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_refresh_stats", lang), "no_login_stats", style="primary"),
            utils.styled_button(utils.get_text("btn_delete_bot", lang), "no_login_delete", style="danger")
        ],
        [
            utils.styled_button("🚪 Exit Admin Access", "admin_exit_impersonation", style="danger") if event.sender_id in _admin_impersonation else utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")
        ]
    ]
    
    try:
        if hasattr(event, "edit"):
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)

async def show_bots_list(event, user_id: int, flash_message: Optional[str] = None):
    """
    Renders the list of added accounts (UserBots) for the user.
    """
    user = database.get_user(user_id) if (user_id and user_id != "__ALL__") else None
    lang = user.get("language", "en") if user else "en"
    
    sender_id = getattr(event, "sender_id", user_id)
    if user_id == "__ALL__" or str(user_id) == "__ALL__":
        sessions = database.get_sessions(None)
    elif user_id:
        sessions = database.get_sessions(user_id)
    else:
        sessions = get_effective_sessions(sender_id)

    if not sessions:
        await show_mock_dashboard(event, user_id, flash_message)
        return
    
    text = ""
    if flash_message:
        text += f"{flash_message}\n\n"
    text += (
        f"{ui.card_open(utils.get_text('premium_bots_title', lang), '', utils.get_text('premium_bots_sub', lang))}\n"
    )
    buttons = [
        [
            utils.styled_button(utils.get_text("btn_all_slots", lang), "menu_all_slots", style="success")
        ]
    ]
    
    for s in sessions:
        phone = s.get("phone")
        # Sync status dynamically
        is_running = userbot_manager.is_bot_running(phone)
        status = "running" if is_running else "stopped"
        if s.get("status") != status:
            s["status"] = status
            database.save_session(s)
            
        status_emoji = "🟢" if status == "running" else "🔴"
        name = s.get("name") or "UserBot"
        username = s.get("username")
        user_display = f"@{username}" if username else phone
        
        text += f"\n{status_emoji} <b>{name}</b>  <code>{user_display}</code>"
        
        # Add a selection button for this bot
        buttons.append([
            utils.styled_button(
                f"{status_emoji} {name} ({user_display})", 
                f"select_bot_{phone}", 
                style="primary"
            )
        ])
    text += (
        f"\n\n{ui.help_hint(utils.get_text('premium_bots_hint', lang))}"
        f"\n{ui.card_close()}"
    )
    
    allowed = utils.get_allowed_slots(user_id)
    if len(sessions) < allowed:
        nth = len(sessions) + 1
        suffix = "nd" if nth == 2 else "rd" if nth == 3 else "th"
        if nth == 1: suffix = "st"
        buttons.append([
            utils.styled_button(f"➕ Connect {nth}{suffix} Userbot", "menu_add_bot", style="success")
        ])
        
    is_managing_other = (str(user_id) != str(sender_id)) or (sender_id in _admin_impersonation)
    if is_managing_other:
        buttons.append([
            utils.styled_button("🔙 My Userbots", "menu_my_bots", style="primary"),
            utils.styled_button("🚪 Exit To Admin", "admin_exit_impersonation", style="danger")
        ])
    else:
        buttons.append([
            utils.styled_button("📂 Load Accounts From Session Files", "user_load_sessions_from_disk", style="primary")
        ])
        buttons.append([utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")])
    
    try:
        await event.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)

async def show_bot_dashboard(event, phone: str, user_id: int, flash_message: Optional[str] = None):
    """
    Displays the detailed control dashboard for a single UserBot.
    """
    try:
        user = database.get_user(user_id) if (user_id and user_id != "__ALL__") else None
        lang = user.get("language", "en") if user else "en"
        
        sess = database.get_session(phone)
        if not sess or not is_session_owner_or_admin(sess, getattr(event, "sender_id", user_id)):
            text = "❌ Session not found."
            if flash_message:
                text = f"{flash_message}\n\n" + text
            try:
                await event.edit(text)
            except Exception:
                await event.respond(text)
            return
            
        # Sync status dynamically with manager memory running state
        is_running = userbot_manager.is_bot_running(phone)
        status = "running" if is_running else "stopped"
        
        if sess.get("status") != status:
            sess["status"] = status
            database.save_session(sess)
            
        status_emoji = "🟢" if status == "running" else "🔴"
        status_text = "Online" if status == "running" else "Offline"
        
        name = sess.get("name") or "UserBot"
        username = sess.get("username") or "None"
        user_handle = f"@{username}" if username and username != "None" else f"`{phone}`"
        
        settings = sess.get("settings", {})
        auto_spam = "🟢 On" if settings.get("auto_spam") else "🔴 Off"
        auto_welcome = "🟢 On" if settings.get("auto_welcome") else "🔴 Off"
        auto_reply = "🟢 On" if settings.get("auto_reply") else "🔴 Off"
        auto_add_contact = "🟢 On" if settings.get("auto_add_contact") else "🔴 Off"
        
        text = ""
        if flash_message:
            text += f"{flash_message}\n\n"
            
        text += (
            f"{ui.card('Userbot Control Panel', 'Everything for this account in one place', name, chr(129496))}\n\n"
            f"{ui.kv('Account', user_handle, chr(128100))}\n"
            f"{ui.kv('Status', status_text, chr(128994))}\n"
            f"{ui.kv('Auto broadcast', auto_spam, chr(128225))}\n"
            f"{ui.kv('Auto welcome', auto_welcome, chr(128075))}\n"
            f"{ui.kv('Tag auto-reply', auto_reply, chr(128172))}\n"
            f"{ui.kv('Auto contact', auto_add_contact, chr(128101))}"
        )
        
        # Configure dashboard buttons (Large full-width layout)
        buttons = []
        rows = []
        
        # Row 0: Start and Stop side-by-side
        rows.append([
            ("btn_start_bot", f"start_bot_{phone}"),
            ("btn_stop_bot", f"stop_bot_{phone}")
        ])
        
        # Row 0.5: Restart Bot
        rows.append([
            ("btn_restart_bot", f"restart_bot_{phone}")
        ])
            
        # Row 1: Set Broadcast Message (Full width)
        rows.append([
            ("btn_set_broadcast", f"set_broadcast_{phone}")
        ])

        
        
        
        # The proxy section is OWNER ONLY - it must not appear in the ads
        # userbot dashboard.

        # Row 1.1: Broadcast Now - the MAIN action of an ads bot.
        # It is on the dashboard directly, no extra sub menu needed.
        rows.append([
            ("\U0001f4e2 Broadcast Now", f"broadcast_now_{phone}")
        ])

        # Row 1.2: Set DM Welcome Message (Full width)
        rows.append([
            ("btn_set_welcome", f"set_welcome_{phone}")
        ])
        
        # Row 1.5: Set Tag Auto-Reply Messages (Full width)
        rows.append([
            ("btn_set_auto_reply", f"set_auto_reply_{phone}")
        ])
        
        # Row 1.6: Set Run Timer (Full width)
        rows.append([
            ("⏱️ Set Run Timer", f"set_run_timer_{phone}")
        ])
        
        # Row 1.8: Voice Chat (VC) Menu & Music Commands Guide
        rows.append([
            ("\U0001f399\ufe0f VC & Music", f"vc_menu_{phone}"),
            ("\U0001f465 Group Joining", f"grp_menu_{phone}")
        ])
        
        # Row 2: Auto Feature Toggles (Spam, Welcome, Tag Reply, Contact)
        rows.append([
            ("btn_toggle_spam", f"toggle_spam_{phone}", auto_spam),
            ("btn_toggle_welcome", f"toggle_welcome_{phone}", auto_welcome)
        ])
        rows.append([
            ("btn_toggle_auto_reply", f"toggle_reply_{phone}", auto_reply),
            ("btn_toggle_add_contact", f"toggle_add_contact_{phone}", auto_add_contact)
        ])
        
        # Row 3: Profile & Timing Settings
        rows.append([
            ("btn_clone_profile", f"clone_profile_{phone}"),
            ("btn_change_name", f"change_name_{phone}")
        ])
        rows.append([
            ("btn_set_interval", f"set_interval_{phone}")
        ])
        
        # Row 4: Help & Info
        rows.append([
            ("btn_help", f"help_bot_{phone}"),
            ("btn_how_to_use", f"how_to_use_{phone}")
        ])
        
        # Row 5: Refresh Stats & Delete Bot
        rows.append([
            ("btn_refresh_stats", f"refresh_stats_{phone}")
        ])
        rows.append([
            ("btn_delete_bot", f"delete_bot_{phone}", None, "danger")
        ])
        
        # Row 6: Back to Bots
        sender_id = getattr(event, "sender_id", user_id)
        is_other_bot = sess and str(sess.get("user_id")) != str(sender_id)
        if is_other_bot:
            rows.append([
                ("🔙 My Userbots", "menu_my_bots", None, "primary"),
                ("👑 Admin Panel", "menu_admin", None, "danger")
            ])
        else:
            rows.append([
                ("btn_back_to_bots", "menu_my_bots", None, "primary")
            ])


        styles = ["success", "danger", "primary"]
        for i, row in enumerate(rows):
            row_style = styles[i % len(styles)]
            row_buttons = []
            for item in row:
                key = item[0]
                callback = item[1]
                state = item[2] if len(item) > 2 else None
                override_style = item[3] if len(item) > 3 else None
                
                if key == "btn_start_bot":
                    style = "success"
                elif key in ("btn_stop_bot", "btn_delete_bot"):
                    style = "danger"
                elif key == "btn_restart_bot":
                    style = None
                elif override_style:
                    style = override_style
                else:
                    style = row_style
                    
                if key == "btn_vc_menu":
                    label = "🎙️ Vc + Grp Joining"
                elif key == "btn_music_guide":
                    label = "🎵 Music Commands"
                elif key.startswith("btn_"):
                    if state is not None:
                        label = utils.get_text(key, lang, state=state)
                    else:
                        label = utils.get_text(key, lang)
                else:
                    label = key
                    
                row_buttons.append(utils.styled_button(label, callback, style=style))
            buttons.append(row_buttons)
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)
            
    except Exception as e:
        logger.exception("Error rendering bot dashboard")
        utils.report_error("my_bots.render_dashboard", e)
        err_msg = "<blockquote><b>» ❌ Dashboard Error</b>\n\n⚠️ <i>Something went wrong while loading the dashboard. The error was reported to the admin team.</i></blockquote>"
        try:
            await event.edit(err_msg)
        except Exception:
            await event.respond(err_msg)

async def show_all_slots_dashboard(event, user_id: int, flash_message: Optional[str] = None, fetch_all: bool = False):
    """
    Renders the dashboard for controlling all userbots at once.
    """
    sender_id = getattr(event, "sender_id", user_id)
    is_sys_all = fetch_all or is_system_all_mode(sender_id) or is_system_all_mode(user_id) or user_id == "__ALL__"
    
    if is_sys_all:
        sessions = database.get_sessions(None)
    else:
        sessions = database.get_sessions(user_id)
        
    user = database.get_user(sender_id) if (sender_id and sender_id != "__ALL__") else None
    lang = user.get("language", "en") if user else "en"
        
    if not sessions:
        text = "⚠️ <b>All Slots Dashboard</b>\n\nNo connected UserBots found in system." if is_sys_all else "⚠️ <b>All Slots Dashboard</b>\n\nNo connected UserBots found."
        back_btn = [utils.styled_button("🚪 Exit Admin Access", "admin_exit_impersonation", style="danger")] if (sender_id in _admin_impersonation or is_sys_all) else [utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")]
        buttons = [back_btn]
        try:
            if hasattr(event, "edit"):
                await event.edit(text, buttons=buttons)
            else:
                await event.respond(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)
        return
        
    total_slots = len(sessions)
    running_bots = sum(1 for s in sessions if userbot_manager.is_bot_running(s["phone"]))
    stopped_bots = total_slots - running_bots
    
    any_spam_on = any(s.get("settings", {}).get("auto_spam", False) for s in sessions)
    any_welcome_on = any(s.get("settings", {}).get("auto_welcome", False) for s in sessions)
    any_reply_on = any(s.get("settings", {}).get("auto_reply", False) for s in sessions)
    any_add_contact_on = any(s.get("settings", {}).get("auto_add_contact", False) for s in sessions)
    
    spam_state_display = "🟢 ON" if any_spam_on else "🔴 OFF"
    welcome_state_display = "🟢 ON" if any_welcome_on else "🔴 OFF"
    reply_state_display = "🟢 ON" if any_reply_on else "🔴 OFF"
    add_contact_state_display = "🟢 ON" if any_add_contact_on else "🔴 OFF"
    
    text = ""
    if flash_message:
        text += f"{flash_message}\n\n"
        
    header_title = "All System Userbots Control (Owner Mode)" if is_sys_all else "All Slots Control Dashboard"
    desc_text = "Control every connected userbot across the entire system simultaneously." if is_sys_all else "Control all of your userbots simultaneously from this panel."

    text += (
        f"{ui.card_open(utils.get_text('premium_slots_title', lang), '', utils.get_text('premium_slots_sub', lang))}\n\n"
        f"{ui.kv('Total linked bots', str(total_slots), '📊')}\n"
        f"{ui.kv('Active', f'🟢 {running_bots}', '⚡')}\n"
        f"{ui.kv('Stopped', f'🔴 {stopped_bots}', '⏹️')}\n\n"
        f"{ui.kv('Auto broadcast (all)', spam_state_display, '✉️')}\n"
        f"{ui.kv('Auto welcome (all)', welcome_state_display, '👋')}\n"
        f"{ui.kv('Tag auto-reply (all)', reply_state_display, '💬')}\n"
        f"{ui.kv('Auto contact (all)', add_contact_state_display, '👥')}\n\n"
        f"{ui.card_close()}"
    )
    
    buttons = [
        # Row 0: Start All, Stop All
        [
            utils.styled_button("🟢 Start All", "all_slots_start", style="success"),
            utils.styled_button("🔴 Stop All", "all_slots_stop", style="danger")
        ],
        # Row 0.5: Restart All
        [
            utils.styled_button("🔄 Restart All Userbots", "all_slots_restart", style="primary")
        ],
        # Row 1: Set All Broadcast
        [
            utils.styled_button("✉️ Set All Broadcast Text", "all_slots_set_broadcast", style="primary")
        ],
        # Row 1.1: Broadcast NOW on every running userbot (live progress)
        [
            utils.styled_button("📢 Broadcast Now (All)", "all_slots_broadcast_now", style="success")
        ],
        # Row 1.2: Set All Welcome
        [
            utils.styled_button("👋 Set All Welcome", "all_slots_set_welcome", style="primary")
        ],
        # Row 1.5: Set All Tag Auto-Reply
        [
            utils.styled_button("💬 Set All Tag Auto-Reply", "all_slots_set_auto_reply", style="primary")
        ],
        # Row 1.6: Set All Run Timer
        [
            utils.styled_button("⏱️ Set Run Timer (All)", "all_slots_set_run_timer", style="primary")
        ],
        # Row 1.8: Voice Chat (VC) Menu (All)
        [
            utils.styled_button("🎙️ Vc + Grp Joining (All)", "all_slots_vc_menu", style="success")
        ],
        # Row 2: Auto Feature Toggles (All)
        [
            utils.styled_button(f"🔄 Auto Broadcast (All): {spam_state_display}", "all_slots_toggle_spam", style="primary"),
            utils.styled_button(f"👋 Auto-Welcome (All): {welcome_state_display}", "all_slots_toggle_welcome", style="primary")
        ],
        [
            utils.styled_button(f"💬 Tag Auto-Reply (All): {reply_state_display}", "all_slots_toggle_reply", style="primary"),
            utils.styled_button(f"👥 Auto-Contact (All): {add_contact_state_display}", "all_slots_toggle_add_contact", style="primary")
        ],
        # Row 3: Clone Profile (All)
        [
            utils.styled_button("👤 Clone Profile (All)", "all_slots_clone_profile", style="primary"),
            utils.styled_button("✏️ Change Name (All)", "all_slots_change_name", style="primary")
        ],
        # Row 4: Timing & Help
        [
            utils.styled_button("⏱️ Set Timing & Delays (All)", "all_slots_set_interval", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_help", lang), "all_slots_help", style="primary"),
            utils.styled_button(utils.get_text("btn_how_to_use", lang), "all_slots_how_to_use", style="primary")
        ],
        # Row 5: Refresh Stats & Delete
        [
            utils.styled_button("🔄 Refresh Stats (All)", "all_slots_refresh_stats", style="primary")
        ],
        [
            utils.styled_button("🗑️ Delete All Userbots", "all_slots_delete", style="danger")
        ],
        # Row 6: Back to Bots / Exit
        [
            utils.styled_button("🚪 Exit Admin Access", "admin_exit_impersonation", style="danger") if event.sender_id in _admin_impersonation else utils.styled_button(utils.get_text("btn_back_to_bots", lang), "menu_my_bots", style="danger")
        ]
    ]
    
    try:
        if hasattr(event, "edit"):
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


async def render_broadcast_menu(event, phone: str, user_id: int):
    sess = database.get_session(phone)
    if not sess or not is_session_owner_or_admin(sess, getattr(event, "sender_id", user_id)):
        await show_bot_dashboard(event, phone, user_id, flash_message="<blockquote><b>» ❌ Session Not Found.</b></blockquote>")
        return
        
    settings = sess.get("settings", {}) if sess else {}
    mode = settings.get("broadcast_mode", "single")
    single_msg = settings.get("broadcast_msg")
    multi_msgs = settings.get("broadcast_messages", [])
    name = sess.get("name") or "UserBot"
    
    mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "✉️ Single (Normal)"
    single_status = "✅ <b>Set</b>" if single_msg else "❌ <b>Empty</b>"
    multiple_status = f"✅ <b>Set ({len(multi_msgs)} Msgs)</b>" if multi_msgs else "❌ <b>Empty</b>"
    
    text = (
        f"<blockquote><b>» ✉️ Broadcast Message Settings</b>\n\n"
        f"👤 <b>Userbot :</b> <b>{name}</b>\n\n"
        f"• <b>Current Mode :</b> <b>{mode_display}</b>\n"
        f"• <b>Single Msg :</b> {single_status}\n"
        f"• <b>Multiple Msgs :</b> {multiple_status}\n\n"
        f"💡 <i>How To Set Multiple Messages : Click 'Set Multiple Messages' And Send Messages Separated By Commas (<code>,</code>).</i></blockquote>"
    )
    
    buttons = [
        [
            utils.styled_button("✉️ Set Single Message", f"set_single_msg_{phone}", style="primary"),
            utils.styled_button("📚 Set Multiple Messages", f"set_multi_msg_{phone}", style="primary")
        ],
        [
            utils.styled_button(f"🔄 Mode : {mode.upper()}", f"toggle_broadcast_mode_{phone}", style="primary")
        ],
        [
            utils.styled_button("🔙 Back To Dashboard", f"select_bot_{phone}", style="danger")
        ]
    ]
    try:
        await event.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


async def render_welcome_menu(event, phone: str, user_id: int):
    sess = database.get_session(phone)
    if not sess or not is_session_owner_or_admin(sess, getattr(event, "sender_id", user_id)):
        await show_bot_dashboard(event, phone, user_id, flash_message="<blockquote><b>» ❌ Session Not Found.</b></blockquote>")
        return
        
    settings = sess.get("settings", {}) if sess else {}
    mode = settings.get("welcome_mode", "single")
    single_msg = settings.get("welcome_msg")
    multi_msgs = settings.get("welcome_messages", [])
    name = sess.get("name") or "UserBot"
    
    mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "👋 Single (Normal)"
    single_status = "✅ <b>Set</b>" if single_msg else "❌ <b>Empty</b>"
    multiple_status = f"✅ <b>Set ({len(multi_msgs)} Msgs)</b>" if multi_msgs else "❌ <b>Empty</b>"
    
    text = (
        f"<blockquote><b>» 👋 Dm Welcome Message Settings</b>\n\n"
        f"👤 <b>Userbot :</b> <b>{name}</b>\n\n"
        f"• <b>Current Mode :</b> <b>{mode_display}</b>\n"
        f"• <b>Single Welcome :</b> {single_status}\n"
        f"• <b>Multiple Welcomes :</b> {multiple_status}\n\n"
        f"💡 <i>How To Set Multiple Welcomes : Click 'Set Multiple Welcomes' And Send Messages Separated By Commas (<code>,</code>).</i></blockquote>"
    )
    
    buttons = [
        [
            utils.styled_button("👋 Set Single Welcome", f"set_single_welcome_{phone}", style="primary"),
            utils.styled_button("📚 Set Multiple Welcomes", f"set_multi_welcome_{phone}", style="primary")
        ],
        [
            utils.styled_button(f"🔄 Mode : {mode.upper()}", f"toggle_welcome_mode_{phone}", style="primary")
        ],
        [
            utils.styled_button("🔙 Back To Dashboard", f"select_bot_{phone}", style="danger")
        ]
    ]
    try:
        await event.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


async def render_auto_reply_menu(event, phone: str, user_id: int):
    sess = database.get_session(phone)
    if not sess or not is_session_owner_or_admin(sess, getattr(event, "sender_id", user_id)):
        await show_bot_dashboard(event, phone, user_id, flash_message="<blockquote><b>» ❌ Session Not Found.</b></blockquote>")
        return
        
    settings = sess.get("settings", {}) if sess else {}
    mode = settings.get("auto_reply_mode", "single")
    single_msg = settings.get("auto_reply_msg")
    multi_msgs = settings.get("auto_reply_messages", [])
    name = sess.get("name") or "UserBot"
    
    mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "💬 Single (Normal)"
    single_status = "✅ <b>Set</b>" if single_msg else "❌ <b>Empty</b>"
    multiple_status = f"✅ <b>Set ({len(multi_msgs)} Msgs)</b>" if multi_msgs else "❌ <b>Empty</b>"
    
    text = (
        f"<blockquote><b>» 💬 Group Tag Auto-Reply Settings</b>\n\n"
        f"👤 <b>Userbot :</b> <b>{name}</b>\n\n"
        f"• <b>Current Mode :</b> <b>{mode_display}</b>\n"
        f"• <b>Single Reply :</b> {single_status}\n"
        f"• <b>Multiple Replies :</b> {multiple_status}\n\n"
        f"💡 <i>How To Set Multiple Replies : Click 'Set Multiple Replies' And Send Messages Separated By Commas (<code>,</code>).</i></blockquote>"
    )
    
    buttons = [
        [
            utils.styled_button("💬 Set Single Reply", f"set_single_reply_{phone}", style="primary"),
            utils.styled_button("📚 Set Multiple Replies", f"set_multi_reply_{phone}", style="primary")
        ],
        [
            utils.styled_button(f"🔄 Mode : {mode.upper()}", f"toggle_reply_mode_{phone}", style="primary")
        ],
        [
            utils.styled_button("🔙 Back To Dashboard", f"select_bot_{phone}", style="danger")
        ]
    ]
    try:
        await event.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


def register_handlers(client):
    
    # ------------------ New Features / Handlers ------------------
    @client.on(events.CallbackQuery(pattern="^all_slots_toggle_reply$"))
    async def all_slots_toggle_reply_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
        any_reply_on = any(s.get("settings", {}).get("auto_reply", False) for s in sessions)
        target_state = not any_reply_on
        for s in sessions:
            s.setdefault("settings", {})["auto_reply"] = target_state
            database.save_session(s)
            userbot_manager.reload_bot_settings(s["phone"])
        word = "ENABLED" if target_state else "DISABLED"
        await show_all_slots_dashboard(event, user_id, flash_message=f"💬 <b>Tag Auto-Reply {word} for all userbots!</b>", fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern="^menu_all_slots$"))
    async def menu_all_slots_callback(event):
        
        user_id = event.sender_id
        if user_id in _bot_action_states:
            _bot_action_states.pop(user_id)
        effective_id = _admin_impersonation.get(user_id, user_id)
        await show_all_slots_dashboard(event, effective_id)


    @client.on(events.CallbackQuery(pattern=r"^grp_menu_(.+)$"))
    async def grp_menu_callback(event):
        """The GROUP JOINING sub menu - one place for every group action."""
        phone = event.pattern_match.group(1).strip()
        bot_obj = userbot_manager.find_running_bot(phone)
        if not bot_obj:
            await event.answer("\u26a0\ufe0f The userbot is not running.", alert=True)
            return

        text = (
            f"{ui.card('Group Joining', f'Account : {bot_obj.name}', 'Join and leave groups', chr(128101))}\n\n"
            f"{ui.help_hint('Joining many groups at once is handled automatically - a flood limit is waited out and the remaining groups are joined afterwards.')}"
            f"\n{ui.footer()}</blockquote>"
        )
        buttons = [
            [
                utils.styled_button("\U0001f517 Join Group", f"vc_join_grp_{phone}", style="success"),
                utils.styled_button("\U0001f4da Join Many Groups", f"vc_join_multi_grp_{phone}", style="success"),
            ],
            [
                utils.styled_button("\U0001f6d1 Leave Group", f"vc_leave_grp_{phone}", style="danger"),
            ],
            [
                utils.styled_button("\U0001f519 Back To Dashboard", f"select_bot_{phone}", style="primary")
            ],
        ]
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=r"^bcast_menu_(.+)$"))
    async def bcast_menu_callback(event):
        """The BROADCAST & SPAM sub menu - one place for every broadcast tool."""
        phone = event.pattern_match.group(1).strip()
        sess = database.get_session(phone)
        if not sess:
            await event.answer("\u274c Session not found.", alert=True)
            return
        settings = sess.get("settings", {}) or {}
        stats = sess.get("stats", {}) or {}
        last = stats.get("last_broadcast") or {}
        budget = antispam.budget_state(sess)
        msg_count = len(utils.get_broadcast_messages(settings))

        last_line = ""
        if last:
            last_sent = str(last.get("sent", 0))
            last_total = str(last.get("total", 0))
            last_text = last_sent + "/" + last_total + " groups"
            last_line = f"{ui.kv('Last broadcast', last_text, chr(128200))}\n"

        account_name = sess.get("name") or phone
        text = (
            f"{ui.card('Broadcast & Spam', 'Account : ' + str(account_name), 'Your advertisement and its protection', chr(128225))}\n\n"
            f"{ui.kv('Auto broadcast', 'ON' if settings.get('auto_spam') else 'OFF', chr(128225))}\n"
            f"{ui.kv('Messages configured', str(msg_count), chr(128172))}\n"
            f"{ui.kv('Account health', antispam_guard.health_text(sess), chr(128202))}\n"
            f"{last_line}"
            f"\n{ui.help_hint('The message really differs per group and a new account warms up first - that is what keeps it out of @SpamBot.')}"
            f"\n{ui.footer()}</blockquote>"
        )
        buttons = [
            [
                utils.styled_button("\U0001f4dd Set Message", f"set_broadcast_{phone}", style="primary"),
            ],
            [
                utils.styled_button("\U0001f4e2 Broadcast Now", f"broadcast_now_{phone}", style="success"),
            ],
            [
                utils.styled_button("\u23f1\ufe0f Run Timer", f"set_run_timer_{phone}", style="primary"),
                utils.styled_button("\u23f1\ufe0f Timing", f"set_interval_{phone}", style="primary"),
            ],
            [
                utils.styled_button("\U0001f399\ufe0f VC & Music", f"vc_menu_{phone}", style="primary"),
                utils.styled_button("\U0001f519 Back To Dashboard", f"select_bot_{phone}", style="primary")
            ],
        ]
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=r"^vc_menu_(.+)$"))
    async def vc_menu_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            try:
                await event.answer("⚠️ Userbot is not running.", alert=True)
            except Exception:
                pass
            return
            
        vc_chat_id = getattr(bot_obj, "current_vc_chat_id", None)
        vc_status = "🟢 Connected" if vc_chat_id else "🔴 Disconnected"
        
        sec_vc_join = (
            "• Join Vc — connect the userbot to the active voice chat\n"
            "• Join All Vcs — connect to every group VC\n"
            "• Leave Vc / Leave All Vcs — disconnect again"
        )
        sec_vc_playing = (
            "• Play Song — stream audio / video into the voice chat\n"
            "• Pause / Resume / Skip / Stop / Queue — playback controls\n"
            "• Mute / Unmute — control the userbot microphone"
        )
        sec_group = (
            "• Join Group — join a group via an invite link\n"
            "• Join Multi Groups — join many groups (flood limits are handled automatically)\n"
            "• Leave Group — leave a group / channel"
        )
        text = (
            f"{ui.card(utils.get_text('vc_menu_title', lang), 'Voice chat & group management', 'Every action of your userbot in one place', '🎙️')}\n\n"
            f"{ui.kv('VC status', vc_status, '📌')}\n\n"
            f"{ui.section('🎙️ ' + utils.get_text('vc_section_join', lang), sec_vc_join)}\n\n"
            f"{ui.section('🎵 ' + utils.get_text('vc_section_playing', lang), sec_vc_playing)}\n\n"
            f"{ui.section('👥 ' + utils.get_text('vc_section_group', lang), sec_group)}"
            f"\n{ui.footer()}</blockquote>"
        )
        
        buttons = [
            # ── SECTION 1 : VC JOIN ──
            [
                utils.styled_button("🎙️ Join Vc", f"vc_join_{phone}", style="success"),
                utils.styled_button("🔴 Leave Vc", f"vc_leave_{phone}", style="danger")
            ],
            [
                utils.styled_button("🎙️ Join All Group Vcs", f"vc_join_all_{phone}", style="success"),
                utils.styled_button("🔴 Leave All Group Vcs", f"vc_leave_all_{phone}", style="danger")
            ],
            # ── SECTION 2 : VC PLAYING ──
            [
                utils.styled_button("🎵 Play Song", f"play_song_{phone}", style="primary"),
                utils.styled_button("📖 Commands Guide", f"music_guide_{phone}", style="primary")
            ],
            # ── SECTION 3 : GROUP JOINING & LEAVING ──
            [
                utils.styled_button("🔗 Join Group", f"vc_join_grp_{phone}", style="primary"),
                utils.styled_button("📚 Join Multi Groups", f"vc_join_multi_grp_{phone}", style="primary")
            ],
            [
                utils.styled_button("❌ Leave Group", f"vc_leave_grp_{phone}", style="danger")
            ],
            [
                utils.styled_button("🔙 Back To Dashboard", f"select_bot_{phone}", style="primary")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^music_guide_(.+)$"))
    async def music_guide_callback(event):
        phone = event.pattern_match.group(1).strip()
        guide_text = (
            f"<blockquote><b>» 🎵 Complete Group Music Commands Guide</b>\n\n"
            f"<b>1. Audio / Video Streaming :</b>\n"
            f"• <code>.play &lt;song name&gt;</code> Or <code>/play &lt;song name&gt;</code>\n"
            f"• <code>.vplay &lt;song name&gt;</code> Or <code>/vplay &lt;song name&gt;</code>\n"
            f"• <code>.play &lt;youtube link&gt;</code>\n"
            f"• Reply To Any Audio/Video File With <code>.play</code> Or <code>/play</code>\n\n"
            f"<b>2. Playback Controls :</b>\n"
            f"• <code>.pause</code> / <code>/pause</code> — Pause Active Stream\n"
            f"• <code>.resume</code> / <code>/resume</code> — Resume Paused Stream\n"
            f"• <code>.stop</code> / <code>.end</code> — Stop Stream And Clear\n"
            f"• <code>.mute</code> / <code>.unmute</code> — Mute / Unmute Bot Mic In Vc\n\n"
            f"<b>3. Voice Chat & Channel :</b>\n"
            f"• <code>.vc</code> / <code>.joinvc</code> — Connect Userbot To Vc\n"
            f"• <code>.leavevc</code> / <code>.vcleft</code> — Disconnect From Vc\n\n"
            f"<b>4. Thumbnail & Download :</b>\n"
            f"• <code>.thumb on</code> — Show Song Artwork Banner\n"
            f"• <code>.thumb off</code> — Clean Text-Only Stream Card (No Image)\n"
            f"• <code>.song &lt;song name&gt;</code> — Download MP3 File Directly To Telegram\n\n"
            f"💡 <i>Tip : Run Any Of These Commands Directly In The Group Where Your Userbot Is Added!</i></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Back To Dashboard", f"select_bot_{phone}", style="primary")]]
        try:
            await event.edit(guide_text, buttons=buttons)
        except Exception:
            await event.respond(guide_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_menu$"))
    async def all_slots_vc_menu_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(event.sender_id) if event.sender_id != "__ALL__" else None
        lang = user.get("language", "en") if user else "en"
        
        sessions = get_effective_sessions(event.sender_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        
        vc_connected_count = sum(
            1 for p in running_phones
            if getattr(userbot_manager._running_bots.get(p), "current_vc_chat_id", None)
        )
        
        header_vc = "VC + Group Control (All System Bots)" if is_system_all_mode(event.sender_id) else "VC + Group Control (All Slots)"
        all_vc_join = (
            "• Join Vc / Leave Vc — connect or disconnect every userbot\n"
            "• Join All Active Vcs / Leave All — sweep every group VC"
        )
        all_vc_playing = "• Play Song — stream on all running userbots at once"
        all_group = (
            "• Join Group / Join Multi Groups — every userbot joins (flood limits are handled automatically)\n"
            "• Leave Group — every userbot leaves"
        )
        text = (
            f"{ui.card(header_vc, 'Bulk voice chat & group management', 'Control every userbot at once', '🎙️')}\n\n"
            f"{ui.kv('VC connected bots', f'{vc_connected_count} / {len(sessions)}', '📌')}\n\n"
            f"{ui.section('🎙️ VC Join', all_vc_join)}\n\n"
            f"{ui.section('🎵 VC Playing', all_vc_playing)}\n\n"
            f"{ui.section('👥 Group Joining & Leaving', all_group)}"
            f"\n{ui.footer()}</blockquote>"
        )
        
        buttons = [
            # ── SECTION 1 : VC JOIN ──
            [
                utils.styled_button("🎙️ Join Vc (All)", "all_slots_vc_join", style="success"),
                utils.styled_button("🔴 Leave Vc (All)", "all_slots_vc_leave", style="danger")
            ],
            [
                utils.styled_button("🎙️ Join All Active Vcs (All)", "all_slots_vc_join_all", style="success"),
                utils.styled_button("🔴 Leave All Active Vcs (All)", "all_slots_vc_leave_all", style="danger")
            ],
            # ── SECTION 2 : VC PLAYING ──
            [
                utils.styled_button("🎵 Play Song (All)", "all_slots_play_song", style="primary")
            ],
            # ── SECTION 3 : GROUP JOINING & LEAVING ──
            [
                utils.styled_button("🔗 Join Group (All)", "all_slots_vc_join_grp", style="primary"),
                utils.styled_button("📚 Join Multi Groups (All)", "all_slots_vc_join_multi_grp", style="primary")
            ],
            [
                utils.styled_button("❌ Leave Group (All)", "all_slots_vc_leave_grp", style="danger")
            ],
            [
                utils.styled_button("🔙 Back", "menu_all_slots", style="primary")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    async def _send_broadcast_report(phone: str, stats: dict, trigger: str = "auto"):
        """
        Sends the broadcast result to the owner of the userbot.
        The user always knows what happened - that is the proof of work.
        """
        try:
            sess = database.get_session(phone)
            if not sess:
                return
            owner_id = sess.get("user_id")
            if not owner_id:
                return

            total = stats.get("total", 0)
            sent = stats.get("sent", 0)
            failed = stats.get("failed", 0)
            skipped = stats.get("skipped", 0)
            flood = stats.get("flood_waits", 0)
            elapsed = stats.get("elapsed", 0)
            remaining = max(0, total - sent - failed - skipped)

            if trigger == "test":
                title = "🧪 Test Send"
                body = (
                    f"{ui.kv('Test group', str(stats.get('test_group', '?')), '🗢️')}\n"
                    f"{ui.kv('Delivered', 'YES' if stats.get('sent') else 'NO', '✅')}\n"
                    f"{ui.kv('Time', f'{elapsed}s', '⏱️')}"
                )
            else:
                title = "📢 Broadcast Report"
                body = (
                    f"{ui.kv('Groups reached', str(total), '👥')}\n"
                    f"{ui.kv('Sent', str(sent), '✅')}\n"
                    f"{ui.kv('Failed', str(failed), '❌')}\n"
                    f"{ui.kv('Skipped', str(skipped), '⏳️')}\n"
                    f"{ui.kv('Time taken', f'{elapsed}s', '⏱️')}"
                )
                if flood:
                    body += f"\n{ui.kv('Flood waits', f'{flood}', '⏸️')}"
                if remaining:
                    body += f"\n{ui.kv('Not reached', str(remaining), '❓')}"

            account_name = sess.get("name") or phone
            text = (
                f"{ui.card(title, f'Account : {account_name}', 'Here is how it went', '📢')}\n\n"
                f"{body}\n\n"
                f"{ui.help_hint('Open the userbot dashboard to broadcast again or change the message.')}"
                f"\n{ui.footer()}</blockquote>"
            )
            await client.send_message(owner_id, text, parse_mode="html")
        except Exception as e:
            logger.debug(f"Could not send the broadcast report: {e}")

    @client.on(events.CallbackQuery(pattern=r"^broadcast_test_(.+)$"))
    async def broadcast_test_callback(event):
        """
        Sends the configured message to ONE group so the user can check the
        formatting BEFORE the message goes out to every single group.
        """
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id

        bot_obj = userbot_manager.find_running_bot(phone)
        if not bot_obj or not bot_obj.is_running:
            await event.answer("⚠️ The userbot must be running to test.", alert=True)
            return

        sess = database.get_session(phone)
        settings = (sess or {}).get("settings", {}) or {}
        messages = utils.get_broadcast_messages(settings)
        if not messages:
            await event.answer("⚠️ No broadcast message is set for this userbot.", alert=True)
            return

        status = await event.respond(
            "🧪 <b>Preparing the test send...</b>\n\n<i>🔍 Collecting your groups…</i>",
            parse_mode="html"
        )
        try:
            groups = await bot_obj.get_groups(force_refresh=True)
        except Exception:
            groups = []
        if not groups:
            await status.edit(
                "<blockquote><b>❌ No groups found</b>\n\n"
                "⚠️ <i>Join some groups first, then try the test send again.</i></blockquote>",
                parse_mode="html"
            )
            return

        target = groups[0]
        title = getattr(target, "title", None) or str(getattr(target, "id", "?"))
        message = random.choice(messages)
        processed = utils.parse_spintax(message)
        processed = utils.normalize_text(processed)
        processed = utils.make_message_unique(processed)

        sent = False
        error = None
        try:
            await bot_obj.client.send_message(target.input_entity, processed, parse_mode="html")
            sent = True
        except Exception as e:
            error = str(e)
            logger.warning(f"Test send failed for {phone}: {e}")

        await status.edit(
            f"<blockquote><b>🧪 Test Send {'✅' if sent else '❌'}</b>\n\n"
            f"🗢️ <b>Test group :</b> <code>{title}</code>\n"
            f"📤 <b>Delivered :</b> <b>{'YES' if sent else 'NO'}</b>\n\n"
            f"{ui.help_hint('Check the message in that group. If it looks right, broadcast it to everyone.')}"
            f"\n{ui.footer()}</blockquote>",
            parse_mode="html"
        )

        await _send_broadcast_report(
            phone,
            {"total": 1, "sent": 1 if sent else 0, "failed": 0 if sent else 1,
             "skipped": 0, "elapsed": 0, "flood_waits": 0, "test_group": title},
            trigger="test",
        )

        if sent:
            buttons = [[utils.styled_button("📢 Broadcast To All Groups", f"broadcast_now_{phone}", style="success")]]
            try:
                await event.respond(
                    "<blockquote><b>✅ Looks good?</b>\n\n"
                    f"{ui.help_hint('Tap below to send it to every group.')}</blockquote>",
                    buttons=buttons, parse_mode="html"
                )
            except Exception:
                pass

    @client.on(events.CallbackQuery(pattern=r"^spam_status_(.+)$"))
    async def spam_status_callback(event):
        """
        Shows the @SpamBot restriction of the account and the anti spam
        budget (daily / hourly messages that are still allowed).
        """
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id

        sess = database.get_session(phone)
        if not sess or not is_session_owner_or_admin(sess, event.sender_id):
            await event.answer("\u274c Session not found.", alert=True)
            return

        budget = antispam.budget_state(sess)
        status_msg = await event.respond(
            "\U0001f9ea <b>Checking @SpamBot...</b>\n\n<i>This can take a moment.</i>",
            parse_mode="html"
        )

        bot_obj = userbot_manager.find_running_bot(phone)
        if not bot_obj or not bot_obj.client:
            await status_msg.edit(
                "<blockquote><b>\u274c The account is not running</b>\n\n"
                "<i>Start it first so the status can be checked.</i></blockquote>",
                parse_mode="html"
            )
            return

        result = await antispam.check_spam_status(bot_obj.client, force=True)

        if result.get("error"):
            body = f"\u274c <b>Could not reach @SpamBot</b>\n\n<code>{str(result.get('error'))[:200]}</code>"
        elif result.get("limited"):
            severity = "PERMANENT" if result.get("severity") == "permanent" else "TEMPORARY"
            body = (
                f"\U0001f6a8 <b>THIS ACCOUNT IS LIMITED BY @SpamBot</b>\n\n"
                f"\u26a0\ufe0f <b>Severity :</b> <code>{severity}</code>\n"
                + (f"\U0001f550 <b>Until :</b> <code>{result.get('until') or 'unknown'}</code>\n" if result.get("until") else "")
            )
        else:
            body = "\u2705 <b>No @SpamBot restriction</b>\n\n<i>The account can send messages normally.</i>"

        budget_line = (
            f"\U0001f4ca <b>Account Health</b>\n"
            f"\U0001f4c8 <b>Account age :</b> <code>{budget.get('age_days', 0)} day(s)</code>\n"
            f"\u2705 <b>Messages sent today :</b> <code>{budget.get('daily_used', 0)}</code>\n"
            f"\U0001f4a1 <i>{antispam_guard.health_text(sess)}</i>\n"
        )

        try:
            await status_msg.edit(
                f"{ui.card('Spam Status', sess.get('name') or phone, 'Your @SpamBot restriction and budget', chr(128225))}\n\n"
                f"{body}\n\n{budget_line}"
                f"\n{ui.footer()}</blockquote>",
                parse_mode="html"
            )
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^broadcast_now_(.+)$"))
    async def broadcast_now_callback(event):
        """Sends one broadcast round right now with a live progress message."""
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id

        bot_obj = userbot_manager.find_running_bot(phone)
        if not bot_obj or not bot_obj.is_running:
            await event.answer("⚠️ The userbot must be running to broadcast.", alert=True)
            return

        sess = database.get_session(phone)
        settings = (sess or {}).get("settings", {}) or {}
        if not (settings.get("broadcast_msg") or settings.get("broadcast_messages")):
            await event.answer("⚠️ No broadcast message is set for this userbot.", alert=True)
            return

        progress = await event.respond(
            "📢 <b>Broadcast starting...</b>\n\n⏳ <i>Preparing the group list…</i>",
            parse_mode="html"
        )
        try:
            stats = await bot_obj.broadcast_round(progress_msg=progress, force=True)
        finally:
            pass

        total = stats.get("total", 0)
        sent = stats.get("sent", 0)
        failed = stats.get("failed", 0)
        remaining = max(0, total - sent - failed)

        summary = (
            f"📢 <b>Broadcast Complete</b>\n\n"
            f"✅ <b>Sent :</b> <code>{sent}</code> / <code>{total}</code>\n"
            f"❌ <b>Failed :</b> <code>{failed}</code>\n"
            f"⏳ <b>Remaining :</b> <code>{remaining}</code>\n"
            f"⏱️ <b>Took :</b> <code>{stats.get('elapsed', 0)}s</code>"
        )
        # The owner also gets the report in his private chat.
        await _send_broadcast_report(phone, stats, trigger="auto")
        if stats.get("flood_waits"):
            summary += (
                f"\n⏸️ <b>Flood waits :</b> <code>{stats['flood_waits']}</code> "
                f"(<code>{stats.get('wait_total', 0)}s</code> waited)"
            )
        if remaining:
            summary += (
                f"\n\n💡 <i>{remaining} group(s) were not reached. Run it again to continue.</i>"
            )

        try:
            await progress.edit(summary, parse_mode="html")
        except Exception:
            await event.respond(summary, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^all_slots_broadcast_now$"))
    async def all_slots_broadcast_now_callback(event):
        """Sends one broadcast round on every running userbot at once."""
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No userbot slots found.", alert=True)
            return

        running = [s for s in sessions if userbot_manager.is_bot_running(s.get("phone"))]
        if not running:
            await event.answer("⚠️ None of your userbots are running.", alert=True)
            return

        progress = await event.respond(
            f"📢 <b>Broadcasting on {len(running)} userbot(s)...</b>\n\n⏳ <i>Starting…</i>",
            parse_mode="html"
        )

        total_sent = 0
        total_failed = 0
        total_groups = 0
        total_flood = 0

        async def _one(s):
            nonlocal total_sent, total_failed, total_groups, total_flood
            bot_obj = userbot_manager.find_running_bot(s.get("phone"))
            if not bot_obj:
                return
            try:
                stats = await bot_obj.broadcast_round(force=True)
            except Exception as e:
                logger.warning(f"Broadcast failed for {s.get('phone')}: {e}")
                return
            total_sent += stats.get("sent", 0)
            total_failed += stats.get("failed", 0)
            total_groups += stats.get("total", 0)
            total_flood += stats.get("flood_waits", 0)

        for s in running:
            await _one(s)
            try:
                await progress.edit(
                    f"📢 <b>Broadcasting on {len(running)} userbot(s)...</b>\n\n"
                    f"✅ <b>Sent :</b> <code>{total_sent}</code> / <code>{total_groups}</code>\n"
                    f"❌ <b>Failed :</b> <code>{total_failed}</code>\n"
                    f"⏸️ <b>Flood waits :</b> <code>{total_flood}</code>",
                    parse_mode="html"
                )
            except Exception:
                pass

        summary = (
            f"📢 <b>Broadcast Complete (All Slots)</b>\n\n"
            f"🤖 <b>Userbots :</b> <code>{len(running)}</code>\n"
            f"✅ <b>Sent :</b> <code>{total_sent}</code> / <code>{total_groups}</code>\n"
            f"❌ <b>Failed :</b> <code>{total_failed}</code>\n"
            f"⏸️ <b>Flood waits :</b> <code>{total_flood}</code>"
        )
        try:
            await progress.edit(summary, parse_mode="html")
        except Exception:
            await event.respond(summary, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=r"^vc_join_all_(.+)$"))
    async def vc_join_all_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await userbot_manager.start_userbot(phone, user_id)
            bot_obj = userbot_manager._running_bots.get(phone)
            
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ <b>Scanning And Joining All Active Voice Chats...</b>", parse_mode="html")
        joined, total = await bot_obj.join_all_active_group_vcs()
        try:
            await progress_msg.delete()
        except Exception:
            pass
            
        flash = (
            f"<blockquote><b>» 🎙️ Auto-Join Vc Results</b>\n\n"
            f"• <b>Successfully Joined :</b> <b>{joined} / {total}</b> Active Voice Chats!</blockquote>"
        )
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_join_all$"))
    async def all_slots_vc_join_all_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ <b>Scanning And Joining All Active Vcs Across Userbots...</b>", parse_mode="html")
        total_joined = 0
        total_found = 0
        
        for s in sessions:
            p = s["phone"]
            if not userbot_manager.is_bot_running(p):
                await userbot_manager.start_userbot(p, user_id)
            bot_obj = userbot_manager._running_bots.get(p)
            if bot_obj:
                j, f = await bot_obj.join_all_active_group_vcs()
                total_joined += j
                total_found += f
                
        try:
            await progress_msg.delete()
        except Exception:
            pass
            
        flash = (
            f"<blockquote><b>» 🎙️ All Slots : Auto-Join Vc Results</b>\n\n"
            f"• <b>Total Active Vcs Joined :</b> <b>{total_joined} / {total_found}</b></blockquote>"
        )
        await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_leave_all$"))
    async def all_slots_vc_leave_all_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return

        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return

        progress_msg = await event.reply("⏳ <b>Leaving All Active Vcs Across All Userbots...</b>", parse_mode="html")
        total_left = 0
        total_processed = 0

        async def _leave_all_one(p):
            bot = userbot_manager.find_running_bot(p)
            if bot:
                try:
                    c, _ = await bot.leave_all_voice_chats()
                    return c
                except Exception as e:
                    logger.warning(f"Error leaving all VCs for {p}: {e}")
                    return 0
            else:
                return 0

        results = await asyncio.gather(*[_leave_all_one(p) for p in running_phones], return_exceptions=True)
        for r in results:
            if not isinstance(r, Exception) and r:
                total_left += r
            total_processed += 1

        try:
            await progress_msg.delete()
        except Exception:
            pass

        flash = (
            f"<blockquote><b>» 🔴 All Slots : Leave All Active Vcs</b>\n\n"
            f"• <b>Total Vcs Left :</b> <b>{total_left}</b>\n"
            f"• <b>Userbots Processed :</b> <b>{total_processed} / {len(running_phones)}</b></blockquote>"
        )
        await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern=r"^vc_leave_all_(.+)$"))
    async def vc_leave_all_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager.find_running_bot(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return

        progress_msg = await event.reply("⏳ <b>Leaving All Active Voice Chats...</b>", parse_mode="html")
        try:
            left_count, msg = await bot_obj.leave_all_voice_chats()
        except Exception as e:
            logger.error(f"Error during leave_all_voice_chats for {phone}: {e}")
            utils.report_error(f"my_bots.leave_all_vc:{phone}", e)
            left_count, msg = 0, "Could not leave the voice chats. The error was reported to the admin team."

        try:
            await progress_msg.delete()
        except Exception:
            pass

        flash_text = (
            f"<blockquote><b>» 🔴 Leave All Vcs Results</b>\n\n"
            f"• <b>Status :</b> {msg}\n"
            f"• <b>Vcs Left :</b> <b>{left_count}</b></blockquote>"
        )
        await show_bot_dashboard(event, phone, user_id, flash_message=flash_text)

    @client.on(events.CallbackQuery(pattern=r"^vc_leave_(?!all_|grp_)(.+)$"))
    async def vc_leave_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager.find_running_bot(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
            
        progress_msg = await event.reply("<blockquote><b>» ⏳ Leaving Voice Chat...</b></blockquote>", parse_mode="html")
        try:
            success, msg = await bot_obj.leave_voice_chat()
        except Exception as e:
            logger.error(f"Error during leave_voice_chat for {phone}: {e}")
            utils.report_error(f"my_bots.leave_vc:{phone}", e)
            success, msg = False, "Could not leave the voice chat. The error was reported to the admin team."

        try:
            await progress_msg.delete()
        except Exception:
            pass
        
        flash_text = f"<blockquote><b>» 🎙️ Voice Chat Status</b>\n\n• {msg}</blockquote>"
        await show_bot_dashboard(event, phone, user_id, flash_message=flash_text)

    @client.on(events.CallbackQuery(pattern=r"^vc_leave_grp_(.+)$"))
    async def vc_leave_grp_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to leave a group.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_LEAVE_GRP"
        }
        
        prompt_text = (
            "❌ <b>Leave Group / Channel</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "> Send the <b>Group invite link</b>, <b>Username</b>, or <b>Chat ID</b> of the group you want the userbot to leave.\n\n"
            "✍️ <b>Send the link or ID below:</b>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^vc_mute_(.+)$"))
    async def vc_mute_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
        success, msg = await bot_obj.mute_mic()
        await event.answer(msg, alert=True)
        await vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern=r"^vc_unmute_(.+)$"))
    async def vc_unmute_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
        success, msg = await bot_obj.unmute_mic()
        await event.answer(msg, alert=True)
        await vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern=r"^stop_song_(.+)$"))
    async def stop_song_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await event.answer("⚠️ Userbot is not running.", alert=True)
            return
        success, msg = await bot_obj.stop_song()
        await event.answer(msg, alert=True)
        await vc_menu_callback(event)

    # ---------- Now Playing Inline Buttons (skip/end/autoplay) ----------
    @client.on(events.CallbackQuery(pattern=r"^np_skip_all_(\d+)$"))
    async def np_skip_all_callback(event):
        """Skip current song on all active VC userbots (triggered from Now Playing message)."""
        user_id = int(event.pattern_match.group(1))
        if event.sender_id != user_id:
            await event.answer("⛔ This button is not for you.", alert=True)
            return
        sessions = get_effective_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        vc_bots = [(p, userbot_manager.find_running_bot(p)) for p in running_phones
                   if getattr(userbot_manager.find_running_bot(p), "current_vc_chat_id", None)]
        if not vc_bots:
            await event.answer("⚠️ No active VC bots found.", alert=True)
            return
        await asyncio.gather(*[bot.stop_song() for _, bot in vc_bots], return_exceptions=True)
        await event.answer("⏭️ Skipped on all active VCs!", alert=True)
        try:
            await event.edit(buttons=None)
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^np_end_all_(\d+)$"))
    async def np_end_all_callback(event):
        """End song and mute all active VC userbots (triggered from Now Playing message)."""
        user_id = int(event.pattern_match.group(1))
        if event.sender_id != user_id:
            await event.answer("⛔ This button is not for you.", alert=True)
            return
        sessions = get_effective_sessions(user_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        vc_bots = [(p, userbot_manager.find_running_bot(p)) for p in running_phones
                   if getattr(userbot_manager.find_running_bot(p), "current_vc_chat_id", None)]
        if not vc_bots:
            await event.answer("⚠️ No active VC bots found.", alert=True)
            return
        await asyncio.gather(*[bot.stop_song() for _, bot in vc_bots], return_exceptions=True)
        await event.answer("🛑 Stopped playback on all active VCs!", alert=True)
        try:
            await event.edit(buttons=None)
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^np_autoplay_(\d+)$"))
    async def np_autoplay_callback(event):
        """Toggle autoplay loop for all active VC userbots."""
        user_id = int(event.pattern_match.group(1))
        if event.sender_id != user_id:
            await event.answer("⛔ This button is not for you.", alert=True)
            return
        current = _np_autoplay_state.get(user_id, False)
        _np_autoplay_state[user_id] = not current
        state_text = "🔁 ON" if _np_autoplay_state[user_id] else "➡️ OFF"
        await event.answer(f"Autoplay set to {state_text}", alert=True)


    @client.on(events.CallbackQuery(pattern="^all_slots_vc_mute$"))
    async def all_slots_vc_mute_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        async def _mute_one(p):
            bot_obj = userbot_manager.find_running_bot(p)
            return await bot_obj.mute_mic()
            
        await asyncio.gather(*[_mute_one(p) for p in running_phones], return_exceptions=True)
        await event.answer("🔇 Muted mic on all running userbots!", alert=True)
        await all_slots_vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_unmute$"))
    async def all_slots_vc_unmute_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        async def _unmute_one(p):
            bot_obj = userbot_manager.find_running_bot(p)
            return await bot_obj.unmute_mic()
            
        await asyncio.gather(*[_unmute_one(p) for p in running_phones], return_exceptions=True)
        await event.answer("🔊 Unmuted mic on all running userbots!", alert=True)
        await all_slots_vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern="^all_slots_stop_song$"))
    async def all_slots_stop_song_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        async def _stop_one(p):
            bot_obj = userbot_manager.find_running_bot(p)
            return await bot_obj.stop_song()
            
        await asyncio.gather(*[_stop_one(p) for p in running_phones], return_exceptions=True)
        await event.answer("🛑 Stopped playback on all running userbots!", alert=True)
        await all_slots_vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_leave_grp$"))
    async def all_slots_vc_leave_grp_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_LEAVE_GRP"
        }
        
        prompt_text = (
            "❌ <b>Leave Group / Channel (All Slots)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "> Send the <b>Group invite link</b>, <b>Username</b>, or <b>Chat ID</b> of the group you want ALL running userbots to leave.\n\n"
            "✍️ <b>Send the link or ID below:</b>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_vc_menu", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_leave$"))
    async def all_slots_vc_leave_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        progress_msg = await event.reply("<blockquote><b>» ⏳ Leaving Voice Chats On All Running Userbots...</b></blockquote>", parse_mode="html")
        
        async def _leave_one(p):
            bot_obj = userbot_manager.find_running_bot(p)
            success, msg = await bot_obj.leave_voice_chat()
            return success
            
        results = await asyncio.gather(*[_leave_one(p) for p in running_phones], return_exceptions=True)
        success_count = sum(1 for r in results if not isinstance(r, Exception) and r)
        
        try:
            await progress_msg.delete()
        except Exception:
            pass
        flash = f"<blockquote><b>» 🎙️ All Slots : Leave Vc Results</b>\n\n• <b>Successfully Left :</b> <b>{success_count} / {len(running_phones)}</b> Userbots</blockquote>"
        await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern="^all_slots_restart$"))
    async def all_slots_restart_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ <b>Restarting userbots sequentially to optimize memory...</b>")
        
        restarted = 0
        limit_reached = False
        from villain_ads_bot import config
        max_running = getattr(config, "MAX_RUNNING_USERBOTS", 99999)

        for s in sessions:
            phone = s["phone"]
            await userbot_manager.stop_userbot(phone)
            await asyncio.sleep(0.5) # Let the slot free up
            
            if not userbot_manager.can_start_more_bots():
                limit_reached = True
                continue # We continue stopping the rest, but won't restart them

            success = await userbot_manager.start_userbot(phone)
            if success:
                restarted += 1
            await asyncio.sleep(0.5)
            
        await progress_msg.delete()
        
        if limit_reached:
            flash = f"🔄 <b>Restarted {restarted} userbots!</b>\n⚠️ *Some bots stopped but couldn't restart as the server limit of {max_running} active bots was reached.*"
        else:
            flash = f"🔄 <b>Restarted {restarted} userbots!</b>"
            
        await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern="^all_slots_clone_profile$"))
    async def all_slots_clone_profile_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        text = utils.format_html_message(
            "<blockquote><b>» 👥 Bulk Profile Cloning Options</b>\n\n"
            "Choose Which Aspect Of The Target Profile You Would Like to Clone To All Running Userbots:</blockquote>"
        )
        
        buttons = [
            [
                utils.styled_button("👥 Complete Profile Clone", "all_slots_clone_opt_complete", style="success")
            ],
            [
                utils.styled_button("✏️ Clone Name Only", "all_slots_clone_opt_name", style="primary"),
                utils.styled_button("📝 Clone Bio Only", "all_slots_clone_opt_bio", style="primary")
            ],
            [
                utils.styled_button("🖼️ Clone Photo Only", "all_slots_clone_opt_photo", style="primary")
            ],
            [utils.styled_button("🔙 Cancel", "menu_all_slots", style="danger")]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^all_slots_clone_opt_(complete|name|bio|photo)$"))
    async def all_slots_clone_opt_callback(event):
        clone_type = event.pattern_match.group(1)
        user_id = event.sender_id
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_CLONE_TARGET",
            "clone_type": clone_type
        }
        
        type_display = {
            "complete": "Complete Profile",
            "name": "Name Only",
            "bio": "Bio Only",
            "photo": "Photo Only"
        }.get(clone_type, "Complete Profile")
        
        prompt_text = utils.format_html_message(
            f"<blockquote><b>» 👥 Bulk Clone Profile ({type_display})</b>\n\n"
            f"• Enter The Username (e.g. <code>@username</code>), User ID (e.g. <code>8668706422</code> or <code>tg://openmessage?user_id=8668706422</code>), or <b>forward any message from the target user</b>:</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_clone_profile", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_help$"))
    async def all_slots_help_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("help_dashboard_text", lang)
        buttons = [[utils.styled_button("🔙 Back", "menu_all_slots", style="primary")]]
        global_settings = database.get_global_settings()
        help_image = global_settings.get("help_image")
        try:
            if help_image and os.path.exists(help_image):
                await event.respond(text, file=help_image, buttons=buttons)
            else:
                await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_how_to_use$"))
    async def all_slots_how_to_use_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("how_to_use_text", lang)
        buttons = [[utils.styled_button("🔙 Back", "menu_all_slots", style="primary")]]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_change_name$"))
    async def all_slots_change_name_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_NAME"
        }
        prompt_text = (
            "<blockquote><b>» ✏️ Change Name (All Userbots)</b>\n\n"
            "Send The New Name You Want To Set For All Your Userbots :</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_refresh_stats$"))
    async def all_slots_refresh_stats_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        running_bots = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_bots:
            await event.answer("⚠️ Start at least one userbot first!", alert=True)
            return
            
        progress_msg = await event.reply("⏳ <b>Refreshing statistics for all running userbots concurrently...</b>")
        
        async def _refresh_one(phone):
            bot_obj = userbot_manager.find_running_bot(phone)
            try:
                await bot_obj.get_groups(force_refresh=True)
                return True
            except Exception:
                return False
                
        results = await asyncio.gather(*[_refresh_one(p) for p in running_bots], return_exceptions=True)
        refreshed = sum(1 for r in results if not isinstance(r, Exception) and r)
        
        await progress_msg.delete()
        await show_all_slots_dashboard(event, user_id, flash_message=f"🔄 <b>Refreshed stats for {refreshed} userbots!</b>", fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern="^all_slots_delete$"))
    async def all_slots_delete_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        text = (
            "⚠️ <b>Delete All UserBots</b>\n\n"
            "Are you absolutely sure you want to delete <b>ALL</b> connected userbots? "
            "This will delete all Telegram sessions from disk and database. This action cannot be undone!"
        )
        buttons = [
            [utils.styled_button("🗑️ Yes, Delete All", "all_slots_delete_confirm", style="danger")],
            [utils.styled_button("❌ Cancel", "menu_all_slots", style="primary")]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_delete_confirm$"))
    async def all_slots_delete_confirm_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        
        async def _delete_one(s):
            await userbot_manager.remove_userbot(s["phone"])
            return True
            
        results = await asyncio.gather(*[_delete_one(s) for s in sessions], return_exceptions=True)
        deleted = sum(1 for r in results if not isinstance(r, Exception) and r)
        
        from .my_bots import show_bots_list
        await show_bots_list(event, user_id, flash_message=f"🗑️ <b>Deleted {deleted} userbot sessions successfully.</b>")

    @client.on(events.CallbackQuery(pattern="^all_slots_start$"))
    async def all_slots_start_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ <b>Starting userbots sequentially to optimize memory...</b>")
        
        started = 0
        limit_reached = False
        from villain_ads_bot import config
        max_running = getattr(config, "MAX_RUNNING_USERBOTS", 99999)

        for s in sessions:
            phone = s["phone"]
            if not userbot_manager.is_bot_running(phone):
                if not userbot_manager.can_start_more_bots():
                    limit_reached = True
                    break
                success = await userbot_manager.start_userbot(phone)
                if success:
                    started += 1
                await asyncio.sleep(0.5)
                
        try:
            await progress_msg.delete()
        except Exception:
            pass
        
        if limit_reached:
            flash = f"🟢 <b>Started {started} userbots!</b>\n⚠️ *Some userbots could not start because the server limit of {max_running} active bots was reached.*"
        else:
            flash = f"🟢 <b>Started {started} userbots!</b>"
            
        await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern="^all_slots_stop$"))
    async def all_slots_stop_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ <b>Stopping all userbots concurrently...</b>")
        
        async def _stop_one(s):
            phone = s["phone"]
            if userbot_manager.is_bot_running(phone):
                await userbot_manager.stop_userbot(phone)
                return True
            return False
            
        results = await asyncio.gather(*[_stop_one(s) for s in sessions], return_exceptions=True)
        stopped = sum(1 for r in results if not isinstance(r, Exception) and r)
        
        try:
            await progress_msg.delete()
        except Exception:
            pass
        await show_all_slots_dashboard(event, user_id, flash_message=f"🔴 <b>Stopped {stopped} userbots!</b>", fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_join$"))
    async def all_slots_vc_join_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sessions = get_effective_sessions(event.sender_id)
        running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
        if not running_phones:
            await event.answer("⚠️ Please start at least one userbot first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_VC_LINK"
        }
        
        prompt_text = utils.get_text("prompt_all_vc_link", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    # ------------------ Bulk Broadcast Sub-Menu (All) ------------------
    @client.on(events.CallbackQuery(pattern="^all_slots_set_broadcast$"))
    async def all_slots_set_broadcast_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id) if user_id != "__ALL__" else None
        lang = user.get("language", "en") if user else "en"
        
        sessions = get_effective_sessions(event.sender_id)
        total_bots = len(sessions)
        modes = [s.get("settings", {}).get("broadcast_mode", "single") for s in sessions]
        mode = "multiple" if (modes and all(m == "multiple" for m in modes)) else "single"
        mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "✉️ Single (Normal)"
        
        text = (
            f"<blockquote><b>» ✉️ Bulk Broadcast Settings (All Slots)</b>\n\n"
            f"Configure Broadcasting Settings For All Userbots Simultaneously:\n\n"
            f"• <b>Current Mode :</b> <b>{mode_display}</b>\n"
            f"• <b>Configured Userbots :</b> <b>{total_bots}</b>\n\n"
            f"💡 <i>How To Set Multiple Messages : Click 'Set Multiple Messages (All)' And Send Messages Separated By Commas (<code>,</code>).</i></blockquote>"
        )
        buttons = [
            [
                utils.styled_button("✉️ Set Single Message (All)", "all_slots_set_single_msg", style="primary"),
                utils.styled_button("📚 Set Multiple Messages (All)", "all_slots_set_multi_msg", style="primary")
            ],
            [
                utils.styled_button(f"🔄 Mode : {mode.upper()} (All)", "all_slots_toggle_broadcast_mode", style="primary")
            ],
            [
                utils.styled_button("🔙 Back To Dashboard", "menu_all_slots", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_single_msg$"))
    async def all_slots_set_single_msg_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id) if user_id != "__ALL__" else None
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_BROADCAST"
        }
        prompt_text = utils.get_text("prompt_broadcast", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_broadcast", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_multi_msg$"))
    async def all_slots_set_multi_msg_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_MULTI_MSG"
        }
        prompt_text = (
            "<blockquote><b>» 📚 Set Multiple Messages (All Slots)</b>\n\n"
            "Send Your Multiple Broadcast Messages Separated By Commas (<code>,</code>). The Bot Will Rotate/Pick One Message For Each Group.\n\n"
            "💡 <b>Example Input :</b>\n"
            "<code>Hey Check This Out!, Join Our Channel Now!, Best Deals Today!</code>\n\n"
            "✍️ <b>Send Your Comma-Separated Message List Below :</b></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_broadcast", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_toggle_broadcast_mode$"))
    async def all_slots_toggle_broadcast_mode_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
        modes = [s.get("settings", {}).get("broadcast_mode", "single") for s in sessions]
        current_mode = "multiple" if (modes and all(m == "multiple" for m in modes)) else "single"
        new_mode = "single" if current_mode == "multiple" else "multiple"
        for s in sessions:
            s.setdefault("settings", {})["broadcast_mode"] = new_mode
            database.save_session(s)
            if userbot_manager.is_bot_running(s["phone"]):
                userbot_manager.reload_bot_settings(s["phone"])
        await all_slots_set_broadcast_callback(event)

    # ------------------ Bulk DM Welcome Sub-Menu (All) ------------------
    @client.on(events.CallbackQuery(pattern="^all_slots_set_welcome$"))
    async def all_slots_set_welcome_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id) if user_id != "__ALL__" else None
        lang = user.get("language", "en") if user else "en"
        
        sessions = get_effective_sessions(event.sender_id)
        total_bots = len(sessions)
        modes = [s.get("settings", {}).get("welcome_mode", "single") for s in sessions]
        mode = "multiple" if (modes and all(m == "multiple" for m in modes)) else "single"
        mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "👋 Single (Normal)"
        
        text = (
            f"<blockquote><b>» 👋 Dm Welcome Message Settings (All Userbots)</b>\n\n"
            f"Configure Dm Welcome Responses For All Userbots Simultaneously:\n\n"
            f"• <b>Current Mode :</b> <b>{mode_display}</b>\n"
            f"• <b>Configured Userbots :</b> <b>{total_bots}</b>\n\n"
            f"💡 <i>How To Set Multiple Welcomes : Click 'Set Multiple Welcomes' And Send Messages Separated By Commas (<code>,</code>).</i></blockquote>"
        )
        buttons = [
            [
                utils.styled_button("👋 Set Single Welcome (All)", "all_slots_set_single_welcome", style="primary"),
                utils.styled_button("📚 Set Multiple Welcomes (All)", "all_slots_set_multi_welcome", style="primary")
            ],
            [
                utils.styled_button(f"🔄 Mode : {mode.upper()} (All)", "all_slots_toggle_welcome_mode", style="primary")
            ],
            [
                utils.styled_button("🔙 Back To Dashboard", "menu_all_slots", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_single_welcome$"))
    async def all_slots_set_single_welcome_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id) if user_id != "__ALL__" else None
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_WELCOME"
        }
        
        prompt_text = utils.get_text("prompt_all_welcome", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_welcome", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_multi_welcome$"))
    async def all_slots_set_multi_welcome_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id) if user_id != "__ALL__" else None
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_MULTI_WELCOME"
        }
        
        prompt_text = utils.get_text("prompt_multi_welcome", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_welcome", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_toggle_welcome_mode$"))
    async def all_slots_toggle_welcome_mode_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
        modes = [s.get("settings", {}).get("welcome_mode", "single") for s in sessions]
        current_mode = "multiple" if (modes and all(m == "multiple" for m in modes)) else "single"
        new_mode = "single" if current_mode == "multiple" else "multiple"
        for s in sessions:
            s.setdefault("settings", {})["welcome_mode"] = new_mode
            database.save_session(s)
            if userbot_manager.is_bot_running(s["phone"]):
                userbot_manager.reload_bot_settings(s["phone"])
        await all_slots_set_welcome_callback(event)

    # ------------------ Bulk Tag Auto-Reply Sub-Menu (All) ------------------
    @client.on(events.CallbackQuery(pattern="^all_slots_set_auto_reply$"))
    async def all_slots_set_auto_reply_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id) if user_id != "__ALL__" else None
        lang = user.get("language", "en") if user else "en"
        
        sessions = get_effective_sessions(event.sender_id)
        total_bots = len(sessions)
        modes = [s.get("settings", {}).get("auto_reply_mode", "single") for s in sessions]
        mode = "multiple" if (modes and all(m == "multiple" for m in modes)) else "single"
        mode_display = "📚 Multiple (Rotational)" if mode == "multiple" else "💬 Single (Normal)"
        
        text = (
            f"<blockquote><b>» 💬 Group Tag Auto-Reply Settings (All Userbots)</b>\n\n"
            f"Configure Group Tag/Mention Responses For All Userbots Simultaneously:\n\n"
            f"• <b>Current Mode :</b> <b>{mode_display}</b>\n"
            f"• <b>Configured Userbots :</b> <b>{total_bots}</b>\n\n"
            f"💡 <i>How To Set Multiple Replies : Click 'Set Multiple Replies (All)' And Send Messages Separated By Commas (<code>,</code>).</i></blockquote>"
        )
        buttons = [
            [
                utils.styled_button("💬 Set Single Reply (All)", "all_slots_set_single_reply", style="primary"),
                utils.styled_button("📚 Set Multiple Replies (All)", "all_slots_set_multi_reply", style="primary")
            ],
            [
                utils.styled_button(f"🔄 Mode : {mode.upper()} (All)", "all_slots_toggle_reply_mode", style="primary")
            ],
            [
                utils.styled_button("🔙 Back To Dashboard", "menu_all_slots", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_single_reply$"))
    async def all_slots_set_single_reply_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_AUTO_REPLY_SINGLE"
        }
        prompt_text = "<blockquote><b>» 💬 Set Single Group Tag Reply (All Slots)</b>\n\nSend Your Single Group Tag Auto-Reply Message For All Bots Below :</blockquote>"
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_auto_reply", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_multi_reply$"))
    async def all_slots_set_multi_reply_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id) if user_id != "__ALL__" else None
        lang = user.get("language", "en") if user else "en"
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_AUTO_REPLY_MSGS"
        }
        prompt_text = utils.get_text("prompt_set_auto_reply", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_auto_reply", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_toggle_reply_mode$"))
    async def all_slots_toggle_reply_mode_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
        modes = [s.get("settings", {}).get("auto_reply_mode", "single") for s in sessions]
        current_mode = "multiple" if (modes and all(m == "multiple" for m in modes)) else "single"
        new_mode = "single" if current_mode == "multiple" else "multiple"
        for s in sessions:
            s.setdefault("settings", {})["auto_reply_mode"] = new_mode
            database.save_session(s)
            if userbot_manager.is_bot_running(s["phone"]):
                userbot_manager.reload_bot_settings(s["phone"])
        await all_slots_set_auto_reply_callback(event)

    @client.on(events.CallbackQuery(pattern="^all_slots_toggle_(spam|welcome|add_contact)$"))
    async def all_slots_toggles_callback(event):
        feature = event.pattern_match.group(1)
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        key_map = {
            "spam": "auto_spam",
            "welcome": "auto_welcome",
            "add_contact": "auto_add_contact"
        }
        db_key = key_map[feature]
        
        any_on = any(s.get("settings", {}).get(db_key, False) for s in sessions)
        all_on = all(s.get("settings", {}).get(db_key, False) for s in sessions)
        # If every userbot is already on -> switch everything off.
        # If only some are on -> switch everything on (so a single tap never
        # silently disables a feature that is active on other userbots).
        new_state = False if all_on else True
        
        for s in sessions:
            s.setdefault("settings", {})[db_key] = new_state
        for s in sessions:
            database.save_session(s)
            userbot_manager.reload_bot_settings(s.get("phone", ""))
            userbot_manager.reload_bot_settings(s.get("session_id", ""))
            
        state_word = "🟢 On" if new_state else "🔴 Off"
        feature_name = "Auto Broadcast" if feature == "spam" else ("Auto-Welcome" if feature == "welcome" else "Auto-Contact")
        await show_all_slots_dashboard(event, user_id, flash_message=f"<blockquote><b>» ⚙️ Settings Updated</b>\n\n{feature_name} Turned <b>{state_word}</b> For All Bots!</blockquote>", fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern=r"^set_run_timer_(.+)$"))
    async def set_run_timer_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_RUN_TIMER"
        }
        
        prompt_text = utils.format_html_message(
            "<blockquote><b>» ⏱️ Set Run Timer</b>\n\n"
            "Send The Number Of Hours (Or Minutes Using 'M') You Want The Userbot To Run Before Automatically Stopping.\n\n"
            "<b>Example :</b>\n"
            "• <code>2</code> (For 2 Hours)\n"
            "• <code>30m</code> (For 30 Minutes)\n"
            "• <code>0</code> (To Disable Timer)</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"select_bot_{phone}", style="primary")]]
        
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_run_timer$"))
    async def all_slots_set_run_timer_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_RUN_TIMER"
        }
        
        prompt_text = utils.format_html_message(
            "<blockquote><b>» ⏱️ Set Run Timer (All Bots)</b>\n\n"
            "Send The Number Of Hours (Or Minutes Using 'M') You Want All Your Userbots To Run Before Automatically Stopping.\n\n"
            "<b>Example :</b>\n"
            "• <code>2</code> (For 2 Hours)\n"
            "• <code>30m</code> (For 30 Minutes)\n"
            "• <code>0</code> (To Disable Timer)</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "menu_all_slots", style="primary")]]
        
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^vc_join_(\+.+)$"))
    async def vc_join_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to join a VC.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_VC_LINK"
        }
        
        prompt_text = utils.get_text("prompt_vc_link", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^vc_join_grp_(.+)$"))
    async def vc_join_grp_callback(event):
        """Join Group via invite link AND its VC in one step (single bot)."""
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to join a group/VC.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_VC_GRP_LINK"
        }
        
        prompt_text = (
            f"<blockquote><b>» 🔗 Join Group + Vc ({phone})</b>\n\n"
            f"• Send Your <b>Group Invite Link</b> (E.G. <code>https://t.me/+xxxx</code>) Or <b>Username</b>.\n"
            f"• <i>(You Can Also Send Multiple Links Separated By Commas)</i>\n\n"
            f"✅ <b>The Userbot Will :</b>\n"
            f"1. Auto-Join The Group/Channel.\n"
            f"2. Connect To The Active Voice Chat.</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^vc_join_multi_grp_(.+)$"))
    async def vc_join_multi_grp_callback(event):
        """Join Multiple Groups via comma-separated links/usernames (single bot)."""
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to join groups.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_VC_MULTI_GRP_LINK"
        }
        
        prompt_text = (
            f"<blockquote><b>» 📚 Join Multiple Groups ({phone})</b>\n\n"
            f"• <b>Description :</b> Send All Your Group / Channel Links Separated By Commas (<code>,</code>) Or Newlines.\n"
            f"• <b>Format Example :</b>\n"
            f"<code>https://t.me/groupone, https://t.me/+AbCdEfGh, @groupthree</code>\n\n"
            f"⚡ <i>The Userbot Will Automatically Join All Provided Groups/Channels Sequentially With Safe Delay!</i></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_join_grp$"))
    async def all_slots_vc_join_grp_callback(event):
        """Join Group via invite link for ALL userbots (running or stopped)."""
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id) if (user_id and user_id != "__ALL__") else None
        
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No userbot slots found. Please login a bot first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_VC_GRP_LINK"
        }
        
        header_grp = "» 🔗 Join Group (All System Bots)" if is_system_all_mode(event.sender_id) else "» 🔗 Join Group (All Slots)"
        prompt_text = (
            f"<blockquote><b>{header_grp}</b>\n\n"
            f"• <b>All {len(sessions)} Userbots</b> (Running Or Stopped) Will :\n"
            f"1. Auto-Start (If Currently Stopped).\n"
            f"2. Auto-Join The Group/Channel Via Your Link.\n\n"
            f"✍️ <b>Send The Group Invite Link Or Username Below:</b></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_vc_menu", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_vc_join_multi_grp$"))
    async def all_slots_vc_join_multi_grp_callback(event):
        """Join Multiple Groups via comma-separated links for ALL userbots."""
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No userbot slots found. Please login a bot first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_VC_MULTI_GRP_LINK"
        }
        
        header_grp = "» 📚 Join Multiple Groups (All System Bots)" if is_system_all_mode(event.sender_id) else f"» 📚 Join Multiple Groups (All {len(sessions)} Slots)"
        prompt_text = (
            f"<blockquote><b>{header_grp}</b>\n\n"
            f"• <b>Description :</b> Send All Group/Channel Links Separated By Commas (<code>,</code>) Or Newlines.\n"
            f"• <b>Format Example :</b>\n"
            f"<code>https://t.me/groupone, https://t.me/+AbCdEfGh, @groupthree</code>\n\n"
            f"⚡ <i>All Userbots Will Join Every Group Sequentially With Anti-Flood Delay!</i></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_vc_menu", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^no_login_"))
    async def no_login_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        alert_text = utils.get_text("account_login_first", lang)
        await event.answer(alert_text, alert=True)

    @client.on(events.CallbackQuery(pattern=r"^help_bot_(.+)$"))
    async def help_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("help_dashboard_text", lang)
        buttons = [[utils.styled_button("🔙 Back", f"select_bot_{phone}", style="primary")]]
        
        global_settings = database.get_global_settings()
        help_image = global_settings.get("help_image")
        try:
            if help_image:
                await event.respond(text, file=help_image, buttons=buttons)
            else:
                await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^how_to_use_(.+)$"))
    async def how_to_use_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("how_to_use_text", lang)
        buttons = [[utils.styled_button("🔙 Back", f"select_bot_{phone}", style="primary")]]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^help_bot_no_login$"))
    async def help_bot_no_login_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("help_dashboard_text", lang)
        buttons = [[utils.styled_button("🔙 Back", "menu_my_bots", style="primary")]]
        
        global_settings = database.get_global_settings()
        help_image = global_settings.get("help_image")
        try:
            if help_image:
                await event.respond(text, file=help_image, buttons=buttons)
            else:
                await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^how_to_use_no_login$"))
    async def how_to_use_no_login_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("how_to_use_text", lang)
        buttons = [[utils.styled_button("🔙 Back", "menu_my_bots", style="primary")]]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^clone_profile_(.+)$"))
    async def clone_profile_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to clone a profile.", alert=True)
            return
            
        text = utils.format_html_message(
            "<blockquote><b>» 👤 Profile Cloning Options</b>\n\n"
            "Choose Which Aspect Of The Target Profile You Would Like To Clone To Your Userbot:</blockquote>"
        )
        
        sess = database.get_session(phone)
        buttons = [
            [
                utils.styled_button("👤 Complete Profile Clone", f"clone_opt_complete_{phone}", style="success")
            ],
            [
                utils.styled_button("✏️ Clone Name Only", f"clone_opt_name_{phone}", style="primary"),
                utils.styled_button("📝 Clone Bio Only", f"clone_opt_bio_{phone}", style="primary")
            ],
            [
                utils.styled_button("🖼️ Clone Photo Only", f"clone_opt_photo_{phone}", style="primary")
            ]
        ]
        
        if sess and "original_first_name" in sess:
            buttons.append([utils.styled_button("🔄 Return To Original Profile", f"restore_profile_{phone}", style="success")])
            
        buttons.append([utils.styled_button("🔙 Back", f"select_bot_{phone}", style="danger")])
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^clone_opt_(complete|name|bio|photo)_(.+)$"))
    async def clone_opt_callback(event):
        clone_type = event.pattern_match.group(1)
        phone = event.pattern_match.group(2)
        user_id = event.sender_id
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to clone a profile.", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_CLONE_TARGET",
            "clone_type": clone_type
        }
        
        type_display = {
            "complete": "Complete Profile",
            "name": "Name Only",
            "bio": "Bio Only",
            "photo": "Photo Only"
        }.get(clone_type, "Complete Profile")
        
        prompt_text = utils.format_html_message(
            f"<blockquote><b>» 👤 Clone Profile ({type_display})</b>\n\n"
            f"• Enter The Username (e.g. <code>@username</code>), User ID (e.g. <code>8668706422</code> or <code>tg://openmessage?user_id=8668706422</code>), or <b>forward any message from the target user</b>:</blockquote>"
        )
        
        buttons = [[utils.styled_button("🔙 Cancel", f"clone_profile_{phone}", style="danger")]]
        
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^restore_profile_(.+)$"))
    async def restore_profile_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        
        if not userbot_manager.is_bot_running(phone):
            await event.answer("⚠️ Userbot must be running to restore a profile.", alert=True)
            return
            
        progress_msg = await event.reply("⏳ <b>Restoring original profile, please wait...</b>")
        success, msg = await userbot_manager.restore_original_profile(phone)
        await progress_msg.delete()
        
        if success:
            flash = f"✅ <b>Profile restored!</b>\n{msg}"
        else:
            flash = f"❌ <b>Restoration failed:</b> {msg}"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    # ------------------ Navigation ------------------
    @client.on(events.CallbackQuery(pattern="^menu_my_bots$"))
    async def bots_list_callback(event):
        await event.answer()
        # Always clear impersonation so caller always sees their OWN bots
        _admin_impersonation.pop(event.sender_id, None)
        _admin_impersonation.pop(str(event.sender_id), None)
        await show_bots_list(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern=r"^select_bot_(.+)$"))
    async def select_bot_callback(event):
        await event.answer()
        phone = event.pattern_match.group(1)
        await show_bot_dashboard(event, phone, event.sender_id)

    @client.on(events.CallbackQuery(pattern="^user_load_sessions_from_disk$"))
    async def user_load_sessions_from_disk_callback(event):
        """Lets a user load & log in every account that has a session file on disk."""
        user_id = event.sender_id
        await event.answer("📂 Scanning your session files...", alert=True)

        try:
            files = userbot_manager.list_session_files()
            mine = [f for f in files if str(f.get("owner_id")) == str(user_id)]
            if not mine:
                await event.edit(
                    "<blockquote><b>» 📂 No Session Files Found</b>\n\n"
                    "⚠️ <i>No <code>.session</code> file was found for your account. "
                    "Use <b>Add / Login Userbot</b> to connect a new account.</i></blockquote>",
                    parse_mode="html"
                )
                return

            status_msg = await event.edit(
                "<blockquote><b>» 🔑 Logging In Your Accounts...</b>\n\n"
                f"📁 <b>Session Files :</b> <code>{len(mine)}</code>\n\n"
                "<i>Please wait, this can take a moment…</i></blockquote>",
                parse_mode="html"
            )

            result = await userbot_manager.login_all_sessions_from_disk(owner_id=user_id)
            lines = [
                "<blockquote><b>» 🔑 Account Login Results</b>\n",
                f"📁 <b>Session Files :</b> <code>{len(mine)}</code>\n",
                f"🆕 <b>Newly Registered :</b> <code>{result['imported']}</code>\n",
                f"✅ <b>Logged In (Running) :</b> <code>{result['started']}</code>\n",
                f"❌ <b>Failed :</b> <code>{result['failed']}</code>\n",
            ]
            if result.get("details"):
                lines.append("\n<b>Details :</b>")
                for sid, note in result["details"][:40]:
                    lines.append(f"\n• <code>{sid}</code> — <i>{note}</i>")
            lines.append("\n\n💡 <i>All your accounts are now listed in the dashboard.</i></blockquote>")

            buttons = [[utils.styled_button("📱 My Userbots", "menu_my_bots", style="primary")]]
            try:
                await status_msg.edit("\n".join(lines), buttons=buttons, parse_mode="html")
            except Exception:
                await event.edit("\n".join(lines), buttons=buttons, parse_mode="html")
        except Exception as e:
            logger.error(f"Load sessions from disk failed for {user_id}: {e}")
            utils.report_error(f"my_bots.load_sessions_from_disk:{user_id}", e)
            try:
                await event.edit(
                    "<blockquote><b>» ❌ Could Not Load Accounts</b>\n\n"
                    "⚠️ <i>Something went wrong. The error was reported to the admin team.</i></blockquote>",
                    parse_mode="html"
                )
            except Exception:
                pass

    @client.on(events.CallbackQuery(pattern=r"^admin_ctrl_bot_(.+)$"))
    async def admin_ctrl_bot_callback(event):
        user_id = event.sender_id
        phone_target = event.pattern_match.group(1).decode() if isinstance(event.pattern_match.group(1), bytes) else event.pattern_match.group(1)
        phone_target = str(phone_target).strip()
        
        global_settings = database.get_global_settings()
        admins_list = global_settings.get("admins", [])
        is_admin = user_id in admins_list or user_id in config.ORIGINAL_ADMIN_IDS
        
        if not is_admin:
            await event.answer("⚠️ Access Denied: Only Bot Owner can control this userbot.", alert=True)
            return
            
        sess = database.get_session(phone_target)
        if not sess:
            await event.answer(f"❌ Userbot session not found ({phone_target})", alert=True)
            return
            
        target_user_id = sess.get("user_id")
        # Admins have full access to control without replacing their own userbot dashboard
        
        if not event.is_private:
            try:
                class PMEvent:
                    def __init__(self, c, uid):
                        self.client = c
                        self.sender_id = uid
                        self.chat_id = uid
                        self.is_private = True
                    async def respond(self, *args, **kwargs):
                        return await self.client.send_message(self.sender_id, *args, **kwargs)
                    async def edit(self, *args, **kwargs):
                        return await self.client.send_message(self.sender_id, *args, **kwargs)
                    async def reply(self, *args, **kwargs):
                        return await self.client.send_message(self.sender_id, *args, **kwargs)
                    async def delete(self):
                        pass
                    async def answer(self, *args, **kwargs):
                        pass
                pm_event = PMEvent(client, user_id)
                await show_bot_dashboard(
                    pm_event, 
                    sess.get("phone", phone_target), 
                    user_id, 
                    flash_message=f"<blockquote><b>» 👑 Owner Access :</b> Controlling Userbot <code>{sess.get('phone', phone_target)}</code> (User: <code>{target_user_id}</code>)</blockquote>"
                )
                await event.answer("✅ Userbot Dashboard sent to your PM!", alert=True)
            except Exception as e:
                logger.error(f"Error opening dashboard in PM: {e}")
                await event.answer("⚠️ Could not open in PM. Please start the bot in private chat first.", alert=True)
        else:
            await show_bot_dashboard(
                event, 
                sess.get("phone", phone_target), 
                user_id, 
                flash_message=f"<blockquote><b>» 👑 Owner Access :</b> Controlling Userbot <code>{sess.get('phone', phone_target)}</code> (User: <code>{target_user_id}</code>)</blockquote>"
            )

    @client.on(events.CallbackQuery(pattern="^admin_exit_impersonation$"))
    async def admin_exit_impersonation_callback(event):
        _admin_impersonation.pop(event.sender_id, None)
        from villain_ads_bot.handlers.admin import show_admin_panel
        await show_admin_panel(event, event.sender_id)

    # ------------------ Core Controls ------------------
    @client.on(events.CallbackQuery(pattern=r"^start_bot_(.+)$"))
    async def start_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        # Check if already running
        if userbot_manager.is_bot_running(phone):
            await show_bot_dashboard(event, phone, user_id, flash_message="🟢 <b>Userbot is already running.</b>")
            return

        # Limit check removed

        # Start bot in background
        success = await userbot_manager.start_userbot(phone)
        if success:
            flash = "🟢 <b>Userbot successfully started!</b>"
        else:
            flash = "❌ <b>Failed to start Userbot. Check Telegram session/auth.</b>"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern=r"^stop_bot_(.+)$"))
    async def stop_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        # Stop bot
        await userbot_manager.stop_userbot(phone)
        await show_bot_dashboard(event, phone, user_id, flash_message="🔴 <b>Userbot stopped.</b>")

    @client.on(events.CallbackQuery(pattern=r"^restart_bot_(.+)$"))
    async def restart_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        # Stop
        await userbot_manager.stop_userbot(phone)
        await asyncio.sleep(1.0)
        
        # Start
        success = await userbot_manager.start_userbot(phone)
        if success:
            flash = "🔄 <b>Userbot successfully restarted!</b>"
        else:
            flash = "❌ <b>Failed to start Userbot after stopping.</b>"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern=r"^view_settings_info_(.+)$"))
    async def view_settings_info_callback(event):
        phone = event.pattern_match.group(1)
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        sess = database.get_session(phone)
        if not sess or not is_session_owner_or_admin(sess, event.sender_id):
            await event.answer("❌ Session error.", alert=True)
            return
            
        settings = sess.get("settings", {})
        
        spam_status = "🟢 On" if settings.get("auto_spam") else "🔴 Off"
        welcome_status = "🟢 On" if settings.get("auto_welcome") else "🔴 Off"
        add_contact_status = "🟢 On" if settings.get("auto_add_contact") else "🔴 Off"
        reply_status = "🟢 On" if settings.get("auto_reply") else "🔴 Off"
        # Resolve everything from the data so the panel can never lie.
        utils.resolve_modes(settings)
        spam_mode = settings.get("broadcast_mode", "single")
        welcome_mode = settings.get("welcome_mode", "single")
        reply_mode = settings.get("auto_reply_mode", "single")
        try:
            group_count = len(sess.get("stats", {}).get("group_count", 0) or 0)
        except Exception:
            group_count = 0
        timing = utils.get_broadcast_timing(settings, group_count)
        spam_list = utils.get_broadcast_messages(settings)
        welcome_list = utils.get_welcome_messages(settings)
        reply_list = utils.get_auto_reply_messages(settings)
        spam_msg = spam_list[0] if spam_list else "None"
        welcome_msg = welcome_list[0] if welcome_list else "None"
        welcome_msgs = ", ".join(welcome_list) or "None"
        spam_msgs = ", ".join(spam_list) or "None"
        run_expiry = sess.get("run_expiry")
        if run_expiry:
            import time as _t
            remaining = int(max(0, float(run_expiry) - _t.time()))
            run_timer_txt = f"⏳ <b>{remaining // 3600}h {(remaining % 3600) // 60}m {remaining % 60}s</b> remaining"
        else:
            run_timer_txt = "🔴 <b>Not set</b>"
        
        # Format a clean message
        text = (
            f"<blockquote><b>» ℹ️ Userbot Settings Info</b>\n\n"
            f"• <b>📞 Account :</b> <code>{phone}</code>\n"
            f"• <b>🏷️ Name :</b> <b>{sess.get('name', 'Userbot')}</b>\n"
            f"• <b>🔗 Username :</b> @{sess.get('username', 'None')}\n\n"
            f"<b>📢 Auto Broadcast Settings :</b>\n"
            f"• <b>Status :</b> {spam_status}\n"
            f"• <b>Mode :</b> <b>{'📚 Multiple (' + str(len(spam_list)) + ' msgs)' if spam_mode == 'multiple' else '✉️ Single'}</b>\n"
            f"• <b>Group delay :</b> <b>{timing['group_delay_text']}</b>\n"
            f"• <b>Round duration :</b> <b>{timing['round_text']}</b>\n"
            f"• <b>Repeat interval :</b> <b>{timing['repeat_text']}</b>\n"
            f"• <b>⭐ Real period :</b> <b>{timing['real_period_text']}</b>\n"
            f"• <b>Broadcast Msg ({len(spam_list)}) :</b>\n"
            f"  <code>{spam_msgs}</code>\n\n"
            f"<b>👋 Auto-Welcome Settings :</b>\n"
            f"• <b>Status :</b> {welcome_status}\n"
            f"• <b>Mode :</b> <b>{'📚 Multiple (' + str(len(welcome_list)) + ' msgs)' if welcome_mode == 'multiple' else '👋 Single'}</b>\n"
            f"• <b>Welcome Msg ({len(welcome_list)}) :</b>\n"
            f"  <code>{welcome_msgs}</code>\n\n"
            f"<b>💬 Tag Auto-Reply Settings :</b>\n"
            f"• <b>Status :</b> {reply_status}\n"
            f"• <b>Mode :</b> <b>{'📚 Multiple (' + str(len(reply_list)) + ' msgs)' if reply_mode == 'multiple' else '💬 Single'}</b>\n\n"
            f"<b>👥 Auto-Contact Settings :</b>\n"
            f"• <b>Status :</b> {add_contact_status}\n\n"
            f"<b>⏱️ Run Timer :</b> {run_timer_txt}</blockquote>"
        )
        
        buttons = [[utils.styled_button("🔙 Back To Dashboard", f"select_bot_{phone}", style="primary")]]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^delete_bot_(.+)$"))
    async def delete_bot_callback(event):
        phone = event.pattern_match.group(1)
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        await userbot_manager.remove_userbot(phone)
        await show_bots_list(event, user_id, flash_message="🗑️ <b>Userbot session successfully deleted.</b>")

    # ------------------ Toggles ------------------
    @client.on(events.CallbackQuery(pattern=r"^toggle_(spam|welcome|add_contact|reply)_(?!mode_)(\+?\d+)$"))
    async def toggles_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        feature = event.pattern_match.group(1)
        phone = event.pattern_match.group(2).strip()
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        sess = database.get_session(phone)
        flash = None
        if sess and is_session_owner_or_admin(sess, event.sender_id):
            settings = sess.setdefault("settings", {})
            
            key_map = {
                "spam": "auto_spam",
                "welcome": "auto_welcome",
                "add_contact": "auto_add_contact",
                "reply": "auto_reply"
            }
            db_key = key_map[feature]
            settings[db_key] = not settings.get(db_key, False)
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            
            state_word = "🟢 On" if settings[db_key] else "🔴 Off"
            feature_name = "Auto Broadcast" if feature == "spam" else ("Auto-Welcome" if feature == "welcome" else ("Auto-Contact" if feature == "add_contact" else "Tag Auto-Reply"))
            flash = f"<blockquote><b>» ⚙️ Settings Updated</b>\n\n{feature_name} Is Now <b>{state_word}</b></blockquote>"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    # ------------------ Stats Refresh ------------------
    @client.on(events.CallbackQuery(pattern=r"^refresh_stats_(.+)$"))
    async def refresh_stats_callback(event):
        phone = event.pattern_match.group(1)
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        sess = database.get_session(phone)
        flash = None
        if sess and is_session_owner_or_admin(sess, event.sender_id):
            if userbot_manager.is_bot_running(phone):
                bot_obj = userbot_manager.find_running_bot(phone)
                try:
                    # Force refresh the groups cache, which also updates the DB stats
                    groups = await bot_obj.get_groups(force_refresh=True)
                    sess = database.get_session(phone)
                    users = sess["stats"]["user_count"]
                    
                    flash = f"🔄 <b>Stats refreshed! Groups: {len(groups)} | Contacts: {users}</b>"
                except Exception as e:
                    logger.error(f"Error refreshing stats: {e}")
                    utils.report_error(f"my_bots.refresh_stats:{phone}", e)
                    flash = "❌ <b>Error during refresh.</b> <i>The error was reported to the admin team.</i>"
            else:
                flash = "⚠️ <b>Bot must be running to refresh statistics.</b>"
                
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    # ------------------ Broadcast Message Sub-Menu ------------------
    @client.on(events.CallbackQuery(pattern=r"^set_broadcast_(.+)$"))
    async def set_broadcast_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        await render_broadcast_menu(event, phone, user_id)

    @client.on(events.CallbackQuery(pattern=r"^set_single_msg_(.+)$"))
    async def set_single_msg_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_BROADCAST"
        }
        prompt_text = utils.get_text("prompt_broadcast", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"set_broadcast_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_multi_msg_(.+)$"))
    async def set_multi_msg_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_MULTI_MSG"
        }
        prompt_text = (
            "<blockquote><b>» 📚 Set Multiple Messages</b>\n\n"
            "Send Your Multiple Broadcast Messages Separated By Commas (<code>,</code>). The Bot Will Rotate/Pick One Message For Each Group.\n\n"
            "💡 <b>Example Input :</b>\n"
            "<code>Hey Check This Out!, Join Our Channel Now!, Best Deals Today!</code>\n\n"
            "✍️ <b>Send Your Comma-Separated Message List Below :</b></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"set_broadcast_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^toggle_broadcast_mode_(.+)$"))
    async def toggle_broadcast_mode_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        if sess and is_session_owner_or_admin(sess, event.sender_id):
            settings = sess.setdefault("settings", {})
            current_mode = settings.get("broadcast_mode", "single")
            new_mode = "multiple" if current_mode == "single" else "single"
            settings["broadcast_mode"] = new_mode
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            
        await render_broadcast_menu(event, phone, user_id)

    # ------------------ DM Welcome Message Sub-Menu ------------------
    @client.on(events.CallbackQuery(pattern=r"^set_welcome_(.+)$"))
    async def set_welcome_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        await render_welcome_menu(event, phone, user_id)

    @client.on(events.CallbackQuery(pattern=r"^set_single_welcome_(.+)$"))
    async def set_single_welcome_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_WELCOME"
        }
        prompt_text = utils.get_text("prompt_welcome", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"set_welcome_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_multi_welcome_(.+)$"))
    async def set_multi_welcome_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_MULTI_WELCOME"
        }
        prompt_text = utils.get_text("prompt_multi_welcome", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"set_welcome_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^toggle_welcome_mode_(.+)$"))
    async def toggle_welcome_mode_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        if sess and is_session_owner_or_admin(sess, event.sender_id):
            settings = sess.setdefault("settings", {})
            current_mode = settings.get("welcome_mode", "single")
            new_mode = "multiple" if current_mode == "single" else "single"
            settings["welcome_mode"] = new_mode
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            
        await render_welcome_menu(event, phone, user_id)

    # ------------------ Tag Auto-Reply Sub-Menu ------------------
    @client.on(events.CallbackQuery(pattern=r"^set_auto_reply_(.+)$"))
    async def set_auto_reply_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        await render_auto_reply_menu(event, phone, user_id)

    @client.on(events.CallbackQuery(pattern=r"^set_single_reply_(.+)$"))
    async def set_single_reply_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_AUTO_REPLY_SINGLE"
        }
        prompt_text = "<blockquote><b>» 💬 Set Single Group Tag Reply</b>\n\nSend Your Single Group Tag Auto-Reply Message Below :</blockquote>"
        buttons = [[utils.styled_button("🔙 Cancel", f"set_auto_reply_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_multi_reply_(.+)$"))
    async def set_multi_reply_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_AUTO_REPLY_MSGS"
        }
        prompt_text = utils.get_text("prompt_set_auto_reply", lang)
        buttons = [[utils.styled_button("🔙 Cancel", f"set_auto_reply_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^toggle_reply_mode_(.+)$"))
    async def toggle_reply_mode_callback(event):
        try:
            await event.answer()
        except Exception:
            pass
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        if sess and is_session_owner_or_admin(sess, event.sender_id):
            settings = sess.setdefault("settings", {})
            current_mode = settings.get("auto_reply_mode", "single")
            new_mode = "multiple" if current_mode == "single" else "single"
            settings["auto_reply_mode"] = new_mode
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            userbot_manager.reload_bot_settings(sess.get("session_id", phone))
            
        await render_auto_reply_menu(event, phone, user_id)

    @client.on(events.CallbackQuery(pattern=r"^change_name_(.+)$"))
    async def change_name_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_NAME"
        }
        prompt_text = (
            "<blockquote><b>» ✏️ Change Userbot Name</b>\n\n"
            "Send The New Name For This Userbot Account :</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"select_bot_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^play_song_(.+)$"))
    async def play_song_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = event.sender_id
        bot_obj = userbot_manager._running_bots.get(phone)
        if not bot_obj:
            await userbot_manager.start_userbot(phone)
            bot_obj = userbot_manager._running_bots.get(phone)
            
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_SONG"
        }
        
        prompt_text = utils.format_html_message(
            "<blockquote><b>» 🎵 Voice Chat Music Player</b>\n\n"
            "• Send <code>/play &lt;song name / yt link&gt;</code> To Stream Audio.\n"
            "• Send <code>/vplay &lt;video name / yt link&gt;</code> To Stream Video.\n"
            "• Reply To Any Audio/Video File With <code>/play</code> Or <code>/vplay</code>.\n\n"
            "✍️ <b>Type Your Song Name Or Youtube Link Below:</b></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", f"vc_menu_{phone}", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_play_song$"))
    async def all_slots_play_song_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found. Please add a userbot first!", alert=True)
            return
            
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_SONG"
        }
        
        prompt_text = utils.format_html_message(
            "<blockquote><b>» 🎵 Voice Chat Music Player (All Slots)</b>\n\n"
            "• Send <code>/play &lt;song name / yt link&gt;</code> To Stream Audio Across All Vcs.\n"
            "• Send <code>/vplay &lt;video name / yt link&gt;</code> To Stream Video Across All Vcs.\n"
            "• All Userbots Will Auto-Start And Stream Simultaneously!\n\n"
            "✍️ <b>Type Your Song Name Or Youtube Link Below:</b></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_vc_menu", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)



    # ------------------ Interval settings ------------------
    @client.on(events.CallbackQuery(pattern=r"^set_interval_(.+)$"))
    async def set_interval_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        sess = database.get_session(phone)
        settings = sess.get("settings", {}) if sess else {}
        current_delay = settings.get("inter_group_delay", 30.0)
        current_interval = settings.get("broadcast_interval", 300)
        name = sess.get("name") or "UserBot" if sess else "UserBot"
        
        # How many groups will this userbot reach? (for the real timing)
        bot_obj = userbot_manager.find_running_bot(phone)
        try:
            bot_groups = len(await bot_obj.get_groups(force_refresh=False)) if bot_obj else 0
        except Exception:
            bot_groups = 0
        timing = utils.get_broadcast_timing(settings, bot_groups)
        
        text = (
            f"{ui.card(utils.get_text('timing_title', lang), f'Account : {name}', 'How often your broadcast really runs', '⏱️')}\n\n"
            f"{ui.kv(utils.get_text('timing_group_delay', lang), timing['group_delay_text'], '⏱️')}\n"
            f"{ui.kv(utils.get_text('timing_round_duration', lang), timing['round_text'], '🔁')}\n"
            f"{ui.kv(utils.get_text('timing_repeat_interval', lang), timing['repeat_text'], '⏸️')}\n"
            f"{ui.kv(utils.get_text('timing_real_period', lang), timing['real_period_text'], '⭐')}\n"
            f"{ui.kv('Groups reached', str(bot_groups), '👥')}\n\n"
            f"{ui.help_hint('The real period = one full round (all groups) + the repeat interval. The old panel only showed the repeat interval, which is why the timing looked wrong.')}"
            f"\n{ui.footer()}</blockquote>"
        )
        buttons = [
            [
                utils.styled_button("⚡ Best Timing (45s Delay | 5m Repeat)", f"apply_best_timing_{phone}", style="success")
            ],
            [
                utils.styled_button("⏱️ Custom Group Delay", f"set_inter_delay_{phone}", style="primary"),
                utils.styled_button("🔄 Custom Loop Interval", f"set_loop_interval_{phone}", style="primary")
            ],
            [
                utils.styled_button("🔙 Back To Dashboard", f"select_bot_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^apply_best_timing_(.+)$"))
    async def apply_best_timing_callback(event):
        phone = event.pattern_match.group(1).strip()
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        
        sess = database.get_session(phone)
        if sess and is_session_owner_or_admin(sess, event.sender_id):
            settings = sess.setdefault("settings", {})
            settings["inter_group_delay"] = 45.0
            settings["broadcast_interval"] = 300
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            flash = (
                "<blockquote><b>» ⚡ Best Timing Applied!</b>\n\n"
                "• <b>Group delay :</b> <b>45s</b>\n"
                "• <b>Repeat interval :</b> <b>5m</b>\n"
                "• <i>The real period also includes the time one full round needs.</i></blockquote>"
            )
        else:
            flash = "<blockquote><b>» ❌ Session Not Found.</b></blockquote>"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    # ------------------ All Slots Timing Settings ------------------
    @client.on(events.CallbackQuery(pattern="^all_slots_set_interval$"))
    async def all_slots_set_interval_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        total = len(sessions)
        
        text = (
            f"<blockquote><b>» ⏱️ All Userbots Timing & Delay Settings</b>\n\n"
            f"Configure Broadcasting Speeds For All <b>{total}</b> Userbots Simultaneously:\n\n"
            f"• <b>Group Delay :</b> <code>45s</code> (Safe & Anti-Flood Delay)\n"
            f"• <b>Loop Repeat :</b> <code>300s</code> (5-Minute Repeat Cycle)</blockquote>"
        )
        buttons = [
            [
                utils.styled_button("⚡ Best Timing (All: 45s Delay | 300s Loop)", "all_slots_apply_best_timing", style="success")
            ],
            [
                utils.styled_button("⏱️ Custom Group Delay (All)", "all_slots_set_inter_delay", style="primary"),
                utils.styled_button("🔄 Custom Loop Interval (All)", "all_slots_set_loop_interval", style="primary")
            ],
            [
                utils.styled_button("🔙 Back To Dashboard", "menu_all_slots", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_apply_best_timing$"))
    async def all_slots_apply_best_timing_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        sessions = get_effective_sessions(event.sender_id)
        if not sessions:
            await event.answer("⚠️ No slots found.", alert=True)
            return
            
        for s in sessions:
            s.setdefault("settings", {})["inter_group_delay"] = 45.0
            s["settings"]["broadcast_interval"] = 300
            database.save_session(s)
            if userbot_manager.is_bot_running(s["phone"]):
                userbot_manager.reload_bot_settings(s["phone"])
                
        flash = f"<blockquote><b>» ⚡ Best Timing Applied To {len(sessions)} Userbots!</b></blockquote>"
        await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(event.sender_id))

    @client.on(events.CallbackQuery(pattern="^all_slots_set_inter_delay$"))
    async def all_slots_set_inter_delay_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_CUSTOM_DELAY"
        }
        prompt_text = (
            "<blockquote><b>» ⏱️ Custom Group-To-Group Delay (All Bots)</b>\n\n"
            "Send The Delay In Seconds Between Sending Messages To Different Groups (E.G. <code>45</code>):\n"
            "<i>(Must Be Between 2 And 300 Seconds)</i></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_interval", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^all_slots_set_loop_interval$"))
    async def all_slots_set_loop_interval_callback(event):
        user_id = _admin_impersonation.get(event.sender_id, event.sender_id)
        _bot_action_states[user_id] = {
            "action": "WAITING_FOR_ALL_CUSTOM_INTERVAL"
        }
        prompt_text = (
            "<blockquote><b>» 🔄 Custom Loop Repeat Interval (All Bots)</b>\n\n"
            "Send The Total Broadcast Loop Interval In Seconds (E.G. <code>300</code> For 5 Minutes):\n"
            "<i>(Must Be 60 Seconds Or Higher)</i></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "all_slots_set_interval", style="primary")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^set_loop_interval_(.+)$"))
    async def set_loop_interval_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("interval_title", lang)
        
        buttons = [
            [
                utils.styled_button(utils.get_text("btn_int_val", lang, val=300), f"int_val_300_{phone}", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=500), f"int_val_500_{phone}", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=600), f"int_val_600_{phone}", style="primary")
            ],
            [
                utils.styled_button(utils.get_text("btn_int_custom", lang), f"int_custom_{phone}", style="primary"),
                utils.styled_button("🔙 Back", f"set_interval_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^int_val_(\d+)_(.+)$"))
    async def int_val_callback(event):
        val = int(event.pattern_match.group(1))
        phone = event.pattern_match.group(2)
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        flash = None
        if sess and is_session_owner_or_admin(sess, event.sender_id):
            sess.setdefault("settings", {})["broadcast_interval"] = val
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            flash = f"<blockquote><b>» ⏱️ Interval Updated To {val}s</b></blockquote>"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern=r"^int_custom_(.+)$"))
    async def int_custom_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_CUSTOM_INTERVAL"
        }
        
        prompt_text = utils.get_text("prompt_custom_interval", lang)
        try:
            buttons = [[utils.styled_button("🔙 Cancel", f"set_loop_interval_{phone}", style="primary")]]
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text)

    @client.on(events.CallbackQuery(pattern=r"^set_inter_delay_(.+)$"))
    async def set_inter_delay_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        text = utils.get_text("inter_delay_title", lang)
        
        buttons = [
            [
                utils.styled_button(utils.get_text("btn_int_val", lang, val=20), f"del_val_20_{phone}", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=30), f"del_val_30_{phone}", style="primary"),
                utils.styled_button(utils.get_text("btn_int_val", lang, val=60), f"del_val_60_{phone}", style="primary")
            ],
            [
                utils.styled_button(utils.get_text("btn_del_custom", lang), f"del_custom_{phone}", style="primary"),
                utils.styled_button("🔙 Back", f"set_interval_{phone}", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^del_val_(\d+)_(.+)$"))
    async def del_val_callback(event):
        val = int(event.pattern_match.group(1))
        phone = event.pattern_match.group(2)
        user_id = event.sender_id
        
        sess = database.get_session(phone)
        flash = None
        if sess and is_session_owner_or_admin(sess, event.sender_id):
            sess.setdefault("settings", {})["inter_group_delay"] = val
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            flash = f"<blockquote><b>» ⏱️ Inter-Group Delay Updated To {val}s</b></blockquote>"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)

    @client.on(events.CallbackQuery(pattern=r"^del_custom_(.+)$"))
    async def del_custom_callback(event):
        phone = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        _bot_action_states[user_id] = {
            "phone": phone,
            "action": "WAITING_FOR_CUSTOM_DELAY"
        }
        
        prompt_text = utils.get_text("prompt_custom_inter_delay", lang)
        try:
            buttons = [[utils.styled_button("🔙 Cancel", f"set_inter_delay_{phone}", style="primary")]]
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text)

    # ------------------ Message Input Listeners ------------------
    @client.on(events.NewMessage)
    async def text_input_handler(event):
        if not event.is_private:
            return
            
        user_id = event.sender_id
        
        # Check for any slash command first
        cmd_text = event.text.strip() if event.text else ""
        if cmd_text.startswith("/"):
            # Clear pending state on commands so standard command handlers process them
            _bot_action_states.pop(user_id, None)
            _target = _admin_impersonation.get(user_id)
            if _target is not None:
                _bot_action_states.pop(_target, None)
            return

        # The pending state may have been stored under the sender id OR under the
        # impersonated target id (admin / owner mode). Look up both so the
        # "apply to all" flows work for admins too.
        state = None
        if user_id in _bot_action_states:
            state = _bot_action_states.pop(user_id)
        else:
            _target = _admin_impersonation.get(user_id)
            if _target is None:
                _target = _admin_impersonation.get(str(user_id))
            if _target is not None and _target in _bot_action_states:
                state = _bot_action_states.pop(_target)
            elif _admin_impersonation.get(str(user_id)) in _bot_action_states:
                state = _bot_action_states.pop(_admin_impersonation.get(str(user_id)))

        if not state:
            return
            
        raw_txt = (event.text or "").strip().lower()
        if raw_txt in ("/cancel", "cancel", "back", "/abort", "abort", "🔙 cancel", "🔙 back"):
            phone = state.get("phone")
            if phone:
                await show_bot_dashboard(event, phone, user_id, flash_message="❌ <b>Action cancelled.</b>")
            else:
                await show_all_slots_dashboard(event, user_id, flash_message="❌ <b>Action cancelled.</b>", fetch_all=is_system_all_mode(user_id))
            return
            
        phone = state.get("phone")
        action = state["action"]
        
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        flash = None
        
        # --- Handle All Slots Dashboard Text Actions ---
        if action == "WAITING_FOR_ALL_VC_LINK":
            link = event.text.strip()
            if not link:
                await event.reply("<blockquote><b>❌ Chat Id / Username / Link Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
                
            sessions = get_effective_sessions(user_id)
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            if not running_phones:
                await event.reply("<blockquote><b>❌ No Userbots Are Currently Running. Please Start Your Userbots First.</b></blockquote>", parse_mode="html")
                return
                
            progress_msg = await event.reply(f"<blockquote><b>» ⏳ Joining Voice Chat On {len(running_phones)} Running Userbots...</b></blockquote>", parse_mode="html")
            
            async def _join_vc_concurrent(phone_num):
                bot_obj = userbot_manager.find_running_bot(phone_num)
                if not bot_obj:
                    return phone_num, False, "Userbot is not running."
                success, msg = await bot_obj.join_voice_chat(link)
                return phone_num, success, msg
                
            results = await asyncio.gather(*[_join_vc_concurrent(p) for p in running_phones], return_exceptions=True)
            try:
                await progress_msg.delete()
            except Exception:
                pass
            
            success_count = 0
            fail_msgs = []
            for res in results:
                if isinstance(res, Exception):
                    fail_msgs.append(f"⚠️ Task Error: {res}")
                    continue
                phone_num, success, msg = res
                if success:
                    success_count += 1
                else:
                    fail_msgs.append(f"📞 `{phone_num}`: {msg}")
                    
            flash = f"<blockquote><b>» 🎙️ All Slots : Vc Join Results</b>\n\n• <b>Successfully Joined :</b> <b>{success_count} / {len(running_phones)}</b> Userbots</blockquote>"
            if fail_msgs:
                flash += f"\n\n<b>Errors:</b>\n" + "\n".join(fail_msgs)
                
            await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
            return

        elif action == "WAITING_FOR_ALL_BROADCAST":
            broadcast_msg = event.text
            sessions = get_effective_sessions(user_id)
            for s in sessions:
                s.setdefault("settings", {})["broadcast_msg"] = broadcast_msg
                utils.resolve_modes(s.setdefault("settings", {}))
                # Setting a broadcast message also ENABLES the auto broadcast,
                # otherwise the message would never be sent.
                s["settings"]["auto_spam"] = True
            # Save once per session AFTER all changes so the async writer can
            # never persist an older snapshot last.
            for s in sessions:
                database.save_session(s)
                userbot_manager.reload_bot_settings(s.get("phone", ""))
                userbot_manager.reload_bot_settings(s.get("session_id", ""))
            flash = "<blockquote><b>» ✉️ Broadcast Message Updated For All Bots!</b>\n\n<i>✅ Auto Broadcast has been enabled for all selected userbots.</i></blockquote>"
            await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
            return
            
        elif action == "WAITING_FOR_ALL_WELCOME":
            welcome_msg = event.text
            sessions = get_effective_sessions(user_id)
            for s in sessions:
                s.setdefault("settings", {})["welcome_msg"] = welcome_msg
                utils.resolve_modes(s.setdefault("settings", {}))
                # Setting a welcome message also ENABLES the auto welcome.
                s["settings"]["auto_welcome"] = True
            for s in sessions:
                database.save_session(s)
                userbot_manager.reload_bot_settings(s.get("phone", ""))
                userbot_manager.reload_bot_settings(s.get("session_id", ""))
            flash = "<blockquote><b>» 👋 Welcome Message Updated For All Bots!</b>\n\n<i>✅ Auto Welcome has been enabled for all selected userbots.</i></blockquote>"
            await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
            return

        elif action == "WAITING_FOR_ALL_MULTI_WELCOME":
            msgs = [x.strip() for x in event.text.split("|") if x.strip()]
            if not msgs:
                await event.reply("<blockquote><b>» ❌ Input Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
            sessions = get_effective_sessions(user_id)
            for s in sessions:
                s.setdefault("settings", {})["welcome_messages"] = msgs
                # The mode follows the data - no manual switch needed.
                utils.resolve_modes(s.setdefault("settings", {}))
                # Switch the runtime into "multiple" mode, otherwise the
                # single (default) welcome message would still be used.
                s["settings"]["welcome_mode"] = "multiple"
                s["settings"]["auto_welcome"] = True
            for s in sessions:
                database.save_session(s)
                userbot_manager.reload_bot_settings(s.get("phone", ""))
                userbot_manager.reload_bot_settings(s.get("session_id", ""))
            flash = "<blockquote><b>» 👋 Multiple Welcome Messages Updated For All Bots!</b>\n\n<i>✅ Multiple (Rotational) mode + Auto Welcome enabled.</i></blockquote>"
            await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
            return
 
        elif action == "WAITING_FOR_ALL_CLONE_TARGET":
            target_entity = None
            raw_text = (getattr(event, "raw_text", None) or event.text or "").strip()
            
            # Check if user forwarded a message from the target user
            if getattr(event.message, "forward", None):
                try:
                    fwd_sender = await event.message.forward.get_sender()
                    if fwd_sender:
                        target_entity = fwd_sender
                        if getattr(fwd_sender, "username", None):
                            target = f"@{fwd_sender.username}"
                        else:
                            target = str(fwd_sender.id)
                except Exception as fwd_err:
                    logger.warning(f"Could not get sender from forward: {fwd_err}")
                    
            if not target_entity:
                target = raw_text
                
            if not target and not target_entity:
                await event.reply("<blockquote><b>» ❌ Target Cannot Be Empty. Please Enter A Valid Username, User ID, Link, Or Forward A Message.</b></blockquote>", parse_mode="html")
                return
                
            clone_type = state.get("clone_type", "complete")
            clone_label = {
                "name": "Name",
                "bio": "Bio",
                "photo": "Photo",
                "complete": "Profile Details"
            }.get(clone_type, "Profile")
            sessions = get_effective_sessions(user_id)
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            if not running_phones:
                await event.reply("<blockquote><b>» ❌ No Userbots Are Currently Running. Please Start Your Userbots First.</b></blockquote>", parse_mode="html")
                return
                
            progress_msg = await event.reply(f"<blockquote><b>» ⏳ Cloning {clone_label} On {len(running_phones)} Running Userbots...</b></blockquote>", parse_mode="html")
            
            async def _clone_concurrent(phone_num):
                return await userbot_manager.clone_profile(
                    phone_num, 
                    target, 
                    clone_type=clone_type, 
                    fallback_client=client, 
                    target_entity=target_entity
                )
                
            results = await asyncio.gather(*[_clone_concurrent(p) for p in running_phones], return_exceptions=True)
            try:
                await progress_msg.delete()
            except Exception:
                pass
            
            success_count = 0
            fail_msgs = []
            for res, phone_num in zip(results, running_phones):
                if isinstance(res, Exception):
                    fail_msgs.append(f"📞 <code>{phone_num}</code>: {type(res).__name__}")
                    continue
                if res[0]:
                    success_count += 1
                else:
                    fail_msgs.append(f"📞 <code>{phone_num}</code>: {res[1]}")
                    
            flash = f"<blockquote><b>» 👤 Profile Cloning Results</b>\n• Cloned Successfully On {success_count}/{len(running_phones)} Userbots!</blockquote>"
            if fail_msgs:
                flash += "\n\n<b>Errors :</b>\n" + "\n".join(fail_msgs[:15])
            await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
            return
 
        elif action == "WAITING_FOR_ALL_NAME":
            new_name = event.text.strip()
            if not new_name:
                await event.reply("<blockquote><b>» ❌ Name Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
                
            sessions = get_effective_sessions(user_id)
            if not sessions:
                await event.reply("<blockquote><b>» ❌ No Userbot Slots Found.</b></blockquote>", parse_mode="html")
                return
                
            progress_msg = await event.reply(f"<blockquote><b>» ⏳ Updating Name To '{new_name}' Across All Slots...</b></blockquote>", parse_mode="html")
            
            async def _update_one_name(s):
                return await userbot_manager.set_userbot_name(s["phone"], new_name)
                
            results = await asyncio.gather(*[_update_one_name(s) for s in sessions], return_exceptions=True)
            try:
                await progress_msg.delete()
            except Exception:
                pass
                
            success_count = sum(1 for r in results if not isinstance(r, Exception) and r[0])
            flash = f"<blockquote><b>» ✏️ Updated Name To '{new_name}' For {success_count}/{len(sessions)} Userbots!</b></blockquote>"
            await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
            return
 
        elif action == "WAITING_FOR_ALL_CUSTOM_INTERVAL":
            val_str = event.text.strip()
            if val_str.isdigit() and int(val_str) >= 60:
                val = int(val_str)
                sessions = get_effective_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["broadcast_interval"] = val
                    database.save_session(s)
                    userbot_manager.reload_bot_settings(s["phone"])
                flash = f"<blockquote><b>» ⏱️ Interval Updated To {val}s For All Bots!</b></blockquote>"
                await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
                return
            else:
                await event.reply(utils.get_text("interval_invalid", lang))
                return
 
        elif action == "WAITING_FOR_ALL_CUSTOM_DELAY":
            val_str = event.text.strip()
            if val_str.isdigit() and 2 <= int(val_str) <= 300:
                val = int(val_str)
                sessions = get_effective_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["inter_group_delay"] = val
                    database.save_session(s)
                    userbot_manager.reload_bot_settings(s["phone"])
                flash = f"<blockquote><b>» ⏱️ Inter-Group Delay Updated To {val}s For All Bots!</b></blockquote>"
                await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
                return
            else:
                await event.reply(utils.get_text("inter_delay_invalid", lang))
                return
 
        elif action == "WAITING_FOR_ALL_MULTI_MSG":
            raw_text = event.text
            msgs = [m.strip() for m in raw_text.split(",") if m.strip()]
            if msgs:
                sessions = get_effective_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["broadcast_messages"] = msgs
                    # The mode follows the data - no manual switch needed.
                    utils.resolve_modes(s.setdefault("settings", {}))
                    # Switch the runtime into "multiple" mode, otherwise the
                    # single (default) broadcast message would still be used.
                    s["settings"]["broadcast_mode"] = "multiple"
                    s["settings"]["auto_spam"] = True
                for s in sessions:
                    database.save_session(s)
                    userbot_manager.reload_bot_settings(s.get("phone", ""))
                    userbot_manager.reload_bot_settings(s.get("session_id", ""))
                flash = f"<blockquote><b>» ✅ Randomized Messages Updated For All Bots ({len(msgs)} Msgs)!</b>\n\n<i>✅ Multiple (Rotational) mode + Auto Broadcast enabled.</i></blockquote>"
                await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
                return
            else:
                await event.reply("<blockquote><b>» ❌ Message List Cannot Be Empty. Separate With Commas (,).</b></blockquote>", parse_mode="html")
                return

        elif action == "WAITING_FOR_ALL_AUTO_REPLY_SINGLE":
            reply_msg = event.text.strip()
            if reply_msg:
                sessions = get_effective_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["auto_reply_msg"] = reply_msg
                    s["settings"]["auto_reply"] = True
                    s["settings"]["auto_reply_mode"] = "single"
                    database.save_session(s)
                    userbot_manager.reload_bot_settings(s.get("phone", ""))
                    userbot_manager.reload_bot_settings(s.get("session_id", ""))
                flash = "<blockquote><b>» 💬 Single Tag Auto-Reply Message Updated For All Bots!</b></blockquote>"
                await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
                return
            else:
                await event.reply("<blockquote><b>» ❌ Message Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return

        elif action == "WAITING_FOR_ALL_AUTO_REPLY_MSGS":
            raw_text = event.text
            if "\n" in raw_text and "," not in raw_text:
                raw_msgs = raw_text.split("\n")
            elif "|" in raw_text and "," not in raw_text:
                raw_msgs = raw_text.split("|")
            else:
                raw_msgs = raw_text.split(",")
            msgs = [m.strip() for m in raw_msgs if m.strip()]
            if msgs:
                sessions = get_effective_sessions(user_id)
                for s in sessions:
                    s.setdefault("settings", {})["auto_reply_messages"] = msgs
                    # The mode follows the data - no manual switch needed.
                    utils.resolve_modes(s.setdefault("settings", {}))
                    s["settings"]["auto_reply"] = True
                    s["settings"]["auto_reply_mode"] = "multiple"
                    database.save_session(s)
                    userbot_manager.reload_bot_settings(s.get("phone", ""))
                    userbot_manager.reload_bot_settings(s.get("session_id", ""))
                flash = f"<blockquote><b>» ✅ Tag Auto-Reply Messages Updated For All Bots ({len(msgs)} Msgs)!</b></blockquote>"
                await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
                return
            else:
                await event.reply(utils.get_text("auto_reply_invalid", lang))
                return

        elif action == "WAITING_FOR_ALL_RUN_TIMER":
            raw_text = event.text.strip().lower()
            try:
                if raw_text.endswith("m"):
                    minutes = float(raw_text[:-1])
                    seconds = minutes * 60
                else:
                    hours = float(raw_text)
                    seconds = hours * 3600
                    
                if seconds == 0:
                    sessions = get_effective_sessions(user_id)
                    for s in sessions:
                        if "run_expiry" in s:
                            del s["run_expiry"]
                        database.save_session(s)
                    flash = "<blockquote><b>» ✅ Run Timer Disabled For All Bots!</b></blockquote>"
                else:
                    import time
                    expiry_time = time.time() + seconds
                    sessions = get_effective_sessions(user_id)
                    for s in sessions:
                        s["run_expiry"] = expiry_time
                        database.save_session(s)
                    flash = f"<blockquote><b>» ✅ Run Timer Set! All Bots Will Stop After {raw_text}.</b></blockquote>"
                
                await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
                return
            except ValueError:
                await event.reply("<blockquote><b>» ❌ Invalid Format. Please Send A Number (E.G., 2 For Hours, 30m For Minutes, 0 To Disable).</b></blockquote>", parse_mode="html")
                return

        elif action == "WAITING_FOR_ALL_SONG":
            # Extract media info using extract_media_info
            media_obj, audio_title, audio_duration = extract_media_info(event.message)
            is_audio_file = False
            local_file_path = None
            
            if media_obj:
                is_audio_file = True
                progress_msg = await event.reply("📥 <b>Downloading uploaded media...</b>\n━━━━━━━━━━━━━━━━━━━━\n📊 Progress: `[░░░░░░░░░░] 0.0%`")
                os.makedirs("downloads", exist_ok=True)
                try:
                    local_file_path = await client.download_media(
                        event.message, 
                        file="downloads/",
                        progress_callback=lambda c, t: download_progress_sync(c, t, progress_msg, "Downloading uploaded media")
                    )
                except Exception as dl_err:
                    logger.error(f"Failed to download media: {dl_err}")
                    await progress_msg.edit(f"❌ <b>Failed to download media:</b> {dl_err}")
                    return
                finally:
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass
            
            query = None
            if not is_audio_file:
                query = event.text.strip() if event.text else ""
                if not query:
                    await event.reply("<blockquote><b>» ❌ Please Provide A Song Query Or Send An Audio File.</b></blockquote>", parse_mode="html")
                    return
                    
            sessions = get_effective_sessions(user_id)
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            
            vc_bots = []
            for p in running_phones:
                bot_obj = userbot_manager.find_running_bot(p)
                if getattr(bot_obj, "current_vc_chat_id", None):
                    vc_bots.append((p, bot_obj))
                    
            if not vc_bots:
                await event.reply("<blockquote><b>» ❌ No Running Userbots Are In A Voice Chat.</b></blockquote>", parse_mode="html")
                return
                
            progress_msg = await event.reply(f"<blockquote><b>» ⏳ Starting Play On {len(vc_bots)} Userbots Concurrently...</b></blockquote>", parse_mode="html")
            
            async def _play_one_all_concurrent(p, bot_obj):
                return await bot_obj.play_song(query, play_type="audio", local_file=local_file_path, title=audio_title, duration=audio_duration)
                
            results = await asyncio.gather(*[_play_one_all_concurrent(p, bot) for p, bot in vc_bots], return_exceptions=True)
            try:
                await progress_msg.delete()
            except Exception:
                pass
            
            success_count = 0
            song_info_global = None
            for res in results:
                if not isinstance(res, Exception) and res[0]:
                    success_count += 1
                    song_info_global = res[2]
                    
            if success_count > 0 and song_info_global:
                caption = (
                    f"> 🎵 <b>Now Playing (All Slots)</b>\n"
                    f"> \n"
                    f"> • <b>Title</b>: `{song_info_global['title']}`\n"
                    f"> • <b>Duration</b>: `{song_info_global['duration']}s`\n"
                    f"> • <b>Requested by</b>: [{user.get('name', 'User')}](tg://user?id={user_id})\n"
                    f"> \n"
                    f"> 🎧 _Playing on {success_count} userbot(s) in Voice Chats!_"
                )
                sent_msg = None
                try:
                    sent_msg = await event.reply(caption, file=song_info_global["thumb"])
                except Exception:
                    try:
                        sent_msg = await event.reply(caption)
                    except Exception:
                        pass
                
                if sent_msg:
                    async def auto_delete():
                        await asyncio.sleep(song_info_global["duration"])
                        try:
                            await client.delete_messages(event.chat_id, sent_msg.id)
                        except Exception:
                            pass
                        file_path = song_info_global.get("file_path")
                        if file_path and os.path.exists(file_path) and "silence.mp3" not in file_path:
                            try:
                                os.remove(file_path)
                                logger.info(f"Deleted local song file: {file_path}")
                            except Exception as e:
                                logger.warning(f"Could not delete local file {file_path}: {e}")
                    asyncio.create_task(auto_delete())
                    
                flash = f"<blockquote><b>» ✅ Playing Song:</b> {song_info_global['title']}</blockquote>"
            else:
                flash = "<blockquote><b>» ❌ Failed To Play Song On Any Userbot.</b></blockquote>"
                
            await show_all_slots_dashboard(event, user_id, flash_message=flash)
            return

        # --- All Slots Group / Channel Actions ---
        elif action in ("WAITING_FOR_ALL_VC_GRP_LINK", "WAITING_FOR_ALL_VC_MULTI_GRP_LINK"):
            raw_text = (getattr(event, "raw_text", None) or event.text or "").strip()
            raw_text = re.sub(r'<[^>]+>', '', raw_text).strip()
            if not raw_text:
                await event.reply("<blockquote><b>» ❌ Group Invite Link Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
                
            strip_chars = "\'\"`()[]{}<> \t\n\r"
            links = [re.sub(r'<[^>]+>', '', x).strip().strip(strip_chars) for x in re.split(r'[,;\n\r\t]+', raw_text) if x.strip()]
            links = [x for x in links if x]
            if not links:
                await event.reply("<blockquote><b>» ❌ No Valid Links Provided.</b></blockquote>", parse_mode="html")
                return
                
            sessions = get_effective_sessions(user_id)
            if not sessions:
                await event.reply("<blockquote><b>» ❌ No Userbot Sessions Found.</b></blockquote>", parse_mode="html")
                return
                
            # Auto-start stopped bots if needed
            for s in sessions:
                if not userbot_manager.is_bot_running(s["phone"]):
                    try:
                        await userbot_manager.start_userbot(s["phone"], user_id)
                    except Exception as start_err:
                        logger.warning(f"Could not auto-start {s['phone']}: {start_err}")
                        
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            if not running_phones:
                await event.reply("<blockquote><b>» ❌ None Of The Userbots Are Running.</b></blockquote>", parse_mode="html")
                return
                
            progress_msg = await event.reply(
                f"⏳ <b>Joining {len(links)} Group(s) Across {len(running_phones)} Userbot(s)...</b>",
                parse_mode="html"
            )
            
            total_joins_success = 0
            total_joins_failed = 0
            total_flood_waits = 0
            total_wait_time = 0
            
            for p_idx, phone_num in enumerate(running_phones, 1):
                bot_obj = userbot_manager.find_running_bot(phone_num)
                if not bot_obj:
                    continue
                # Each userbot joins the groups on its own. Flood limits are
                # respected per account and the remaining groups are joined
                # automatically after the wait.
                async def _on_progress(done, total, state, _phone=phone_num):
                    try:
                        await progress_msg.edit(
                            f"⏳ <b>Joining Groups...</b> <code>({p_idx}/{len(running_phones)} bots)</code>\n\n"
                            f"🤖 <b>Account :</b> <code>{_phone}</code>\n"
                            f"✅ <b>Joined :</b> {total_joins_success}\n"
                            f"❌ <b>Failed :</b> {total_joins_failed}\n"
                            f"⏳ <b>Remaining :</b> {state.get('remaining', 0)}\n"
                            f"⏸️ <b>Flood waits :</b> {total_flood_waits}"
                            + (f" (<code>{total_wait_time}s</code> waited)" if total_flood_waits else ""),
                            parse_mode="html"
                        )
                    except Exception:
                        pass

                result = await joiner.join_many(
                    bot_obj.client, links,
                    progress_msg=progress_msg,
                    progress_callback=_on_progress,
                )
                total_joins_success += result.get("joined", 0)
                total_joins_failed += result.get("failed", 0)
                total_flood_waits += result.get("flood_waits", 0)
                total_wait_time += result.get("wait_total", 0)
                bot_obj.groups_cache_time = 0
                
            try:
                await progress_msg.delete()
            except Exception:
                pass
                
            remaining_groups = max(0, len(links) * len(running_phones) - total_joins_success - total_joins_failed)
            flash = (
                f"<blockquote><b>» 📚 All Slots : Group Joining Completed</b>\n\n"
                f"• <b>Total Userbots :</b> <b>{len(running_phones)}</b>\n"
                f"• <b>Total Group Links :</b> <b>{len(links)}</b>\n"
                f"• <b>✅ Successful Joins :</b> <b>{total_joins_success}</b>\n"
                f"• <b>❌ Failed Joins :</b> <b>{total_joins_failed}</b>"
                + (f"\n• <b>⏸️ Flood Waits :</b> <b>{total_flood_waits}</b> (<b>{total_wait_time}s</b> waited)" if total_flood_waits else "")
                + (f"\n• <b>⏳ Remaining :</b> <b>{remaining_groups}</b>" if remaining_groups else "")
                + "</blockquote>"
            )
            await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
            return

        elif action == "WAITING_FOR_ALL_LEAVE_GRP":
            raw_text = event.text.strip()
            if not raw_text:
                await event.reply("<blockquote><b>» ❌ Input Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
            strip_chars = "\'\"`()[]{}<> \t\n\r"
            links = [x.strip().strip(strip_chars) for x in re.split(r'[,;\n\r\t]+', raw_text) if x.strip()]
            sessions = get_effective_sessions(user_id)
            running_phones = [s["phone"] for s in sessions if userbot_manager.is_bot_running(s["phone"])]
            if not running_phones:
                await event.reply("<blockquote><b>» ❌ No Running Userbots Found.</b></blockquote>", parse_mode="html")
                return
            progress_msg = await event.reply("⏳ <b>Leaving Group(s) Across All Userbots...</b>", parse_mode="html")
            left_count = 0
            for phone_num in running_phones:
                bot_obj = userbot_manager._running_bots.get(phone_num)
                if not bot_obj:
                    continue
                for link in links:
                    try:
                        ok = await leave_chat_single(bot_obj.client, link)
                        if ok:
                            left_count += 1
                    except Exception:
                        pass
            try:
                await progress_msg.delete()
            except Exception:
                pass
            flash = f"<blockquote><b>» ❌ All Slots : Leave Group(s) Completed</b>\n\n• <b>Successfully Left Instances :</b> <b>{left_count}</b></blockquote>"
            await show_all_slots_dashboard(event, user_id, flash_message=flash, fetch_all=is_system_all_mode(user_id))
            return
            
        # --- Handle Single Bot Actions ---
        if not phone:
            await event.reply("<blockquote><b>» ❌ Session Not Found.</b></blockquote>", parse_mode="html")
            return
            
        sess = database.get_session(phone)
        if not sess or not is_session_owner_or_admin(sess, user_id):
            await event.reply("<blockquote><b>» ❌ Session Error.</b></blockquote>", parse_mode="html")
            return
            
        # 1. Broadcast Message
        if action == "WAITING_FOR_BROADCAST":
            sess["settings"]["broadcast_msg"] = event.text
            utils.resolve_modes(sess["settings"])
            # Setting a broadcast message also enables the auto broadcast.
            sess["settings"]["auto_spam"] = True
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            flash = "<blockquote><b>» ✉️ Broadcast Message Updated Successfully!</b>\n\n<i>✅ Auto Broadcast enabled.</i></blockquote>"
            
        elif action == "WAITING_FOR_RUN_TIMER":
            raw_text = event.text.strip().lower()
            try:
                if raw_text.endswith("m"):
                    minutes = float(raw_text[:-1])
                    seconds = minutes * 60
                else:
                    hours = float(raw_text)
                    seconds = hours * 3600
                    
                if seconds == 0:
                    if "run_expiry" in sess:
                        del sess["run_expiry"]
                    database.save_session(sess)
                    flash = "<blockquote><b>» ✅ Run Timer Disabled!</b></blockquote>"
                else:
                    import time
                    expiry_time = time.time() + seconds
                    sess["run_expiry"] = expiry_time
                    database.save_session(sess)
                    flash = f"<blockquote><b>» ✅ Run Timer Set! Bot Will Stop After {raw_text}.</b></blockquote>"
            except ValueError:
                await event.reply("<blockquote><b>» ❌ Invalid Format. Please Send A Number (E.G., 2 For Hours, 30m For Minutes, 0 To Disable).</b></blockquote>", parse_mode="html")
                return
            
        # 2. Welcome Message
        elif action == "WAITING_FOR_WELCOME":
            sess["settings"]["welcome_msg"] = event.text
            utils.resolve_modes(sess["settings"])
            # Setting a welcome message also enables the auto welcome.
            sess["settings"]["auto_welcome"] = True
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            flash = "<blockquote><b>» 👋 Welcome Message Updated Successfully!</b>\n\n<i>✅ Auto Welcome enabled.</i></blockquote>"
            
        # 2.b Multiple Welcome Messages
        elif action == "WAITING_FOR_MULTI_WELCOME":
            msgs = [x.strip() for x in event.text.split("|") if x.strip()]
            if not msgs:
                await event.reply("<blockquote><b>» ❌ Input Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
            sess["settings"]["welcome_messages"] = msgs
            # The mode follows the data - no manual switch needed.
            utils.resolve_modes(sess["settings"])
            # Switch the runtime into "multiple" mode.
            sess["settings"]["welcome_mode"] = "multiple"
            sess["settings"]["auto_welcome"] = True
            database.save_session(sess)
            userbot_manager.reload_bot_settings(phone)
            flash = "<blockquote><b>» 👋 Multiple Welcome Messages Updated Successfully!</b>\n\n<i>✅ Multiple (Rotational) mode + Auto Welcome enabled.</i></blockquote>"
            
        # 2.5 Join VC Link
        elif action == "WAITING_FOR_VC_LINK":
            link = event.text.strip()
            if not link:
                await event.reply("<blockquote><b>» ❌ Chat Id/Username/Link Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
                
            if not userbot_manager.is_bot_running(phone):
                await event.reply("<blockquote><b>» ❌ Userbot Is Not Running. Please Start It First.</b></blockquote>", parse_mode="html")
                return
                
            progress_msg = await event.reply("⏳ <b>Joining Voice Chat, Please Wait...</b>", parse_mode="html")
            bot_obj = userbot_manager.find_running_bot(phone)
            success, msg = await bot_obj.join_voice_chat(link)
            try:
                await progress_msg.delete()
            except Exception:
                pass
            
            if success:
                flash = f"<blockquote><b>» ✅ Joined Voice Chat!</b>\n{msg}</blockquote>"
            else:
                flash = f"<blockquote><b>» ❌ Failed To Join Vc:</b> {msg}</blockquote>"

        # 2.6 Join Group via Link or Multiple Links (Single Bot)
        elif action in ("WAITING_FOR_VC_GRP_LINK", "WAITING_FOR_VC_MULTI_GRP_LINK"):
            raw_text = (getattr(event, "raw_text", None) or event.text or "").strip()
            raw_text = re.sub(r'<[^>]+>', '', raw_text).strip()
            if not raw_text:
                await event.reply("<blockquote><b>» ❌ Group Invite Link Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
                
            if not userbot_manager.is_bot_running(phone):
                await event.reply("<blockquote><b>» ❌ Userbot Is Not Running.</b></blockquote>", parse_mode="html")
                return
                
            strip_chars = "\'\"`()[]{}<> \t\n\r"
            links = [re.sub(r'<[^>]+>', '', x).strip().strip(strip_chars) for x in re.split(r'[,;\n\r\t]+', raw_text) if x.strip()]
            links = [x for x in links if x]
            if not links:
                await event.reply("<blockquote><b>» ❌ No Valid Links Provided.</b></blockquote>", parse_mode="html")
                return
                
            bot_obj = userbot_manager.find_running_bot(phone)
            
            if len(links) == 1:
                link = links[0]
                progress_msg = await event.reply("⏳ <b>Joining Group, Please Wait...</b>", parse_mode="html")
                success = await join_channel_single(bot_obj.client, link)
                try:
                    await progress_msg.delete()
                except Exception:
                    pass
                if success:
                    bot_obj.groups_cache_time = 0
                    flash = f"<blockquote><b>» ✅ Successfully Joined Group!</b>\n\n• <b>Target :</b> <code>{link}</code>\n• <i>You Can Now Click '🎙️ Join Vc' To Enter The Voice Chat.</i></blockquote>"
                else:
                    flash = f"<blockquote><b>» ❌ Failed To Join Group</b>\n\n• <b>Target :</b> <code>{link}</code>\n• <i>Make Sure The Link Is Valid Or Not Expired.</i></blockquote>"
            else:
                progress_msg = await event.reply(
                    f"⏳ <b>Joining {len(links)} Groups...</b> <code>(0/{len(links)})</code>",
                    parse_mode="html"
                )
                # Flood limits are respected: the joiner waits and continues
                # with the remaining groups automatically.
                result = await joiner.join_many(bot_obj.client, links, progress_msg=progress_msg)
                bot_obj.groups_cache_time = 0
                try:
                    await progress_msg.delete()
                except Exception:
                    pass

                flash = (
                    f"<blockquote>{joiner.format_result(result, 'Multiple Groups Join Completed')}</blockquote>"
                )
                
            await show_bot_dashboard(event, phone, user_id, flash_message=flash)
            return

        # 2.7 Leave Group via Link/ID (Single Bot)
        elif action == "WAITING_FOR_LEAVE_GRP":
            raw_text = (getattr(event, "raw_text", None) or event.text or "").strip()
            raw_text = re.sub(r'<[^>]+>', '', raw_text).strip()
            if not raw_text:
                await event.reply("<blockquote><b>» ❌ Input Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
                
            if not userbot_manager.is_bot_running(phone):
                await event.reply("<blockquote><b>» ❌ Userbot Is Not Running.</b></blockquote>", parse_mode="html")
                return
                
            strip_chars = "\'\"`()[]{}<> \t\n\r"
            links = [re.sub(r'<[^>]+>', '', x).strip().strip(strip_chars) for x in re.split(r'[,;\n\r\t]+', raw_text) if x.strip()]
            links = [x for x in links if x]
            if not links:
                await event.reply("<blockquote><b>» ❌ No Valid Links Provided.</b></blockquote>", parse_mode="html")
                return
                
            progress_msg = await event.reply("⏳ <b>Leaving Group/Channel, Please Wait...</b>", parse_mode="html")
            bot_obj = userbot_manager.find_running_bot(phone)
            left_cnt = 0
            for lk in links:
                try:
                    if await leave_chat_single(bot_obj.client, lk):
                        left_cnt += 1
                except Exception:
                    pass
            try:
                await progress_msg.delete()
            except Exception:
                pass
            
            if left_cnt > 0:
                flash = f"<blockquote><b>» ✅ Successfully Left {left_cnt} Group(s)!</b></blockquote>"
            else:
                flash = "<blockquote><b>» ❌ Failed To Leave Group(s). Check Link/Id.</b></blockquote>"
                
            await show_bot_dashboard(event, phone, user_id, flash_message=flash)
            return
                
        # 3. Clone Profile
        elif action == "WAITING_FOR_CLONE_TARGET":
            target_entity = None
            raw_text = (getattr(event, "raw_text", None) or event.text or "").strip()
            
            # Check if user forwarded a message from the target user
            if getattr(event.message, "forward", None):
                try:
                    fwd_sender = await event.message.forward.get_sender()
                    if fwd_sender:
                        target_entity = fwd_sender
                        if getattr(fwd_sender, "username", None):
                            target = f"@{fwd_sender.username}"
                        else:
                            target = str(fwd_sender.id)
                except Exception as fwd_err:
                    logger.warning(f"Could not get sender from forward: {fwd_err}")
                    
            if not target_entity:
                target = raw_text
                
            if not target and not target_entity:
                await event.reply("<blockquote><b>» ❌ Target Cannot Be Empty. Please Enter A Valid Username, User ID, Link, Or Forward A Message.</b></blockquote>", parse_mode="html")
                return
                
            clone_type = state.get("clone_type", "complete")
            clone_label = {
                "name": "Name",
                "bio": "Bio",
                "photo": "Photo",
                "complete": "Profile Details"
            }.get(clone_type, "Profile")
            progress_msg = await event.reply(f"<blockquote><b>» ⏳ Cloning {clone_label}, Please Wait...</b></blockquote>", parse_mode="html")
            success, msg = await userbot_manager.clone_profile(
                phone, 
                target, 
                clone_type=clone_type, 
                fallback_client=client, 
                target_entity=target_entity
            )
            try:
                await progress_msg.delete()
            except Exception:
                pass
            
            if success:
                flash = f"<blockquote><b>» ✅ Profile Successfully Cloned!</b>\n{msg}</blockquote>"
            else:
                flash = f"<blockquote><b>» ❌ Cloning Failed:</b>\n{msg}</blockquote>"

        # 4. Change Name
        elif action == "WAITING_FOR_NAME":
            new_name = event.text.strip()
            if not new_name:
                await event.reply("<blockquote><b>» ❌ Name Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return
            progress_msg = await event.reply("<blockquote><b>» ⏳ Updating Account Name...</b></blockquote>", parse_mode="html")
            success, msg = await userbot_manager.set_userbot_name(phone, new_name)
            try:
                await progress_msg.delete()
            except Exception:
                pass
            if success:
                flash = f"<blockquote><b>» ✏️ {msg}</b></blockquote>"
            else:
                flash = f"<blockquote><b>» ❌ {msg}</b></blockquote>"
                
        # 5. Custom Interval
        elif action == "WAITING_FOR_CUSTOM_INTERVAL":
            val_str = event.text.strip()
            if val_str.isdigit() and int(val_str) >= 60:
                val = int(val_str)
                sess["settings"]["broadcast_interval"] = val
                database.save_session(sess)
                flash = f"<blockquote><b>» ⏱️ Interval Updated To {val}s</b></blockquote>"
            else:
                await event.reply(utils.get_text("interval_invalid", lang))
                return

        # 5.5 Custom Delay
        elif action == "WAITING_FOR_CUSTOM_DELAY":
            val_str = event.text.strip()
            if val_str.isdigit() and 2 <= int(val_str) <= 300:
                val = int(val_str)
                sess.setdefault("settings", {})["inter_group_delay"] = val
                database.save_session(sess)
                flash = f"<blockquote><b>» ⏱️ Inter-Group Delay Updated To {val}s</b></blockquote>"
            else:
                await event.reply(utils.get_text("inter_delay_invalid", lang))
                return

        # 5.6 Multiple Messages
        elif action == "WAITING_FOR_MULTI_MSG":
            raw_text = event.text
            msgs = [m.strip() for m in raw_text.split(",") if m.strip()]
            if msgs:
                sess.setdefault("settings", {})["broadcast_messages"] = msgs
                # The mode follows the data - no manual switch needed.
                utils.resolve_modes(sess.setdefault("settings", {}))
                # Switch the runtime into "multiple" mode.
                sess["settings"]["broadcast_mode"] = "multiple"
                sess["settings"]["auto_spam"] = True
                database.save_session(sess)
                userbot_manager.reload_bot_settings(phone)
                flash = f"<blockquote><b>» ✅ Successfully Set {len(msgs)} Messages For Randomized Broadcast!</b>\n\n<i>✅ Multiple (Rotational) mode + Auto Broadcast enabled.</i></blockquote>"
            else:
                await event.reply("<blockquote><b>» ❌ Message List Cannot Be Empty. Separate With Commas (,).</b></blockquote>", parse_mode="html")
                return

        # 5.6.4 Single Auto Reply Message
        elif action == "WAITING_FOR_AUTO_REPLY_SINGLE":
            msg_text = event.text.strip()
            if msg_text:
                sess.setdefault("settings", {})["auto_reply_msg"] = msg_text
                sess["settings"]["auto_reply"] = True
                sess["settings"]["auto_reply_mode"] = "single"
                database.save_session(sess)
                userbot_manager.reload_bot_settings(phone)
                userbot_manager.reload_bot_settings(sess.get("session_id", phone))
                flash = "<blockquote><b>» 💬 Single Tag Auto-Reply Message Updated!</b></blockquote>"
            else:
                await event.reply("<blockquote><b>» ❌ Message Cannot Be Empty.</b></blockquote>", parse_mode="html")
                return

        # 5.6.5 Auto Reply Messages
        elif action == "WAITING_FOR_AUTO_REPLY_MSGS":
            raw_text = event.text
            if "\n" in raw_text and "," not in raw_text:
                raw_msgs = raw_text.split("\n")
            elif "|" in raw_text and "," not in raw_text:
                raw_msgs = raw_text.split("|")
            else:
                raw_msgs = raw_text.split(",")
            msgs = [m.strip() for m in raw_msgs if m.strip()]
            if msgs:
                sess.setdefault("settings", {})["auto_reply_messages"] = msgs
                # The mode follows the data - no manual switch needed.
                utils.resolve_modes(sess.setdefault("settings", {}))
                sess["settings"]["auto_reply"] = True  # Auto-enable when messages are set
                sess["settings"]["auto_reply_mode"] = "multiple"
                database.save_session(sess)
                userbot_manager.reload_bot_settings(phone)
                userbot_manager.reload_bot_settings(sess.get("session_id", phone))
                flash = f"<blockquote><b>» ✅ Tag Auto-Reply Messages Updated ({len(msgs)} Msgs)!</b></blockquote>"
            else:
                await event.reply(utils.get_text("auto_reply_invalid", lang))
                return
 
        # 5.7 Play Song query
        elif action == "WAITING_FOR_SONG":
            # Extract media info using extract_media_info
            media_obj, audio_title, audio_duration = extract_media_info(event.message)
            is_audio_file = False
            local_file_path = None
            
            if media_obj:
                is_audio_file = True
                progress_msg = await event.reply("📥 <b>Downloading uploaded media...</b>\n━━━━━━━━━━━━━━━━━━━━\n📊 Progress: `[░░░░░░░░░░] 0.0%`")
                os.makedirs("downloads", exist_ok=True)
                try:
                    local_file_path = await client.download_media(
                        event.message, 
                        file="downloads/",
                        progress_callback=lambda c, t: download_progress_sync(c, t, progress_msg, "Downloading uploaded media")
                    )
                except Exception as dl_err:
                    logger.error(f"Failed to download media: {dl_err}")
                    await progress_msg.edit(f"❌ <b>Failed to download media:</b> {dl_err}")
                    return
                finally:
                    try:
                        await progress_msg.delete()
                    except Exception:
                        pass
            
            query = None
            if not is_audio_file:
                query = event.text.strip() if event.text else ""
                if not query:
                    await event.reply("<blockquote><b>» ❌ Please Provide A Song Query Or Send An Audio File.</b></blockquote>", parse_mode="html")
                    return
                
            if not userbot_manager.is_bot_running(phone):
                await event.reply("<blockquote><b>» ❌ Userbot Is Not Running.</b></blockquote>", parse_mode="html")
                return
                
            bot_obj = userbot_manager.find_running_bot(phone)
            progress_msg = await event.reply("⏳ <b>Playing Song, Please Wait...</b>", parse_mode="html")
            success, msg, song_info = await bot_obj.play_song(query, play_type="audio", local_file=local_file_path, title=audio_title, duration=audio_duration)
            try:
                await progress_msg.delete()
            except Exception:
                pass
            
            if success and song_info:
                caption = (
                    f"> 🎵 <b>Now Playing</b>\n"
                    f"> \n"
                    f"> • <b>Title</b>: `{song_info['title']}`\n"
                    f"> • <b>Duration</b>: `{song_info['duration']}s`\n"
                    f"> • <b>Requested by</b>: [{user.get('name', 'User')}](tg://user?id={user_id})\n"
                    f"> \n"
                    f"> 🎧 _Playing in voice chat for userbot `{phone}`_"
                )
                sent_msg = None
                try:
                    sent_msg = await event.reply(caption, file=song_info["thumb"])
                except Exception:
                    try:
                        sent_msg = await event.reply(caption)
                    except Exception:
                        pass
                
                if sent_msg:
                    async def auto_delete():
                        await asyncio.sleep(song_info["duration"])
                        try:
                            await client.delete_messages(event.chat_id, sent_msg.id)
                        except Exception:
                            pass
                        file_path = song_info.get("file_path")
                        if file_path and os.path.exists(file_path) and "silence.mp3" not in file_path:
                            try:
                                os.remove(file_path)
                                logger.info(f"Deleted local song file: {file_path}")
                            except Exception as e:
                                logger.warning(f"Could not delete local file {file_path}: {e}")
                    asyncio.create_task(auto_delete())
                    
                flash = f"<blockquote><b>» ✅ Playing Song:</b> {song_info['title']}</blockquote>"
            else:
                flash = f"<blockquote><b>» ❌ Failed To Play:</b> {msg}</blockquote>"
                
        else:
            logger.warning(f"Unhandled action in text_input_handler: {action}")
            flash = "<blockquote><b>» ⚠️ Unknown Or Expired Action.</b></blockquote>"

        # Return to dashboard showing updated stats and flash notification
        userbot_manager.reload_bot_settings(phone)
        await show_bot_dashboard(event, phone, user_id, flash_message=flash)
