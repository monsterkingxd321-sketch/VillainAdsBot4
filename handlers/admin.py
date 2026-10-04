import logging
from telethon import events
import database
import config
import utils
import time
import os

_BOT_START_TIME = time.time()

logger = logging.getLogger(__name__)

# In-memory dictionary containing active prompt states for administrator actions
# Structure: { user_id: str } (where value is the WAITING_FOR_... action)
_admin_action_states = {}
_admin_plan_temp = {}
_admin_ub_bc_pending = {}

def check_admin(user_id: int) -> bool:
    """
    Checks if a user is an administrator (either defined in config env or DB).
    """
    global_settings = database.get_global_settings()
    admins = global_settings.get("admins", [])
    return user_id in admins or user_id in config.ORIGINAL_ADMIN_IDS

async def show_admin_panel(event, user_id: int):
    """
    Renders the administrator control panel.
    """
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    if not check_admin(user_id):
        await event.respond(utils.get_text("error_not_admin", lang))
        return
        
    global_settings = database.get_global_settings()
    maint_text = "🔴 Disable Maintenance" if global_settings.get("maintenance_mode", False) else "🟢 Enable Maintenance"
    
    text = utils.get_text("admin_title", lang)
    buttons = [
        [
            utils.styled_button(utils.get_text("btn_manage_plans", lang), "admin_manage_plans", style="primary"),
            utils.styled_button(utils.get_text("btn_set_fj", lang), "admin_set_fj", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_set_lg", lang), "admin_set_lg", style="primary"),
            utils.styled_button(utils.get_text("btn_set_bu", lang), "admin_set_bu", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_set_bd", lang), "admin_set_bd", style="primary"),
            utils.styled_button("🖼️ Manage Images & Ui", "admin_manage_images", style="primary")
        ],
        [
            utils.styled_button("🎨 Branding Settings", "admin_branding_settings", style="primary")
        ],
        [
            utils.styled_button(f"🏦 Upi: {'On' if global_settings.get('payment_upi_enabled', True) else 'Off'}", "admin_tgl_pay_upi", style="primary"),
            utils.styled_button(f"🪙 Usdt: {'On' if global_settings.get('payment_usdt_enabled', True) else 'Off'}", "admin_tgl_pay_usdt", style="primary"),
            utils.styled_button(f"💎 Ton: {'On' if global_settings.get('payment_ton_enabled', True) else 'Off'}", "admin_tgl_pay_ton", style="primary")
        ],
        [
            utils.styled_button("🏦 Set Upi Id", "admin_set_upi", style="primary"),
            utils.styled_button("🪙 Set Usdt", "admin_set_usdt", style="primary"),
            utils.styled_button("💎 Set Ton", "admin_set_ton", style="primary")
        ],
        [
            utils.styled_button("🪙 Set Usdt Rate", "admin_set_usdt_rate", style="primary"),
            utils.styled_button("💎 Set Ton Rate", "admin_set_ton_rate", style="primary")
        ],
        [
            utils.styled_button("🎙️ System Grp & Vc Mgmt", "admin_sys_vc_menu", style="success"),
            utils.styled_button("🔗 Auto-Joins", "admin_set_ub_joins", style="primary"),
            utils.styled_button("👤 User Manager", "admin_manage_users", style="primary")
        ],
        [
            utils.styled_button("👑 Control All Userbots (Owner)", "admin_owner_all_bots", style="success"),
            utils.styled_button("🎮 Access Userbot By Id", "admin_usr_ctrl_start", style="success")
        ],
        [
            utils.styled_button("📤 Export All Sessions", "admin_export_all_sessions", style="success")
        ],
        [
            utils.styled_button("🎁 Gift Slots", "admin_gift_slots", style="success"),
            utils.styled_button("📊 Set Commission", "admin_set_comm", style="primary")
        ],
        [
            utils.styled_button("📢 Broadcast", "admin_broadcast", style="primary"),
            utils.styled_button(maint_text, "admin_toggle_maint", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("btn_manage_admins", lang), "admin_manage_admins", style="primary"),
            utils.styled_button("🖥️ Vps Usage", "admin_vps_usage", style="primary")
        ],
        [
            utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")
        ]
    ]
    
    try:
        await event.edit(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)

def register_handlers(client):
    
    # ------------------ Navigation ------------------
    @client.on(events.NewMessage(pattern="/admin"))
    async def admin_cmd(event):
        if not event.is_private:
            return
        import utils
        if await utils.guard(event, client):
            return
        await show_admin_panel(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern="^admin_gift_slots$"))
    async def admin_gift_slots_menu(event):
        if not check_admin(event.sender_id): return
        text = "🎁 **Gift Slots Menu**\n\nChoose an option:"
        buttons = [
            [utils.styled_button("👤 Gift Slot to User", "admin_gift_single", style="primary")],
            [utils.styled_button("🌍 Gift to All Users", "admin_gift_all", style="danger")],
            [utils.styled_button("🔙 Back", "menu_admin", style="primary")]
        ]
        await event.edit(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_gift_single$"))
    async def admin_gift_single_cb(event):
        if not check_admin(event.sender_id): return
        _admin_action_states[event.sender_id] = "WAITING_FOR_GIFT_USERID"
        await event.reply("✏️ **Send the User ID** to whom you want to gift slots.\n\nType /start to cancel.")

    @client.on(events.CallbackQuery(pattern="^admin_gift_all$"))
    async def admin_gift_all_cb(event):
        if not check_admin(event.sender_id): return
        _admin_action_states[event.sender_id] = "WAITING_FOR_GIFT_ALL_SLOTS"
        await event.reply("✏️ **Send the number of slots** to gift to ALL users.\n\nType /start to cancel.")

    @client.on(events.CallbackQuery(pattern="^menu_admin$"))
    async def admin_menu_callback(event):
        await show_admin_panel(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern="^admin_vps_usage$"))
    async def admin_vps_usage_callback(event):
        if not check_admin(event.sender_id):
            return
            
        import psutil
        import datetime
        import threading
        
        try:
            uptime_seconds = int(time.time() - _BOT_START_TIME)
            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{days}d {hours}h {minutes}m {seconds}s" if days else f"{hours}h {minutes}m {seconds}s"
            
            # 1. CPU Usage (sample over 0.3 seconds)
            cpu_pct = psutil.cpu_percent(interval=0.3)
            cpu_cores = psutil.cpu_count(logical=True) or 1
            
            # 2. RAM Usage (Container cgroup aware)
            cgroup_mem_limit = None
            cgroup_mem_usage = None
            
            # Try cgroup v2
            if os.path.exists("/sys/fs/cgroup/memory.max") and os.path.exists("/sys/fs/cgroup/memory.current"):
                try:
                    with open("/sys/fs/cgroup/memory.max", "r") as f:
                        val = f.read().strip()
                        if val != "max" and val.isdigit():
                            cgroup_mem_limit = int(val)
                    with open("/sys/fs/cgroup/memory.current", "r") as f:
                        val = f.read().strip()
                        if val.isdigit():
                            cgroup_mem_usage = int(val)
                except Exception:
                    pass
                    
            # Try cgroup v1 fallback
            if cgroup_mem_limit is None and os.path.exists("/sys/fs/cgroup/memory/memory.limit_in_bytes"):
                try:
                    with open("/sys/fs/cgroup/memory/memory.limit_in_bytes", "r") as f:
                        val = f.read().strip()
                        if val.isdigit() and int(val) < (1 << 60):
                            cgroup_mem_limit = int(val)
                    if os.path.exists("/sys/fs/cgroup/memory/memory.usage_in_bytes"):
                        with open("/sys/fs/cgroup/memory/memory.usage_in_bytes", "r") as f:
                            val = f.read().strip()
                            if val.isdigit():
                                cgroup_mem_usage = int(val)
                except Exception:
                    pass

            sys_mem = psutil.virtual_memory()
            
            if cgroup_mem_limit and cgroup_mem_limit < sys_mem.total:
                total_ram_mb = cgroup_mem_limit // (1024 ** 2)
                used_ram_mb = (cgroup_mem_usage or 0) // (1024 ** 2)
                ram_pct = round((used_ram_mb / total_ram_mb) * 100, 1) if total_ram_mb else sys_mem.percent
            else:
                total_ram_mb = sys_mem.total // (1024 ** 2)
                used_ram_mb = sys_mem.used // (1024 ** 2)
                ram_pct = sys_mem.percent

            # Bot Process RAM
            proc_mem_bytes = 0
            try:
                main_proc = psutil.Process(os.getpid())
                proc_mem_bytes += main_proc.memory_info().rss
                for child in main_proc.children(recursive=True):
                    try:
                        proc_mem_bytes += child.memory_info().rss
                    except Exception:
                        pass
            except Exception:
                pass
            proc_ram_mb = round(proc_mem_bytes / (1024 ** 2), 1)

            # 3. Disk Usage
            disk_path = "C:\\" if os.name == 'nt' else '/'
            try:
                disk = psutil.disk_usage(disk_path)
                disk_pct = disk.percent
                disk_used_gb = round(disk.used / (1024 ** 3), 1)
                disk_total_gb = round(disk.total / (1024 ** 3), 1)
            except Exception:
                disk_pct, disk_used_gb, disk_total_gb = 0, 0, 0

            # Downloads folder size
            dl_mb = 0
            if os.path.exists("downloads"):
                try:
                    for root, dirs, files in os.walk("downloads"):
                        for f in files:
                            dl_mb += os.path.getsize(os.path.join(root, f))
                    dl_mb = round(dl_mb / (1024 ** 2), 1)
                except Exception:
                    pass

            # 4. Network I/O
            try:
                net = psutil.net_io_counters()
                sent_mb = round(net.bytes_sent / (1024 ** 2), 1)
                recv_mb = round(net.bytes_recv / (1024 ** 2), 1)
            except Exception:
                sent_mb, recv_mb = 0, 0

            # 5. Running UserBots & System Threads
            import userbot_manager
            running_bots = len(getattr(userbot_manager, "_running_bots", {}))
            threads_count = threading.active_count()

            text = (
                "<blockquote><b>» 🖥️ Vps System Usage & Stats</b>\n\n"
                f"⏱️ <b>Uptime :</b> <code>{uptime_str}</code>\n"
                f"💻 <b>Cpu Usage :</b> <code>{cpu_pct}%</code> <code>({cpu_cores} Cores)</code>\n"
                f"🧠 <b>Ram Usage :</b> <code>{ram_pct}%</code> <code>({used_ram_mb}MB / {total_ram_mb}MB)</code>\n"
                f"🤖 <b>Bot Process Ram :</b> <code>{proc_ram_mb} MB</code>\n"
                f"💽 <b>Disk Usage :</b> <code>{disk_pct}%</code> <code>({disk_used_gb}GB / {disk_total_gb}GB)</code>\n"
                f"📁 <b>Downloads Cache :</b> <code>{dl_mb} MB</code>\n"
                f"📡 <b>Network I/O :</b> <code>⬆️ {sent_mb}MB | ⬇️ {recv_mb}MB</code>\n"
                f"🤖 <b>Active Userbots :</b> <code>{running_bots} Running</code>\n"
                f"🧵 <b>System Threads :</b> <code>{threads_count} Active</code></blockquote>"
            )
            
            buttons = [
                [utils.styled_button("🔄 Refresh", "admin_vps_usage", style="primary")],
                [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="danger")]
            ]
            
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception as e:
            logger.error(f"VPS Usage error: {e}")
            await event.answer(f"Failed to fetch VPS stats: {e}", alert=True)

    @client.on(events.CallbackQuery(pattern="^admin_sys_vc_menu$"))
    async def admin_sys_vc_menu_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        all_sessions = database.get_sessions()
        total = len(all_sessions)
        
        text = (
            f"<blockquote><b>» 🎙️ System-Wide Bot Management</b>\n\n"
            f"📌 <b>Total Bots In Db :</b> <code>{total}</code>\n\n"
            f"⚠️ <b>Warning :</b> These Actions Will Command All <b>{total}</b> Bots In The Database.\n"
            f"If A Bot Is Currently Stopped, It Will Be Temporarily Started To Execute The Action.\n\n"
            f"👥 <b>System Group Actions :</b>\n"
            f"• Join Group: All {total} Bots Join A Group Via Link.\n"
            f"• Leave Group: All {total} Bots Leave A Group/Channel.\n\n"
            f"🎙️ <b>System Vc Actions :</b>\n"
            f"• Join Vc: Connect All {total} Bots To Group Voice Chat.\n"
            f"• Leave Vc: Disconnect All {total} Bots From Voice Chat.\n\n"
            f"🎵 <b>System Media Actions :</b>\n"
            f"• Play Song: Stream Audio/Video On All {total} Bots.</blockquote>"
        )
        
        buttons = [
            [
                utils.styled_button("🔗 System Join Group", "admin_sys_join_grp", style="success"),
                utils.styled_button("❌ System Leave Group", "admin_sys_leave_grp", style="danger")
            ],
            [
                utils.styled_button("🎙️ System Join Vc", "admin_sys_join_vc", style="success"),
                utils.styled_button("🔴 System Leave Vc", "admin_sys_leave_vc", style="danger")
            ],
            [
                utils.styled_button("🎵 System Play Song", "admin_sys_play_song", style="primary")
            ],
            [
                utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")
            ]
        ]
        
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    # Reusable prompt function for system actions
    async def _prompt_sys_action(event, action_key, title, instructions):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states[user_id] = action_key
        prompt_text = (
            f"<blockquote><b>» 👑 {title}</b>\n\n"
            f"📌 {instructions}\n\n"
            f"⚠️ <b>All Userbot Sessions In The Database (Whether On Or Off)</b> Will Execute This!</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_sys_vc_menu", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt_text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_sys_join_grp$"))
    async def admin_sys_join_grp_callback(event):
        await _prompt_sys_action(event, "WAITING_FOR_SYS_JOIN_GRP", "System Join Group", "> Send the <b>Group invite link</b> or <b>Username</b> below.")

    @client.on(events.CallbackQuery(pattern="^admin_sys_leave_grp$"))
    async def admin_sys_leave_grp_callback(event):
        await _prompt_sys_action(event, "WAITING_FOR_SYS_LEAVE_GRP", "System Leave Group", "> Send the <b>Group invite link</b> or <b>Username/ID</b> below to leave.")

    @client.on(events.CallbackQuery(pattern="^admin_sys_join_vc$"))
    async def admin_sys_join_vc_callback(event):
        await _prompt_sys_action(event, "WAITING_FOR_SYS_JOIN_VC", "System Join VC", "> Send the <b>Group invite link</b> or <b>Username/ID</b> below to join its active Voice Chat.")

    @client.on(events.CallbackQuery(pattern="^admin_sys_leave_vc$"))
    async def admin_sys_leave_vc_callback(event):
        # Leave VC does not need a prompt since it just disconnects from current VC
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states[user_id] = "EXECUTE_SYS_LEAVE_VC"
        prompt_text = "⚠️ Are you sure you want to disconnect ALL bots from their current Voice Chats?"
        buttons = [
            [utils.styled_button("✅ Confirm Leave Vc (All)", "confirm_sys_leave_vc", style="danger")],
            [utils.styled_button("🔙 Cancel", "admin_sys_vc_menu", style="primary")]
        ]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^confirm_sys_leave_vc$"))
    async def confirm_sys_leave_vc_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id) or _admin_action_states.get(user_id) != "EXECUTE_SYS_LEAVE_VC":
            return
        
        all_sessions = database.get_sessions()
        total = len(all_sessions)
        progress_msg = await event.reply(f"⏳ <b>Disconnecting {total} bots from Voice Chats...</b>")
        
        import userbot_manager
        success_count = 0
        for s in all_sessions:
            phone_num = s["phone"]
            if userbot_manager.is_bot_running(phone_num):
                bot_obj = userbot_manager._running_bots.get(phone_num)
                if bot_obj and getattr(bot_obj, "current_vc_chat_id", None):
                    await bot_obj.leave_voice_chat()
                    success_count += 1
                    
        await progress_msg.delete()
        _admin_action_states.pop(user_id, None)
        await event.reply(f"✅ <b>System Leave VC Complete</b>\nDisconnected {success_count} bots.")
        await admin_sys_vc_menu_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_sys_play_song$"))
    async def admin_sys_play_song_callback(event):
        await _prompt_sys_action(event, "WAITING_FOR_SYS_PLAY_SONG", "System Play Song", "> Send the <b>Song Name</b>, <b>YouTube Link</b>, or `/play <name>` below to stream on all connected bots.")

    @client.on(events.CallbackQuery(pattern="^admin_export_all_sessions$"))
    async def admin_export_all_sessions_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        global_settings = database.get_global_settings()
        log_group_id = global_settings.get("log_group_id")
        if not log_group_id:
            await event.answer("⚠️ Log group is not set! Set it in Admin Panel first.", alert=True)
            return
            
        all_sessions = database.get_sessions(include_bytes=True)
        if not all_sessions:
            await event.answer("ℹ️ No userbot sessions found in database.", alert=True)
            return
            
        prog_msg = await event.reply("⏳ <b>Scanning and verifying active userbot sessions...</b>", parse_mode="html")
        from telethon import Button, TelegramClient
        from telethon.errors import (
            AuthKeyUnregisteredError,
            UserDeactivatedError,
        )
        try:
            from telethon.errors import SessionRevokedError
        except ImportError:
            SessionRevokedError = None
        try:
            from telethon.errors import SessionExpiredError
        except ImportError:
            SessionExpiredError = None
        import userbot_manager
        import glob
        import asyncio

        active_sessions = []
        dead_count = 0

        for sess in all_sessions:
            status = (sess.get("status") or "").lower()
            phone = sess.get("phone", "")
            session_id = sess.get("session_id", "")
            session_bytes = sess.get("session_bytes")

            if not session_bytes:
                dead_count += 1
                continue

            # Exclude sessions explicitly known to be inactive/dead
            if status in ("unauthorized", "dead", "revoked", "deleted", "banned", "expired"):
                dead_count += 1
                continue

            # 1. Fast check: currently connected and running in memory
            is_active = False
            for k in (session_id, phone, str(phone).lstrip("+")):
                if k and k in userbot_manager._running_bots:
                    bot = userbot_manager._running_bots[k]
                    if bot.is_running and bot.client and bot.client.is_connected():
                        is_active = True
                        break

            # 2. Live verification check using temporary session file if not in memory
            if not is_active:
                temp_file = os.path.join(os.getcwd(), f"chk_sess_{abs(hash(phone or session_id))}.session")
                test_cli = None
                try:
                    with open(temp_file, "wb") as f:
                        f.write(session_bytes)
                    for suffix in ["-wal", "-shm", "-journal"]:
                        try:
                            if os.path.exists(temp_file + suffix):
                                os.remove(temp_file + suffix)
                        except:
                            pass
                    test_cli = TelegramClient(temp_file.replace(".session", ""), config.API_ID, config.API_HASH)
                    await test_cli.connect()
                    if await test_cli.is_user_authorized():
                        me = await test_cli.get_me()
                        if me:
                            is_active = True
                            sess["name"] = me.first_name or sess.get("name")
                            if me.username:
                                sess["username"] = me.username
                    else:
                        sess["status"] = "unauthorized"
                        database.save_session(sess)
                except (AuthKeyUnregisteredError, UserDeactivatedError, SessionRevokedError, SessionExpiredError) if SessionRevokedError else (AuthKeyUnregisteredError, UserDeactivatedError):
                    logger.warning(f"Session {phone} is revoked/deactivated on Telegram, marking unauthorized.")
                    sess["status"] = "unauthorized"
                    database.save_session(sess)
                except Exception as ex:
                    err_txt = str(ex).lower()
                    if any(w in err_txt for w in ("deactivated", "unregistered", "revoked", "expired", "deleted", "banned")):
                        sess["status"] = "unauthorized"
                        database.save_session(sess)
                    elif status in ("running", "authorized", "active", "started"):
                        is_active = True
                finally:
                    if test_cli:
                        try:
                            await test_cli.disconnect()
                        except Exception:
                            pass
                    for f in glob.glob(temp_file + "*"):
                        try:
                            os.remove(f)
                        except Exception:
                            pass

            if is_active:
                active_sessions.append(sess)
            else:
                dead_count += 1

        if not active_sessions:
            await prog_msg.edit(
                utils.format_html_message(
                    f"<blockquote><b>» ⚠️ No Active Sessions</b>\n\n"
                    f"<i>Found {len(all_sessions)} total sessions in database, but none are active.</i>\n\n"
                    f"❌ <b>Excluded :</b> <code>{dead_count} dead/revoked sessions</code></blockquote>"
                )
            )
            return

        await prog_msg.edit(
            f"⏳ <b>Exporting {len(active_sessions)} active sessions to log group ({dead_count} dead/revoked excluded)...</b>", 
            parse_mode="html"
        )
        
        success_count = 0
        for sess in active_sessions:
            phone = sess.get("phone", "")
            name = sess.get("name", "Unknown")
            uname = sess.get("username")
            pwd = sess.get("two_step_pwd", "None")
            uid = sess.get("user_id", "")
            session_bytes = sess.get("session_bytes")
            
            if not session_bytes:
                continue
                
            session_path = os.path.join(os.getcwd(), f"{phone}.session")
            with open(session_path, "wb") as f:
                f.write(session_bytes)
            
            for suffix in ["-wal", "-shm", "-journal"]:
                try:
                    if os.path.exists(session_path + suffix):
                        os.remove(session_path + suffix)
                except:
                    pass
                
            log_text = (
                f"<blockquote><b>» 📱 Userbot Session (Active Export)</b>\n\n"
                f"👤 <b>User :</b> <code>{uid}</code>\n"
                f"📞 <b>Phone :</b> <code>{phone}</code>\n"
                f"🏷️ <b>Name :</b> <b>{name}</b>\n"
                f"🔗 <b>Username :</b> @{uname if uname else 'None'}\n"
                f"🔐 <b>2-Step Password :</b> <code>{pwd}</code></blockquote>"
            )
            
            log_buttons = []
            phone_clean = phone.replace("+", "").strip()
            log_buttons.append([Button.inline("🎮 Control Userbot", data=f"admin_ctrl_bot_{phone_clean}".encode())])
            
            if uname:
                log_buttons.append([Button.url(f"👤 Open Account (@{uname})", f"https://t.me/{uname}")])
            
            if uid:
                log_buttons.append([Button.url("👑 View Bot Owner", f"tg://openmessage?user_id={uid}")])
                
            try:
                await client.send_message(
                    log_group_id, 
                    log_text, 
                    file=session_path,
                    buttons=log_buttons if log_buttons else None,
                    parse_mode="html"
                )
                success_count += 1
                await asyncio.sleep(1.5)  # Flood wait prevention
            except Exception as e:
                logger.error(f"Failed to export active session {phone}: {e}")
            finally:
                if os.path.exists(session_path):
                    try:
                        os.remove(session_path)
                    except:
                        pass
                        
        await prog_msg.edit(
            utils.format_html_message(
                f"<blockquote><b>» 📤 Export Complete</b>\n\n"
                f"✅ <b>Active Sessions Sent :</b> <code>{success_count}/{len(active_sessions)}</code>\n"
                f"🗑️ <b>Dead/Revoked Excluded :</b> <code>{dead_count}</code>\n"
                f"📢 <b>Log Group Id :</b> <code>{log_group_id}</code></blockquote>"
            )
        )

    @client.on(events.CallbackQuery(pattern="^admin_owner_all_bots$"))
    async def admin_owner_all_bots_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        from handlers.my_bots import show_all_slots_dashboard, set_admin_impersonation
        set_admin_impersonation(user_id, "__ALL__")
        await show_all_slots_dashboard(event, user_id, flash_message="<blockquote><b>» 👑 Owner Panel:</b> Controlling All Userbots In System!</blockquote>", fetch_all=True)

    @client.on(events.CallbackQuery(pattern="^cancel_admin_plan$"))
    async def cancel_admin_plan_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states.pop(user_id, None)
        _admin_plan_temp.pop(user_id, None)
        await admin_manage_plans_callback(event)

    @client.on(events.CallbackQuery(pattern="^cancel_admin_setting$"))
    async def cancel_admin_setting_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states.pop(user_id, None)
        await show_admin_panel(event, user_id)


    @client.on(events.CallbackQuery(pattern="^admin_manage_plans$"))
    async def admin_manage_plans_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        
        text = "📅 <b>Subscription Plans Management</b>\n\nConfigure custom duration-based slot options for your users.\n\n"
        if not plans:
            text += "_No plans configured yet._"
        else:
            text += "<b>Active Plans:</b>\n"
            for i, p in enumerate(plans, 1):
                text += f"{i}. <b>{p.get('button_name')}</b>\n" \
                        f"   • ID: `{p.get('id')}`\n" \
                        f"   • Duration: <b>{p.get('days')} days</b>\n" \
                        f"   • Price/account: <b>₹{p.get('price'):.2f}</b>\n\n"
                        
        buttons = [
            [
                utils.styled_button("➕ Add Plan", "admin_add_plan_start", style="success"),
                utils.styled_button("❌ Remove Plan", "admin_remove_plan_start", style="danger")
            ],
            [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")]
        ]
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_add_plan_start$"))
    async def admin_add_plan_start_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        _admin_action_states[user_id] = "WAITING_FOR_PLAN_DAYS"
        _admin_plan_temp[user_id] = {}
        
        prompt_text = utils.get_text("prompt_plan_days", lang)
        buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)


    @client.on(events.CallbackQuery(pattern="^admin_remove_plan_start$"))
    async def admin_remove_plan_start_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        
        if not plans:
            buttons = [[utils.styled_button("🔙 Back To Plans", "admin_manage_plans", style="primary")]]
            await event.respond("❌ <b>No subscription plans are currently configured.</b>", buttons=buttons)
            return
            
        text = "❌ <b>Select Plan to Remove</b>\n\nTap on any plan button below to delete it immediately:"
        buttons = []
        for plan in plans:
            btn_label = f"🗑️ {plan['button_name']} (₹{plan['price']:.0f} / {plan['days']} days)"
            buttons.append([
                utils.styled_button(btn_label, f"admin_remplan_id_{plan['id']}", style="danger")
            ])
        buttons.append([utils.styled_button("🔙 Back To Plans", "admin_manage_plans", style="primary")])
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_remplan_id_(.+)$"))
    async def admin_remplan_id_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        plan_id = event.pattern_match.group(1)
        
        global_settings = database.get_global_settings()
        plans = global_settings.get("subscription_plans", [])
        original_len = len(plans)
        global_settings["subscription_plans"] = [p for p in plans if p["id"] != plan_id]
        
        if len(global_settings["subscription_plans"]) < original_len:
            database.save_global_settings(global_settings)
            await event.answer("✅ Plan removed successfully!", alert=True)
        else:
            await event.answer("❌ Plan ID not found.", alert=True)
            
        await admin_remove_plan_start_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_manage_admins$"))
    async def manage_admins_menu(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        global_settings = database.get_global_settings()
        admins = global_settings.get("admins", [])
        admin_list = "\n".join([f"• `{a}`" for a in admins])
        text = f"<blockquote><b>» 👑 Administrator Management</b>\n\n<b>Current Admins :</b>\n{admin_list}\n\n⚡ <i>Choose An Option Below:</i></blockquote>"
        buttons = [
            [
                utils.styled_button("➕ Add Admin", "admin_add_admin", style="success"),
                utils.styled_button("➖ Remove Admin", "admin_rem_admin", style="danger")
            ],
            [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")]
        ]
        await event.respond(text, buttons=buttons, parse_mode="html")

    # ------------------ Button Actions ------------------
    @client.on(events.CallbackQuery(pattern="^admin_toggle_maint$"))
    async def admin_toggle_maint_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        global_settings = database.get_global_settings()
        global_settings["maintenance_mode"] = not global_settings.get("maintenance_mode", False)
        database.save_global_settings(global_settings)
        
        status_word = "enabled" if global_settings["maintenance_mode"] else "disabled"
        await event.answer(f"🔧 Maintenance Mode is now {status_word}.", alert=True)
        await show_admin_panel(event, user_id)

    # ------------------ Images & UI Management Sub-Menu ------------------
    @client.on(events.CallbackQuery(pattern="^admin_manage_images$"))
    async def admin_manage_images_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        global_settings = database.get_global_settings()
        start_img = global_settings.get("start_image") or "❌ None (Text Only)"
        ping_img = global_settings.get("ping_image") or "❌ None (Text Only)"
        help_img = global_settings.get("help_image") or "❌ None (Text Only)"
        
        text = (
            f"<blockquote><b>» 🖼️ Bot Ui & Images Management</b>\n\n"
            f"Configure Images Displayed Across Bot Commands:\n\n"
            f"• <b>Start Image :</b> <code>{start_img}</code>\n"
            f"• <b>Ping Image :</b> <code>{ping_img}</code>\n"
            f"• <b>Help Image :</b> <code>{help_img}</code>\n\n"
            f"💡 <i>Use The Buttons Below To Set Or Remove Images.</i></blockquote>"
        )
        buttons = [
            [
                utils.styled_button("🖼️ Set Start Image", "admin_set_start_img", style="primary"),
                utils.styled_button("❌ Remove Start Image", "admin_rem_start_img", style="danger")
            ],
            [
                utils.styled_button("🖼️ Set Ping Image", "admin_set_ping_img", style="primary"),
                utils.styled_button("❌ Remove Ping Image", "admin_rem_ping_img", style="danger")
            ],
            [
                utils.styled_button("🖼️ Set Help Image", "admin_set_help_img", style="primary"),
                utils.styled_button("❌ Remove Help Image", "admin_rem_help_img", style="danger")
            ],
            [
                utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_rem_start_img$"))
    async def admin_rem_start_img_callback(event):
        if not check_admin(event.sender_id):
            return
        global_settings = database.get_global_settings()
        global_settings["start_image"] = None
        database.save_global_settings(global_settings)
        await event.answer("✅ Start Image removed (Text-Only Mode active)!", alert=True)
        await admin_manage_images_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_rem_ping_img$"))
    async def admin_rem_ping_img_callback(event):
        if not check_admin(event.sender_id):
            return
        global_settings = database.get_global_settings()
        global_settings["ping_image"] = None
        database.save_global_settings(global_settings)
        await event.answer("✅ Ping Image removed (Text-Only Mode active)!", alert=True)
        await admin_manage_images_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_rem_help_img$"))
    async def admin_rem_help_img_callback(event):
        if not check_admin(event.sender_id):
            return
        global_settings = database.get_global_settings()
        global_settings["help_image"] = None
        database.save_global_settings(global_settings)
        await event.answer("✅ Help Image removed (Text-Only Mode active)!", alert=True)
        await admin_manage_images_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_set_start_img$"))
    async def admin_set_start_img_callback(event):
        if not check_admin(event.sender_id):
            return
        _admin_action_states[event.sender_id] = "WAITING_FOR_SET_START_IMG"
        prompt_text = "<blockquote><b>» 🖼️ Set Start Image</b>\n\nSend A Direct Image Url, File Id, Or Send A Photo (Or Type <code>none</code> To Remove):</blockquote>"
        buttons = [[utils.styled_button("🔙 Cancel", "admin_manage_images", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_set_ping_img$"))
    async def admin_set_ping_img_callback(event):
        if not check_admin(event.sender_id):
            return
        _admin_action_states[event.sender_id] = "WAITING_FOR_SET_PING_IMG"
        prompt_text = "<blockquote><b>» 🖼️ Set Ping Image</b>\n\nSend A Direct Image Url, File Id, Or Send A Photo (Or Type <code>none</code> To Remove):</blockquote>"
        buttons = [[utils.styled_button("🔙 Cancel", "admin_manage_images", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_set_help_img$"))
    async def admin_set_help_img_callback(event):
        if not check_admin(event.sender_id):
            return
        _admin_action_states[event.sender_id] = "WAITING_FOR_SET_HELP_IMG"
        prompt_text = "<blockquote><b>» 🖼️ Set Help Image</b>\n\nSend A Direct Image Url, File Id, Or Send A Photo (Or Type <code>none</code> To Remove):</blockquote>"
        buttons = [[utils.styled_button("🔙 Cancel", "admin_manage_images", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_(set_(price|fj|lg|bu|bd|imgs|upi|usdt|ton|usdt_rate|ton_rate|ub_joins|comm)|join_all_sessions|add_admin|rem_admin)$"))
    async def admin_setting_callback(event):
        action = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return
            
        # Register prompt state
        _admin_action_states[user_id] = f"WAITING_FOR_{action.upper()}"
        
        # Select prompt message key
        prompt_keys = {
            "set_price": "prompt_set_price",
            "set_fj": "prompt_set_fj",
            "set_lg": "prompt_set_lg",
            "set_bu": "prompt_set_bu",
            "set_bd": "prompt_set_bd",
            "set_imgs": "prompt_set_imgs",
            "set_upi": "prompt_set_upi",
            "set_usdt": "prompt_set_usdt",
            "set_ton": "prompt_set_ton",
            "set_usdt_rate": "prompt_set_usdt_rate",
            "set_ton_rate": "prompt_set_ton_rate",
            "set_ub_joins": "prompt_set_ub_joins",
            "set_comm": "prompt_set_comm",
            "add_admin": "prompt_add_admin",
            "rem_admin": "prompt_rem_admin"
        }
        
        prompt_key = prompt_keys.get(action, "error_generic")
        
        # Custom prompt display helper
        if action == "set_upi":
            prompt_text = "<blockquote><b>» 🏦 Send The New Admin Upi Id</b> (e.g. <code>merchant@upi</code>):</blockquote>"
        elif action == "set_usdt":
            prompt_text = "<blockquote><b>» 🪙 Send The New Usdt Wallet Address :</b></blockquote>"
        elif action == "set_ton":
            prompt_text = "<blockquote><b>» 💎 Send The New Ton Wallet Address :</b></blockquote>"
        elif action == "set_usdt_rate":
            prompt_text = "<blockquote><b>» 🪙 Send The New Usdt Rate (e.g. 90.5 for 1$ = 90.5 INR) :</b></blockquote>"
        elif action == "set_ton_rate":
            prompt_text = "<blockquote><b>» 💎 Send The New Ton Rate (e.g. 500 for 1 TON = 500 INR) :</b></blockquote>"
        elif action == "set_ub_joins":
            prompt_text = "<blockquote><b>» 🔗 Send The New List Of Userbot Auto-Join Links</b> (Separated By Commas Or 'none'):</blockquote>"
        elif action == "join_all_sessions":
            prompt_text = "<blockquote><b>» 👥 Join All Sessions</b>\n\nSend The Invite Link/Username Of The Group Or Channel That All Logged-In Accounts Should Join :</blockquote>"
        elif action == "set_comm":
            prompt_text = "<blockquote><b>» 📊 Send The New Referral Commission Rate</b> (0.01 - 0.99 for 1%-99%):</blockquote>"
        else:
            prompt_text = utils.get_text(prompt_key, lang)
            
        buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_setting", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    # ------------------ Broadcast Handlers ------------------
    @client.on(events.CallbackQuery(pattern="^admin_broadcast$"))
    async def admin_broadcast_menu_callback(event):
        if not check_admin(event.sender_id):
            return
        all_sessions = database.get_sessions()
        active_sessions = [s for s in all_sessions if s.get("status") not in ("unauthorized", "dead", "revoked", "deleted", "banned")]
        all_users = database.get_all_users()
        
        text = (
            f"<blockquote><b>» 📢 ADMIN BROADCAST CONTROL HUB</b>\n\n"
            f"Select the type of broadcast you want to perform:\n\n"
            f"⚡ <b>Global UserBots Broadcast:</b> Broadcast directly through all <b>{len(active_sessions)}</b> active UserBots across their joined Groups, Supergroups, or DMs!\n\n"
            f"🤖 <b>Main Bot Users Broadcast:</b> Broadcast directly through this Bot to all <b>{len(all_users)}</b> registered bot users.</blockquote>"
        )
        buttons = [
            [
                utils.styled_button("⚡ Global Userbots Broadcast", "admin_bc_all_ub_menu", style="success")
            ],
            [
                utils.styled_button("🤖 Main Bot Users Broadcast", "admin_bc_main_bot", style="primary")
            ],
            [
                utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_bc_all_ub_menu$"))
    async def admin_bc_all_ub_menu_callback(event):
        if not check_admin(event.sender_id):
            return
        all_sessions = database.get_sessions()
        active_sessions = [s for s in all_sessions if s.get("status") not in ("unauthorized", "dead", "revoked", "deleted", "banned")]
        text = (
            f"<blockquote><b>» ⚡ GLOBAL USERBOTS BROADCAST</b>\n\n"
            f"🤖 <b>Active UserBots Available:</b> <b>{len(active_sessions)}</b>\n\n"
            f"📌 <b>Select Target Destination:</b>\n"
            f"Choose where the UserBots should send your broadcast message:</blockquote>"
        )
        buttons = [
            [
                utils.styled_button("👥 Only In Groups / Supergroups", "admin_ub_bc_target_groups", style="primary"),
            ],
            [
                utils.styled_button("👤 Only In User Dms (Private)", "admin_ub_bc_target_dms", style="primary"),
            ],
            [
                utils.styled_button("🌐 Both Groups & Dms", "admin_ub_bc_target_both", style="success"),
            ],
            [
                utils.styled_button("🔙 Back", "admin_broadcast", style="danger")
            ]
        ]
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_ub_bc_target_(groups|dms|both)$"))
    async def admin_ub_bc_target_callback(event):
        if not check_admin(event.sender_id):
            return
        target = event.pattern_match.group(1)
        target_names = {
            "groups": "👥 Groups / Supergroups Only",
            "dms": "👤 User DMs (Private) Only",
            "both": "🌐 Both Groups & User DMs"
        }
        target_title = target_names.get(target, target)
        _admin_action_states[event.sender_id] = f"WAITING_FOR_ADMIN_UB_BC_{target.upper()}"
        
        prompt_text = (
            f"<blockquote><b>» 📝 ENTER USERBOT BROADCAST MESSAGE</b>\n\n"
            f"🎯 <b>Target:</b> <b>{target_title}</b>\n\n"
            f"Please send the message you want to broadcast through all UserBots.\n"
            f"• Supports text with formatting (Bold, HTML, links)\n"
            f"• Supports Photos, Videos, Documents with captions\n"
            f"• Supports Forwarded messages</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_bc_all_ub_menu", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern="^admin_bc_main_bot$"))
    async def admin_bc_main_bot_callback(event):
        if not check_admin(event.sender_id):
            return
        _admin_action_states[event.sender_id] = "WAITING_FOR_BROADCAST"
        all_users = database.get_all_users()
        prompt_text = (
            f"<blockquote><b>» 🤖 MAIN BOT USERS BROADCAST</b>\n\n"
            f"👥 <b>Total Bot Users:</b> <b>{len(all_users)}</b>\n\n"
            f"Please send the message you want to broadcast to all registered bot users.\n"
            f"• Supports text, links, photos, videos, and media with captions.</blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_broadcast", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    async def _execute_global_ub_broadcast(client, prog_msg, target, target_title, broadcast_msg, active_sessions):
        import userbot_manager
        from telethon.errors import (
            FloodWaitError, ChatWriteForbiddenError, UserBannedInChannelError,
            ChatAdminRequiredError, ChannelPrivateError, SlowModeWaitError
        )
        import asyncio
        import time
        import os

        total_bots = len(active_sessions)
        total_sent_groups = 0
        total_sent_dms = 0
        total_failed = 0
        bots_done = 0
        last_edit_time = time.time()

        try:
            # Download media once to disk if the broadcast message has media
            media_path = None
            if broadcast_msg.media:
                try:
                    media_path = await client.download_media(broadcast_msg)
                    logger.info(f"Downloaded broadcast media to {media_path}")
                except Exception as dl_err:
                    logger.error(f"Failed to download broadcast media: {dl_err}")
            for sess in active_sessions:
                sess_id = sess.get("session_id") or sess.get("phone")
                phone_num = sess.get("phone") or sess_id

                try:
                    # Check if bot is running
                    bot_obj = userbot_manager._running_bots.get(sess_id) or userbot_manager._running_bots.get(phone_num)
                    if not bot_obj or not bot_obj.is_running:
                        started = await userbot_manager.start_userbot(sess_id)
                        if not started and phone_num != sess_id:
                            started = await userbot_manager.start_userbot(phone_num)
                        bot_obj = userbot_manager._running_bots.get(sess_id) or userbot_manager._running_bots.get(phone_num)

                    if not bot_obj or not bot_obj.client:
                        logger.warning(f"Userbot {phone_num} could not be loaded or started for broadcast.")
                        total_failed += 1
                        bots_done += 1
                        continue

                    ub_client = bot_obj.client
                    if not ub_client.is_connected():
                        await ub_client.connect()

                    # Iterate dialogs
                    async for dialog in ub_client.iter_dialogs(limit=150):
                        is_grp = dialog.is_group or (dialog.is_channel and getattr(dialog.entity, 'megagroup', False))
                        is_dm = dialog.is_user and not getattr(dialog.entity, 'bot', False) and not getattr(dialog.entity, 'is_self', False)

                        should_send = False
                        if target == "groups" and is_grp:
                            should_send = True
                        elif target == "dms" and is_dm:
                            should_send = True
                        elif target == "both" and (is_grp or is_dm):
                            should_send = True

                        if not should_send:
                            continue

                        try:
                            if media_path:
                                await ub_client.send_file(dialog.id, media_path, caption=broadcast_msg.text or "")
                            else:
                                await ub_client.send_message(dialog.id, broadcast_msg.text or "")

                            if is_grp:
                                total_sent_groups += 1
                            else:
                                total_sent_dms += 1

                            now = time.time()
                            if now - last_edit_time >= 4.0:
                                last_edit_time = now
                                try:
                                    await prog_msg.edit(
                                        f"<blockquote><b>» ⏳ GLOBAL USERBOT BROADCAST IN PROGRESS</b>\n\n"
                                        f"🎯 <b>Target:</b> <b>{target_title}</b>\n"
                                        f"🤖 <b>Processing UserBots:</b> <code>[{bots_done + 1} / {total_bots}]</code>\n"
                                        f"👥 <b>Groups Sent:</b> <code>{total_sent_groups}</code>\n"
                                        f"👤 <b>DMs Sent:</b> <code>{total_sent_dms}</code>\n"
                                        f"⚠️ <b>Skipped / Errors:</b> <code>{total_failed}</code>\n\n"
                                        f"⚡ <i>Broadcasting actively in background with floodwait protection...</i></blockquote>"
                                    )
                                except Exception:
                                    pass

                            await asyncio.sleep(1.0)

                        except FloodWaitError as fwe:
                            if fwe.seconds <= 20:
                                await asyncio.sleep(fwe.seconds + 1)
                            else:
                                logger.warning(f"Long FloodWait ({fwe.seconds}s) on userbot {phone_num}, moving to next...")
                                total_failed += 1
                                break
                        except (ChatWriteForbiddenError, UserBannedInChannelError, ChatAdminRequiredError, ChannelPrivateError):
                            total_failed += 1
                        except SlowModeWaitError:
                            total_failed += 1
                        except Exception as send_err:
                            logger.debug(f"Failed to send from {phone_num} to {dialog.id}: {send_err}")
                            total_failed += 1

                except Exception as bot_err:
                    logger.error(f"Error processing broadcast for bot {phone_num}: {bot_err}")
                    total_failed += 1

                bots_done += 1
                try:
                    await prog_msg.edit(
                        f"<blockquote><b>» ⏳ GLOBAL USERBOT BROADCAST IN PROGRESS</b>\n\n"
                        f"🎯 <b>Target:</b> <b>{target_title}</b>\n"
                        f"🤖 <b>Processing UserBots:</b> <code>[{bots_done} / {total_bots}]</code>\n"
                        f"👥 <b>Groups Sent:</b> <code>{total_sent_groups}</code>\n"
                        f"👤 <b>DMs Sent:</b> <code>{total_sent_dms}</code>\n"
                        f"⚠️ <b>Skipped / Errors:</b> <code>{total_failed}</code>\n\n"
                        f"⚡ <i>Broadcasting actively in background with floodwait protection...</i></blockquote>"
                    )
                except Exception:
                    pass

        except Exception as fatal_err:
            logger.exception(f"Fatal error in global userbot broadcast: {fatal_err}")
            try:
                await prog_msg.edit(
                    f"<blockquote><b>» ❌ GLOBAL USERBOT BROADCAST FAILED</b>\n\n"
                    f"⚠️ <b>Error:</b> <code>{fatal_err}</code></blockquote>"
                )
            except Exception:
                pass
            return
        finally:
            if media_path and os.path.exists(media_path):
                try:
                    os.remove(media_path)
                except Exception:
                    pass

        report = (
            f"<blockquote><b>» 📊 GLOBAL USERBOT BROADCAST COMPLETED</b>\n\n"
            f"🎯 <b>Target Destination:</b> <b>{target_title}</b>\n"
            f"🤖 <b>UserBots Used:</b> <b>{bots_done} / {total_bots}</b>\n"
            f"👥 <b>Groups Delivered:</b> <b>{total_sent_groups}</b>\n"
            f"👤 <b>DMs Delivered:</b> <b>{total_sent_dms}</b>\n"
            f"⚠️ <b>Errors / Skipped:</b> <b>{total_failed}</b>\n"
            f"✅ <b>Status:</b> <b>Completed Successfully</b></blockquote>"
        )
        try:
            await prog_msg.edit(report)
        except Exception:
            try:
                await prog_msg.respond(report)
            except Exception:
                pass

    @client.on(events.CallbackQuery(pattern="^admin_ub_bc_confirm$"))
    async def admin_ub_bc_confirm_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        pending = _admin_ub_bc_pending.pop(user_id, None)
        if not pending:
            try:
                await event.answer("⚠️ No pending broadcast found. Please try again.", alert=True)
            except Exception:
                pass
            await admin_broadcast_menu_callback(event)
            return
            
        try:
            await event.answer("🚀 Starting Global UserBot Broadcast...", alert=False)
        except Exception:
            pass

        target = pending["target"]
        broadcast_msg = pending["message"]
        target_names = {
            "groups": "👥 Groups / Supergroups Only",
            "dms": "👤 User DMs (Private) Only",
            "both": "🌐 Both Groups & User DMs"
        }
        target_title = target_names.get(target, target)
        
        all_sessions = database.get_sessions()
        active_sessions = [
            s for s in all_sessions 
            if s.get("status") not in ("unauthorized", "dead", "revoked", "deleted", "banned")
        ]
        
        if not active_sessions:
            await event.respond("❌ <b>No active or authorized userbot sessions found to broadcast.</b>\n\nPlease add or start userbots first.")
            return
            
        prog_msg = await event.respond(
            f"<blockquote><b>» ⏳ GLOBAL USERBOT BROADCAST IN PROGRESS</b>\n\n"
            f"🎯 <b>Target:</b> <b>{target_title}</b>\n"
            f"🤖 <b>Processing UserBots:</b> <code>[0 / {len(active_sessions)}]</code>\n"
            f"👥 <b>Groups Sent:</b> <code>0</code>\n"
            f"👤 <b>DMs Sent:</b> <code>0</code>\n\n"
            f"⚡ <i>Broadcasting in background with floodwait protection...</i></blockquote>"
        )
        
        import asyncio
        asyncio.create_task(
            _execute_global_ub_broadcast(
                client=client,
                prog_msg=prog_msg,
                target=target,
                target_title=target_title,
                broadcast_msg=broadcast_msg,
                active_sessions=active_sessions
            )
        )


    # ------------------ Admin Message Input Listener ------------------
    @client.on(events.NewMessage)
    async def admin_text_input_handler(event):
        if not event.is_private:
            return
            
        user_id = event.sender_id
        if user_id not in _admin_action_states:
            return
            
        if event.text.startswith("/start"):
            _admin_action_states.pop(user_id, None)
            _admin_plan_temp.pop(user_id, None)
            return
            
        action = _admin_action_states.pop(user_id)
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.reply(utils.get_text("error_not_admin", lang))
            return
            
        global_settings = database.get_global_settings()
        success = False
        val_str = (event.text or event.raw_text or "").strip()
        
        try:
            # Gift Slots feature
            if action == "WAITING_FOR_GIFT_USERID":
                try:
                    target_uid = int(val_str)
                except ValueError:
                    await event.reply("❌ Invalid User ID. Please enter a valid number.")
                    return
                target_user = database.get_user(target_uid)
                if not target_user:
                    await event.reply("❌ **User not found** in the database.")
                    return
                _admin_plan_temp.setdefault(user_id, {})["gift_target"] = target_uid
                _admin_action_states[user_id] = "WAITING_FOR_GIFT_USER_SLOTS"
                await event.reply(f"✅ User found: `{target_uid}`\n\n✏️ **Send the number of slots** you want to gift them.")
                return

            elif action == "WAITING_FOR_GIFT_USER_SLOTS":
                try:
                    slots_to_gift = int(val_str)
                except ValueError:
                    await event.reply("❌ Invalid number. Please enter a valid integer.")
                    return
                if slots_to_gift <= 0: raise ValueError("Slots must be positive")
                target_uid = _admin_plan_temp.get(user_id, {}).get("gift_target")
                if target_uid:
                    target_user = database.get_user(target_uid)
                    if target_user:
                        target_user["allowed_slots"] = target_user.get("allowed_slots", 0) + slots_to_gift
                        database.save_user(target_user)
                        await event.reply(f"✅ **Successfully gifted {slots_to_gift} slots** to `{target_uid}`!")
                        try:
                            await client.send_message(target_uid, f"🎉 **Congratulations!**\n\nYou have been gifted **{slots_to_gift} Userbot Slots** by the Administrator! Enjoy your new slots! 🎁")
                        except Exception as e:
                            logger.error(f"Failed to send gift notification: {e}")
                _admin_plan_temp.pop(user_id, None)
                return

            elif action == "WAITING_FOR_GIFT_ALL_SLOTS":
                try:
                    slots_to_gift = int(val_str)
                except ValueError:
                    await event.reply("❌ Invalid number. Please enter a valid integer.")
                    return
                if slots_to_gift <= 0: raise ValueError("Slots must be positive")
                all_users = database.get_all_users()
                count = 0
                await event.reply(f"⏳ **Gifting {slots_to_gift} slots** to all {len(all_users)} users... Please wait.")
                for u in all_users:
                    u["allowed_slots"] = u.get("allowed_slots", 0) + slots_to_gift
                    database.save_user(u)
                    count += 1
                    try:
                        # Schedule message in background to not block the loop
                        client.loop.create_task(client.send_message(u["user_id"], f"🎉 **Congratulations!**\n\nYou and all other users have been gifted **{slots_to_gift} Userbot Slots** by the Administrator! Enjoy! 🎁"))
                    except Exception:
                        pass
                await event.reply(f"✅ **Successfully gifted {slots_to_gift} slots** to all {count} users!")
                return

            # 0.1 Plan Days
            elif action == "WAITING_FOR_PLAN_DAYS":
                days = int(val_str)
                if days <= 0:
                    raise ValueError("Days must be positive")
                _admin_plan_temp.setdefault(user_id, {})["days"] = days
                _admin_action_states[user_id] = "WAITING_FOR_PLAN_SLOTS"
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
                await event.reply(utils.get_text("prompt_plan_slots", lang), buttons=buttons)
                return
                
            # 0.1.b Plan Slots
            elif action == "WAITING_FOR_PLAN_SLOTS":
                slots = int(val_str)
                if slots <= 0:
                    raise ValueError("Slots must be positive")
                _admin_plan_temp.setdefault(user_id, {})["slots"] = slots
                _admin_action_states[user_id] = "WAITING_FOR_PLAN_PRICE"
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
                await event.reply(utils.get_text("prompt_plan_price", lang), buttons=buttons)
                return
                
            # 0.2 Plan Price
            elif action == "WAITING_FOR_PLAN_PRICE":
                price = float(val_str)
                if price <= 0:
                    raise ValueError("Price must be positive")
                _admin_plan_temp.setdefault(user_id, {})["price"] = price
                _admin_action_states[user_id] = "WAITING_FOR_PLAN_NAME"
                buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
                await event.reply(utils.get_text("prompt_plan_name", lang), buttons=buttons)
                return
                
            # 0.3 Plan Name
            elif action == "WAITING_FOR_PLAN_NAME":
                name = val_str
                if not name:
                    raise ValueError("Name cannot be empty")
                
                temp_data = _admin_plan_temp.pop(user_id, None)
                if not temp_data or "days" not in temp_data or "slots" not in temp_data or "price" not in temp_data:
                    await event.reply("❌ State lost. Please start over.")
                    # Show manage plans sub-menu
                    class MockEvent:
                        def __init__(self, uid, ev):
                            self.sender_id = uid
                            self.respond = ev.respond
                            self.edit = ev.respond
                        async def answer(self, *args, **kwargs):
                            pass
                    await admin_manage_plans_callback(MockEvent(user_id, event))
                    return
                    
                days = temp_data["days"]
                slots = temp_data["slots"]
                price = temp_data["price"]
                
                import uuid
                plan_id = "plan_" + str(uuid.uuid4())[:6]
                
                plans = global_settings.setdefault("subscription_plans", [])
                plans.append({
                    "id": plan_id,
                    "days": days,
                    "slots": slots,
                    "price": price,
                    "button_name": name
                })
                database.save_global_settings(global_settings)
                
                await event.reply(
                    f"✅ <b>Subscription Plan Added Successfully!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Plan ID: `{plan_id}`\n"
                    f"Name: <b>{name}</b>\n"
                    f"Days: <b>{days}</b>\n"
                    f"Slots count: <b>{slots}</b>\n"
                    f"Total Price: <b>₹{price:.2f}</b>"
                )
                
                # Show manage plans sub-menu
                class MockEvent:
                    def __init__(self, uid, ev):
                        self.sender_id = uid
                        self.respond = ev.respond
                        self.edit = ev.respond
                    async def answer(self, *args, **kwargs):
                        pass
                await admin_manage_plans_callback(MockEvent(user_id, event))
                return

            # 1. Set global Price per extra ID
            elif action == "WAITING_FOR_SET_PRICE":
                global_settings["price_per_id"] = float(val_str)
                success = True
                
            # 2. Set Force Join channels
            elif action == "WAITING_FOR_SET_FJ":
                if val_str.lower() == "none":
                    global_settings["force_join_links"] = []
                else:
                    global_settings["force_join_links"] = [x.strip() for x in val_str.split(",") if x.strip()]
                success = True
                
            # 3. Set log group ID
            elif action == "WAITING_FOR_SET_LG":
                # Must be an integer ID
                global_settings["log_group_id"] = int(val_str)
                success = True
                
            # 4. Set branding username
            elif action == "WAITING_FOR_SET_BU":
                if val_str.lower() == "none":
                    global_settings["branding_username"] = None
                else:
                    # Strip @ if present
                    global_settings["branding_username"] = val_str.replace("@", "")
                success = True
                
            # 5. Set branding duration
            elif action == "WAITING_FOR_SET_BD":
                global_settings["branding_duration"] = int(val_str)
                success = True
                
            # Set branding name suffix text
            elif action == "WAITING_FOR_BRAND_NAME_TXT":
                if val_str.lower() == "none":
                    global_settings["branding_name_text"] = None
                else:
                    global_settings["branding_name_text"] = val_str
                success = True
                
            # Set branding bio suffix text
            elif action == "WAITING_FOR_BRAND_BIO_TXT":
                if val_str.lower() == "none":
                    global_settings["branding_bio_text"] = None
                else:
                    global_settings["branding_bio_text"] = val_str
                success = True
                
            # 6. Set Individual Images
            elif action == "WAITING_FOR_SET_START_IMG":
                if event.photo:
                    os.makedirs("downloads", exist_ok=True)
                    photo_file = await client.download_media(event.photo, file="downloads/")
                    global_settings["start_image"] = photo_file
                elif val_str.lower() in ("none", "remove", "delete", "off"):
                    global_settings["start_image"] = None
                elif val_str:
                    global_settings["start_image"] = val_str
                success = True

            elif action == "WAITING_FOR_SET_PING_IMG":
                if event.photo:
                    os.makedirs("downloads", exist_ok=True)
                    photo_file = await client.download_media(event.photo, file="downloads/")
                    global_settings["ping_image"] = photo_file
                elif val_str.lower() in ("none", "remove", "delete", "off"):
                    global_settings["ping_image"] = None
                elif val_str:
                    global_settings["ping_image"] = val_str
                success = True

            elif action == "WAITING_FOR_SET_HELP_IMG":
                if event.photo:
                    os.makedirs("downloads", exist_ok=True)
                    photo_file = await client.download_media(event.photo, file="downloads/")
                    global_settings["help_image"] = photo_file
                elif val_str.lower() in ("none", "remove", "delete", "off"):
                    global_settings["help_image"] = None
                elif val_str:
                    global_settings["help_image"] = val_str
                success = True

            # 6.b Set Images (Start, Ping, Help) in bulk
            elif action == "WAITING_FOR_SET_IMGS":
                parts = [p.strip() for p in val_str.split(",") if p.strip()]
                if len(parts) == 3:
                    global_settings["start_image"] = parts[0] if parts[0].lower() != "none" else None
                    global_settings["ping_image"] = parts[1] if parts[1].lower() != "none" else None
                    global_settings["help_image"] = parts[2] if parts[2].lower() != "none" else None
                    success = True
                else:
                    raise ValueError("Must provide 3 comma-separated URLs or File IDs (or 'none').")
                    
            # 6.1 Set UPI ID
            elif action == "WAITING_FOR_SET_UPI":
                global_settings["upi_id"] = val_str
                success = True
                
            # 6.2 Set USDT address
            elif action == "WAITING_FOR_SET_USDT":
                global_settings["usdt_bep20_address"] = val_str
                success = True
                
            # 6.2.1 Set TON address
            elif action == "WAITING_FOR_SET_TON":
                global_settings["ton_address"] = val_str
                success = True
                
            # 6.2.x Set USDT Rate
            elif action == "WAITING_FOR_SET_USDT_RATE":
                global_settings["usdt_rate"] = float(val_str)
                success = True
                
            # 6.2.y Set TON Rate
            elif action == "WAITING_FOR_SET_TON_RATE":
                global_settings["ton_rate"] = float(val_str)
                success = True
                
            # 6.2.2 Set custom userbot auto-join links
            elif action == "WAITING_FOR_SET_UB_JOINS":
                if val_str.lower() == "none":
                    global_settings["userbot_auto_join_links"] = []
                else:
                    global_settings["userbot_auto_join_links"] = [x.strip() for x in val_str.split(",") if x.strip()]
                success = True
                
            # System-wide collective tasks for ALL DB accounts (running or stopped)
            elif action in ["WAITING_FOR_SYS_JOIN_GRP", "WAITING_FOR_SYS_LEAVE_GRP", "WAITING_FOR_SYS_JOIN_VC", "WAITING_FOR_SYS_PLAY_SONG"]:
                link = val_str
                all_sessions = database.get_sessions()
                total = len(all_sessions)
                if not total:
                    await event.reply("❌ No userbot sessions found in database.")
                    await admin_sys_vc_menu_callback(event)
                    return
                    
                action_name_map = {
                    "WAITING_FOR_SYS_JOIN_GRP": "Join Group",
                    "WAITING_FOR_SYS_LEAVE_GRP": "Leave Group",
                    "WAITING_FOR_SYS_JOIN_VC": "Join Voice Chat",
                    "WAITING_FOR_SYS_PLAY_SONG": "Play Song"
                }
                action_name = action_name_map[action]
                
                progress_msg = await event.reply(f"⏳ <b>Executing '{action_name}' on all {total} bots...</b>\nPlease wait, this may take a while as offline bots will be temporarily started.")
                
                from userbot import join_channel_single, leave_channel_single
                import userbot_manager
                
                async def _sys_action_one_db_account(s):
                    phone_num = s["phone"]
                    if not userbot_manager.is_bot_running(phone_num):
                        await userbot_manager.start_userbot(phone_num)
                    
                    bot_obj = userbot_manager._running_bots.get(phone_num)
                    if not bot_obj or not bot_obj.client:
                        return False
                        
                    if action == "WAITING_FOR_SYS_JOIN_GRP":
                        ok = await join_channel_single(bot_obj.client, link)
                        if ok:
                            bot_obj.groups_cache_time = 0
                        return ok
                    elif action == "WAITING_FOR_SYS_LEAVE_GRP":
                        return await leave_channel_single(bot_obj.client, link)
                    elif action == "WAITING_FOR_SYS_JOIN_VC":
                        try:
                            # Note: join_voice_chat sends progress messages by default, we catch any text prints or errors.
                            await bot_obj.join_voice_chat(link)
                            return True
                        except Exception as e:
                            logger.error(f"Error in System Join VC for {phone_num}: {e}")
                            return False
                    elif action == "WAITING_FOR_SYS_PLAY_SONG":
                        try:
                            # Use query processing from userbot (youtube download or direct play)
                            # We can just call play_song directly.
                            await bot_obj.play_song(link)
                            return True
                        except Exception as e:
                            logger.error(f"Error in System Play Song for {phone_num}: {e}")
                            return False
                    return False
                    
                results = await asyncio.gather(*[_sys_action_one_db_account(s) for s in all_sessions], return_exceptions=True)
                await progress_msg.delete()
                
                success_count = sum(1 for r in results if not isinstance(r, Exception) and r)
                fail_count = total - success_count
                
                report = (
                    f"📊 <b>System Action Report: {action_name}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"Input: {link}\n"
                    f"Total Accounts in DB: <b>{total}</b>\n"
                    f"✅ Success: <b>{success_count}</b>\n"
                    f"❌ Failed: <b>{fail_count}</b>"
                )
                await event.reply(report)
                _admin_action_states.pop(user_id, None)
                await admin_sys_vc_menu_callback(event)
                return
                
            # 6.3 Set referral commission
            elif action == "WAITING_FOR_SET_COMM":
                val = float(val_str)
                if 0.0 <= val <= 1.0:
                    global_settings["referral_commission"] = val
                    success = True
                else:
                    raise ValueError("Commission must be between 0.0 and 1.0")
                    
            # 6.3.a Global UserBots Broadcast Input
            elif action.startswith("WAITING_FOR_ADMIN_UB_BC_"):
                target = action.replace("WAITING_FOR_ADMIN_UB_BC_", "").lower()
                _admin_ub_bc_pending[user_id] = {
                    "target": target,
                    "message": event.message
                }
                target_names = {
                    "groups": "👥 Groups / Supergroups Only",
                    "dms": "👤 User DMs (Private) Only",
                    "both": "🌐 Both Groups & User DMs"
                }
                target_title = target_names.get(target, target)
                all_sessions = database.get_sessions()
                active_sessions = [s for s in all_sessions if s.get("status") not in ("unauthorized", "dead", "revoked", "deleted", "banned")]
                
                confirm_text = (
                    f"<blockquote><b>» 🚀 CONFIRM GLOBAL USERBOT BROADCAST</b>\n\n"
                    f"🎯 <b>Target:</b> <b>{target_title}</b>\n"
                    f"🤖 <b>Active UserBots:</b> <b>{len(active_sessions)}</b>\n\n"
                    f"⚠️ <i>Broadcast will be dispatched from all connected userbots across their joined chats with smart flood protection.</i>\n\n"
                    f"<b>Are you ready to start broadcasting?</b></blockquote>"
                )
                buttons = [
                    [
                        utils.styled_button("🚀 Start Global Broadcast", "admin_ub_bc_confirm", style="success")
                    ],
                    [
                        utils.styled_button("❌ Cancel", "admin_broadcast", style="danger")
                    ]
                ]
                await event.reply(confirm_text, buttons=buttons)
                return

            # 6.3.b Global Main Bot Broadcast
            elif action == "WAITING_FOR_BROADCAST":
                _admin_action_states.pop(user_id, None)
                prog_msg = await event.reply("<blockquote><b>» 📢 MAIN BOT BROADCAST IN PROGRESS</b>\n\nSending message to all registered bot users...</blockquote>")
                
                broadcast_message = event.message
                all_users = database.get_all_users()
                
                async def _run_bot_broadcast():
                    total_users = len(all_users)
                    success_count = 0
                    fail_count = 0
                    processed_count = 0
                    last_edit_time = time.time()
                    sem = asyncio.Semaphore(12)
                    lock = asyncio.Lock()

                    async def _send_to_user(u):
                        nonlocal success_count, fail_count, processed_count, last_edit_time
                        uid = u.get("user_id")
                        if not uid:
                            return

                        async with sem:
                            target_peer = None
                            # 1. Stored access_hash is fastest (0 network roundtrips)
                            if u.get("access_hash"):
                                try:
                                    from telethon.tl.types import InputPeerUser
                                    target_peer = InputPeerUser(int(uid), int(u["access_hash"]))
                                except Exception:
                                    pass

                            # 2. Try Telethon internal cache / entity lookup
                            if not target_peer:
                                try:
                                    target_peer = await client.get_input_entity(int(uid))
                                except Exception:
                                    if u.get("username"):
                                        try:
                                            target_peer = await client.get_input_entity(u["username"])
                                        except Exception:
                                            pass

                            if not target_peer:
                                async with lock:
                                    fail_count += 1
                                    processed_count += 1
                                return

                            sent = False
                            try:
                                await client.send_message(target_peer, broadcast_message)
                                sent = True
                            except FloodWaitError as fwe:
                                wait_time = min(fwe.seconds, 15)
                                logger.warning(f"Flood wait {wait_time}s for user {uid}")
                                await asyncio.sleep(wait_time)
                                try:
                                    await client.send_message(target_peer, broadcast_message)
                                    sent = True
                                except Exception as retry_err:
                                    logger.error(f"Failed retry to {uid}: {retry_err}")
                            except Exception as err:
                                logger.warning(f"Failed to send broadcast to {uid}: {err}")

                            async with lock:
                                if sent:
                                    success_count += 1
                                else:
                                    fail_count += 1
                                processed_count += 1

                                now = time.time()
                                if now - last_edit_time >= 3.0:
                                    last_edit_time = now
                                    try:
                                        await prog_msg.edit(
                                            f"<blockquote><b>» 📢 MAIN BOT BROADCAST IN PROGRESS</b>\n\n"
                                            f"👥 <b>Processed:</b> <code>[{processed_count} / {total_users}]</code>\n"
                                            f"✅ <b>Delivered:</b> <code>{success_count}</code>\n"
                                            f"❌ <b>Failed / Skipped:</b> <code>{fail_count}</code></blockquote>"
                                        )
                                    except Exception:
                                        pass

                            await asyncio.sleep(0.04)

                    tasks = [asyncio.create_task(_send_to_user(u)) for u in all_users]
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)

                    report = (
                        f"<blockquote><b>» 📢 MAIN BOT BROADCAST COMPLETED</b>\n\n"
                        f"👥 <b>Total Target Users:</b> <b>{total_users}</b>\n"
                        f"✅ <b>Successfully Sent:</b> <b>{success_count}</b>\n"
                        f"❌ <b>Failed (Blocked/Deleted/Unresolved):</b> <b>{fail_count}</b></blockquote>"
                    )
                    try:
                        await prog_msg.edit(report)
                    except Exception:
                        await event.reply(report)
                        
                import asyncio
                asyncio.create_task(_run_bot_broadcast())
                return
                    
            # 6.4 User Management Search / Actions
            elif action == "WAITING_FOR_USR_STATS":
                await process_admin_usr_search(event, val_str, "stats")
                return
            elif action == "WAITING_FOR_USR_BAN":
                await process_admin_usr_search(event, val_str, "ban")
                return
            elif action == "WAITING_FOR_USR_UNBAN":
                await process_admin_usr_search(event, val_str, "unban")
                return
            elif action == "WAITING_FOR_USR_BAL":
                await process_admin_usr_search(event, val_str, "bal")
                return
            elif action == "WAITING_FOR_USR_CTRL":
                await process_admin_usr_search(event, val_str, "ctrl")
                return
                
            # 6.5 User Balance Editing (+/-)
            elif action.startswith("WAITING_FOR_EDITBAL_"):
                parts = action.split("_")
                edit_type = parts[3]
                target_uid = int(parts[4])
                
                try:
                    amount = float(val_str)
                    if amount <= 0:
                        raise ValueError("Amount must be positive")
                except ValueError:
                    await event.reply("❌ Invalid amount. Please send a valid positive number.")
                    return
                    
                target_user = database.get_user(target_uid)
                if not target_user:
                    await event.reply("❌ Target user not found in database.")
                    return
                    
                current_bal = target_user.get("wallet_balance", 0.0)
                if edit_type == "ADDBAL":
                    new_bal = current_bal + amount
                    success_msg = f"✅ <b>Successfully added ₹{amount:.2f} to user's wallet!</b>\nNew Balance: <b>₹{new_bal:.2f}</b>"
                else:
                    new_bal = max(0.0, current_bal - amount)
                    success_msg = f"✅ <b>Successfully subtracted ₹{amount:.2f} from user's wallet!</b>\nNew Balance: <b>₹{new_bal:.2f}</b>"
                    
                target_user["wallet_balance"] = new_bal
                database.save_user(target_user)
                
                await event.reply(success_msg)
                
                # Re-render balance management screen
                class MockEvent:
                    def __init__(self):
                        self.sender_id = user_id
                        self.respond = event.reply
                        self.edit = event.reply
                    async def answer(self, *args, **kwargs):
                        pass
                await process_admin_usr_search(MockEvent(), str(target_uid), "bal")
                return
                    
            # 7. Add Administrator
            elif action == "WAITING_FOR_ADD_ADMIN":
                new_admin = int(val_str)
                admins_list = global_settings.setdefault("admins", [])
                if new_admin not in admins_list:
                    admins_list.append(new_admin)
                success = True
                
            # 8. Remove Administrator
            elif action == "WAITING_FOR_REM_ADMIN":
                rem_admin = int(val_str)
                if rem_admin in config.ORIGINAL_ADMIN_IDS:
                    await event.reply("❌ Original administrators cannot be removed.")
                else:
                    admins_list = global_settings.setdefault("admins", [])
                    if rem_admin in admins_list:
                        admins_list.remove(rem_admin)
                    success = True
                    
        except Exception as e:
            logger.error(f"Failed to update admin settings: {e}")
            await event.reply(utils.get_text("admin_invalid", lang))
            
        if success:
            database.save_global_settings(global_settings)
            await event.reply(utils.get_text("admin_updated", lang))
            if action in ("WAITING_FOR_BRAND_NAME_TXT", "WAITING_FOR_BRAND_BIO_TXT"):
                await show_branding_settings(event, user_id)
                return
            if action in ("WAITING_FOR_SET_START_IMG", "WAITING_FOR_SET_PING_IMG", "WAITING_FOR_SET_HELP_IMG", "WAITING_FOR_SET_IMGS"):
                class MockImgEvent:
                    def __init__(self, uid, ev):
                        self.sender_id = uid
                        self.respond = ev.respond
                        self.edit = ev.respond
                    async def answer(self, *args, **kwargs):
                        pass
                await admin_manage_images_callback(MockImgEvent(user_id, event))
                return
            
        # Return to admin panel
        await show_admin_panel(event, user_id)

    @client.on(events.CallbackQuery(pattern="^admin_manage_users$"))
    async def admin_manage_users_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        text = (
            "👥 <b>User Management Panel</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "Manage user accounts, view detailed stats, and apply bans/unbans."
        )
        buttons = [
            [
                utils.styled_button("📊 View User Stats", "admin_usr_stats_start", style="primary"),
                utils.styled_button("👛 Check User Balance", "admin_usr_bal_start", style="success")
            ],
            [
                utils.styled_button("🚫 Ban User", "admin_usr_ban_start", style="danger"),
                utils.styled_button("🟢 Unban User", "admin_usr_unban_start", style="success")
            ],
            [
                utils.styled_button("🎮 Control Userbots", "admin_usr_ctrl_start", style="success")
            ],
            [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")]
        ]
        
        try:
            await event.edit(text, buttons=buttons)
        except Exception:
            await event.respond(text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_usr_(stats|ban|unban|bal|ctrl)_start$"))
    async def admin_usr_action_start(event):
        action = event.pattern_match.group(1)
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        _admin_action_states[user_id] = f"WAITING_FOR_USR_{action.upper()}"
        
        prompts = {
            "stats": "🔍 Send the User ID or Username of the user to view stats:",
            "ban": "🚫 Send the User ID or Username of the user to BAN:",
            "unban": "🟢 Send the User ID or Username of the user to UNBAN:",
            "bal": "👛 Send the User ID or Username of the user to view and edit balance:",
            "ctrl": "🎮 Send the User ID or Username of the user to open their UserBot Dashboard:"
        }
        
        buttons = [[utils.styled_button("🔙 Cancel", "admin_manage_users", style="danger")]]
        try:
            await event.edit(prompts[action], buttons=buttons)
        except Exception:
            await event.respond(prompts[action], buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_tglban_(\d+)$"))
    async def admin_tglban_callback(event):
        target_uid = int(event.pattern_match.group(1))
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        target_user = database.get_user(target_uid)
        if target_user:
            target_user["is_banned"] = not target_user.get("is_banned", False)
            database.save_user(target_user)
            status_str = "banned 🔴" if target_user["is_banned"] else "unbanned 🟢"
            await event.answer(f"User {target_uid} has been {status_str}.", alert=True)
            # Re-render stats for this user
            await process_admin_usr_search(event, str(target_uid), "stats")
        else:
            await event.answer("❌ User not found.", alert=True)

    @client.on(events.CallbackQuery(pattern=r"^admin_usr_opendashtrg_(\d+)$"))
    async def admin_usr_opendashtrg_callback(event):
        target_uid = int(event.pattern_match.group(1))
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        
        from handlers.my_bots import show_all_slots_dashboard, set_admin_impersonation
        set_admin_impersonation(user_id, target_uid)
        await show_all_slots_dashboard(event, target_uid, flash_message=f"👑 <b>Admin Access</b>: Controlling UserBots for User `{target_uid}`")

    @client.on(events.CallbackQuery(pattern=r"^admin_usr_(addbal|subbal)_(\d+)$"))
    async def admin_usr_editbal_callback(event):
        action = event.pattern_match.group(1)
        target_uid = int(event.pattern_match.group(2))
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        target_user = database.get_user(target_uid)
        if not target_user:
            await event.answer("❌ User not found.", alert=True)
            return
            
        _admin_action_states[user_id] = f"WAITING_FOR_EDITBAL_{action.upper()}_{target_uid}"
        
        prompt_text = (
            f"👛 <b>{'Add' if action == 'addbal' else 'Subtract'} Balance</b>\n"
            f"User ID: `{target_uid}`\n"
            f"Current Balance: <b>₹{target_user.get('wallet_balance', 0.0):.2f}</b>\n\n"
            f"Send the amount in ₹ to {'add' if action == 'addbal' else 'subtract'}:"
        )
        
        buttons = [[utils.styled_button("🔙 Cancel", f"admin_usr_stats_back_{target_uid}", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_usr_stats_back_(\d+)$"))
    async def admin_usr_stats_back_callback(event):
        target_uid = event.pattern_match.group(1)
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        await process_admin_usr_search(event, str(target_uid), "bal")

    @client.on(events.CallbackQuery(pattern="^admin_branding_settings$"))
    async def admin_branding_settings_callback(event):
        await show_branding_settings(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern=r"^admin_tgl_pay_(upi|usdt|ton)$"))
    async def admin_tgl_pay_opt_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        opt = event.pattern_match.group(1) if isinstance(event.pattern_match.group(1), str) else event.pattern_match.group(1).decode("utf-8")
        global_settings = database.get_global_settings()
        
        setting_key = f"payment_{opt}_enabled"
        # Toggle current value (default is True)
        current_val = global_settings.get(setting_key, True)
        global_settings[setting_key] = not current_val
        database.save_global_settings(global_settings)
        
        await event.answer(f"{opt.upper()} payment method {'enabled' if not current_val else 'disabled'}.", alert=True)
        await show_admin_panel(event, user_id)

    @client.on(events.CallbackQuery(pattern=r"^admin_tgl_brand_(name|bio)_opt$"))
    async def admin_toggle_branding_opt_callback(event):
        element = event.pattern_match.group(1)
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        global_settings = database.get_global_settings()
        key = f"branding_{element}_enabled"
        global_settings[key] = not global_settings.get(key, True)
        database.save_global_settings(global_settings)
        
        await show_branding_settings(event, user_id)

    @client.on(events.CallbackQuery(pattern=r"^admin_set_brand_(name|bio)_txt$"))
    async def admin_set_branding_text_callback(event):
        element = event.pattern_match.group(1)
        user_id = event.sender_id
        if not check_admin(user_id):
            return
            
        _admin_action_states[user_id] = f"WAITING_FOR_BRAND_{element.upper()}_TXT"
        
        prompt_text = (
            f"✏️ <b>Set {element.capitalize()} Branding Suffix</b>\n\n"
            f"Send the suffix text to be appended to all userbots' {element}s (or send `none` to disable suffix):\n\n"
            f"Example: ` via @BotUsername`"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_branding_settings", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)


async def show_branding_settings(event, user_id: int):
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    if not check_admin(user_id):
        await event.respond(utils.get_text("error_not_admin", lang))
        return
        
    global_settings = database.get_global_settings()
    brand_name_val = "✅ ON" if global_settings.get("branding_name_enabled", True) else "❌ OFF"
    brand_bio_val = "✅ ON" if global_settings.get("branding_bio_enabled", True) else "❌ OFF"
    name_text = global_settings.get("branding_name_text") or "Not Set (Fallback:  via @BotUsername)"
    bio_text = global_settings.get("branding_bio_text") or "Not Set (Fallback:  via @BotUsername)"
    
    text = (
        "<blockquote><b>» 🎨 Branding Configurations</b>\n\n"
        f"📛 <b>Name Branding :</b> {brand_name_val}\n"
        f"Suffix: <code>{name_text}</code>\n\n"
        f"📝 <b>Bio Branding :</b> {brand_bio_val}\n"
        f"Suffix: <code>{bio_text}</code>\n\n"
        "⚡ <i>Configure Global Branding Text Appended To Userbots.</i></blockquote>"
    )
    
    buttons = [
        [
            utils.styled_button("📛 Toggle Name Branding", "admin_tgl_brand_name_opt", style="primary"),
            utils.styled_button("📝 Toggle Bio Branding", "admin_tgl_brand_bio_opt", style="primary")
        ],
        [
            utils.styled_button("✏️ Set Name Suffix", "admin_set_brand_name_txt", style="primary"),
            utils.styled_button("✏️ Set Bio Suffix", "admin_set_brand_bio_txt", style="primary")
        ],
        [
            utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")
        ]
    ]
    
    try:
        if hasattr(event, "edit"):
            await event.edit(text, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception:
        await event.respond(text, buttons=buttons)


async def process_admin_usr_search(event, search_query: str, action: str):
    user_id = event.sender_id
    search_query = search_query.strip()
    
    # 1. Resolve target user
    target_user = None
    target_id = None
    if search_query.isdigit():
        target_id = int(search_query)
        target_user = database.get_user(target_id)
        if not target_user:
            target_user = {
                "user_id": target_id,
                "username": None,
                "first_name": "User",
                "last_name": "",
                "allowed_slots": 1,
                "wallet_balance": 0.0,
                "tos_accepted": True
            }
            database.save_user(target_user)
    else:
        username_clean = search_query.lstrip("@")
        target_user = database.get_user_by_username(username_clean)
        if target_user:
            target_id = target_user["user_id"]
        
    if not target_user or not target_id:
        buttons = [[utils.styled_button("🔙 Back To User Management", "admin_manage_users", style="primary")]]
        await event.reply("❌ <b>User not found.</b> Please verify the User ID or Username.", buttons=buttons)
        return
        
    username = target_user.get("username") or "None"
    first_name = target_user.get("first_name") or ""
    last_name = target_user.get("last_name") or ""
    
    # Execute Action
    if action == "stats":
        # Get active userbots count
        sessions = database.get_sessions(target_id)
        active_userbots = sum(1 for s in sessions if s.get("status") == "running")
        
        # Count referred users
        referred_count = database.count_referred_users(target_id)
        
        is_banned = target_user.get("is_banned", False)
        
        stats_text = (
            f"👤 <b>User Statistics Report</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 User ID: `{target_id}`\n"
            f"🔗 Username: @{username}\n"
            f"🏷️ Name: <b>{first_name} {last_name}</b>\n"
            f"🚪 TOS Accepted: <b>{'Yes' if target_user.get('tos_accepted') else 'No'}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Slots Limit: <b>{target_user.get('allowed_slots', 1)}</b>\n"
            f"👛 Wallet Balance: <b>₹{target_user.get('wallet_balance', 0.0):.2f}</b>\n"
            f"👥 Total Referred: <b>{referred_count}</b>\n"
            f"🧑‍🤝‍🧑 Referred By: `{target_user.get('referred_by') or 'Direct'}`\n"
            f"🚫 Banned: <b>{'Yes 🔴' if is_banned else 'No 🟢'}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Total Accounts: <b>{len(sessions)}</b>\n"
            f"🟢 Active Userbots: <b>{active_userbots}</b>"
        )
        
        buttons = [
            [utils.styled_button("🚫 Ban User" if not is_banned else "🟢 Unban User", f"admin_tglban_{target_id}", style="danger" if not is_banned else "success")],
            [utils.styled_button("🎮 Control Userbots", f"admin_usr_opendashtrg_{target_id}", style="success")],
            [utils.styled_button("🔙 Back To User Management", "admin_manage_users", style="primary")]
        ]
        await event.reply(stats_text, buttons=buttons)
        
    elif action == "ctrl":
        from handlers.my_bots import show_bots_list, show_all_slots_dashboard, set_admin_impersonation
        set_admin_impersonation(user_id, target_id)
        sessions = database.get_sessions(target_id)
        if sessions:
            await show_all_slots_dashboard(event, target_id, flash_message=f"👑 <b>Admin Access</b>: Controlling UserBots of User `{target_id}` (@{username})")
        else:
            await show_bots_list(event, target_id, flash_message=f"👑 <b>Admin Access</b>: Viewing Dashboard of User `{target_id}` (@{username})")
        
    elif action == "ban":
        if target_id in config.ORIGINAL_ADMIN_IDS:
            await event.reply("❌ Administrators cannot be banned.")
            return
        target_user["is_banned"] = True
        database.save_user(target_user)
        await event.reply(f"✅ <b>User Banned successfully!</b>\nUser ID: `{target_id}`\nUsername: @{username}")
        # Re-render menu
        class MockEvent:
            def __init__(self):
                self.sender_id = user_id
                self.respond = event.respond
                self.edit = event.respond
            async def answer(self, *args, **kwargs):
                pass
        await admin_manage_users_callback(MockEvent())
        
    elif action == "unban":
        target_user["is_banned"] = False
        database.save_user(target_user)
        await event.reply(f"✅ <b>User Unbanned successfully!</b>\nUser ID: `{target_id}`\nUsername: @{username}")
        # Re-render menu
        class MockEvent:
            def __init__(self):
                self.sender_id = user_id
                self.respond = event.respond
                self.edit = event.respond
            async def answer(self, *args, **kwargs):
                pass
        await admin_manage_users_callback(MockEvent())

    elif action == "bal":
        wallet_bal = target_user.get("wallet_balance", 0.0)
        bal_text = (
            f"👛 <b>User Balance Management</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 User ID: `{target_id}`\n"
            f"🔗 Username: @{username}\n"
            f"🏷️ Name: <b>{first_name} {last_name}</b>\n"
            f"👛 Current Balance: <b>₹{wallet_bal:.2f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Choose an option below to add or subtract balance:"
        )
        buttons = [
            [
                utils.styled_button("➕ Add Balance", f"admin_usr_addbal_{target_id}", style="success"),
                utils.styled_button("➖ Subtract Balance", f"admin_usr_subbal_{target_id}", style="danger")
            ],
            [utils.styled_button("🔙 Back To User Management", "admin_manage_users", style="primary")]
        ]
        await event.reply(bal_text, buttons=buttons)
