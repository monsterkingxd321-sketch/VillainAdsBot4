import logging
import asyncio
import secrets
from telethon import events
from telethon.errors import FloodWaitError
from villain_ads_bot.data import database
from villain_ads_bot import config
from villain_ads_bot.core import utils
from villain_ads_bot.core import ui
from villain_ads_bot.core import pricing
from villain_ads_bot.core import proxy as proxy_tool
from villain_ads_bot.services import proxy_mirror
from villain_ads_bot.services import proxy_rotation
from villain_ads_bot.services import proxy_channel
from villain_ads_bot.services import userbot_manager
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


# ── Role hierarchy ────────────────────────────────────────────────────────
ROLE_OWNER = database.ROLE_OWNER
ROLE_MODERATOR = database.ROLE_MODERATOR
ROLE_SUPPORT = database.ROLE_SUPPORT
ROLE_LEVELS = database.ROLE_LEVELS
ROLE_LABELS = database.ROLE_LABELS


def check_role(user_id: int, minimum: str = ROLE_SUPPORT) -> bool:
    """
    Returns True when the admin has AT LEAST the given role.

    owner > moderator > support

    The root owners from ORIGINAL_ADMIN_IDS are always owners, so nobody
    can ever lock the bot out.
    """
    if not check_admin(user_id):
        return False
    try:
        if int(user_id) in (config.ORIGINAL_ADMIN_IDS or set()):
            return True
    except (TypeError, ValueError):
        pass
    role = database.get_admin_role(user_id)
    return ROLE_LEVELS.get(role, 1) >= ROLE_LEVELS.get(minimum, 1)


def current_role(user_id: int) -> str:
    """Returns the role name of an admin."""
    try:
        if int(user_id) in (config.ORIGINAL_ADMIN_IDS or set()):
            return ROLE_OWNER
    except (TypeError, ValueError):
        pass
    return database.get_admin_role(user_id)


def _bot_username(event) -> str:
    """Returns the bot username (for deep links)."""
    try:
        return event.client.me.username or "Bot"
    except Exception:
        try:
            me = event.client.me
            return getattr(me, "username", None) or "Bot"
        except Exception:
            return "Bot"


def audit(action: str, target=None, details: str = "", actor_id: int = None):
    """Writes an entry into the audit log."""
    try:
        database.log_audit(actor_id, action, target, details)
    except Exception:
        pass

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
    
    from villain_ads_bot.core import pricing as _pricing
    text = (
        f"{ui.card_open('Admin Control Panel', 'Owner tools')}\n\n"
        f"{ui.kv('Slot price', f'₹{_pricing.get_price_per_slot():.2f}', '💸')}\n"
        f"{ui.kv('USDT rate', f'₹{_pricing.get_usdt_rate():.2f}', '🪙')}\n"
        f"{ui.kv('TON rate', f'₹{_pricing.get_ton_rate():.2f}', '💎')}\n"
        f"{ui.kv('API credentials', str(len(global_settings.get('api_credentials', []) or [])), '🔐')}\n"
        f"{ui.kv('Maintenance', 'ON' if global_settings.get('maintenance_mode', False) else 'OFF', '🛠️')}\n\n"
        f"{ui.card_close()}"
    )
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
            utils.styled_button("📤 Export All Sessions", "admin_export_all_sessions", style="success"),
            utils.styled_button("📂 Load Accounts From Session Files", "admin_load_sessions_from_disk", style="success")
        ],
        [
            utils.styled_button("🎨 Gift Slots", "admin_gift_slots", style="success"),
            utils.styled_button("📊 Set Commission", "admin_set_comm", style="primary")
        ],
        [
            utils.styled_button("💸 Set Price Per Slot (INR)", "admin_set_price", style="primary"),
            utils.styled_button("🎵 Music API Key", "admin_music_api", style="primary")
        ],
        [
            utils.styled_button("🔐 API ID / API Hash Manager", "admin_api_credentials", style="primary")
        ],
        [
            utils.styled_button("🎟️ Coupons", "admin_coupons", style="success"),
            utils.styled_button("🧙 Manage Admin Roles", "admin_roles", style="primary")
        ],
        [
            utils.styled_button("🌐 Proxy Manager (Unlimited Accounts)", "admin_proxies", style="success"),
            utils.styled_button("💾 Unlimited Accounts", "admin_toggle_unlimited_accounts", style="primary")
        ],
        [
            utils.styled_button("📡 Proxy Source Channel", "admin_proxy_channel", style="success")
        ],
        [
            utils.styled_button("💾 Backup Now", "admin_backup_now", style="success"),
            utils.styled_button("🔄 Restore Backup", "admin_restore_backup", style="primary")
        ],
        [
            utils.styled_button("🧾 Audit Log", "admin_audit_log", style="primary")
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

async def _test_music_api() -> tuple:
    """Performs a tiny health check against the configured music gateway."""
    try:
        import aiohttp
        from villain_ads_bot.core import music as _music

        url, key, _backup = _music.apply_configured_credentials()
        if not key:
            return False, "No music API key is configured"
        test_url = f"{url}/info/dQw4w9WgXcQ?key={key}"
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession() as session:
            async with session.get(test_url, timeout=timeout) as resp:
                if resp.status == 200:
                    return True, f"Music API is ONLINE ({url})"
                return False, f"Music API returned HTTP {resp.status}"
    except Exception as e:
        return False, f"Music API test failed: {e}"


def register_handlers(client):
    
    # ------------------ Navigation ------------------
    @client.on(events.NewMessage(pattern="/admin"))
    async def admin_cmd(event):
        if not event.is_private:
            return
        from villain_ads_bot.core import utils
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
            
            # 1. CPU Usage (short sample so the very first reading is correct)
            try:
                cpu_pct = psutil.cpu_percent(interval=0.2)
            except Exception:
                cpu_pct = 0.0
            
            # Effective CPU cores (respects a container / cgroup CPU quota)
            cpu_cores = psutil.cpu_count(logical=True) or 1
            quota_cores = None
            try:
                if os.path.exists("/sys/fs/cgroup/cpu.max"):
                    with open("/sys/fs/cgroup/cpu.max", "r") as f:
                        parts = f.read().split()
                    if len(parts) == 2 and parts[0] != "max":
                        quota_cores = max(1.0, int(parts[0]) / int(parts[1]))
                elif os.path.exists("/sys/fs/cgroup/cpu/cpu.cfs_quota_us") and os.path.exists("/sys/fs/cgroup/cpu/cpu.cfs_period_us"):
                    with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "r") as f:
                        quota = int(f.read().strip())
                    with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us", "r") as f:
                        period = int(f.read().strip())
                    if quota > 0 and period > 0:
                        quota_cores = max(1.0, quota / period)
            except Exception:
                quota_cores = None
            if quota_cores:
                cpu_cores = max(1, int(round(quota_cores)))
            
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
                    # memory.current also counts the page cache -> read the real
                    # (anonymous) memory usage from memory.stat when possible.
                    anon = None
                    if os.path.exists("/sys/fs/cgroup/memory.stat"):
                        try:
                            with open("/sys/fs/cgroup/memory.stat", "r") as f:
                                for line in f:
                                    if line.startswith("anon "):
                                        anon = int(line.split()[1])
                                        break
                        except Exception:
                            anon = None
                    if anon is not None:
                        cgroup_mem_usage = anon
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
                    # v1: exclude the page cache as well
                    if os.path.exists("/sys/fs/cgroup/memory/memory.stat"):
                        try:
                            rss = None
                            with open("/sys/fs/cgroup/memory/memory.stat", "r") as f:
                                for line in f:
                                    if line.startswith("rss "):
                                        rss = int(line.split()[1])
                                        break
                            if rss is not None:
                                cgroup_mem_usage = rss
                        except Exception:
                            pass
                except Exception:
                    pass

            sys_mem = psutil.virtual_memory()
            
            if cgroup_mem_limit and cgroup_mem_limit < sys_mem.total:
                total_ram_mb = cgroup_mem_limit // (1024 ** 2)
                used_ram_mb = (cgroup_mem_usage or 0) // (1024 ** 2)
                ram_pct = round((used_ram_mb / total_ram_mb) * 100, 1) if total_ram_mb else sys_mem.percent
            else:
                total_ram_mb = sys_mem.total // (1024 ** 2)
                # Use a consistent numerator/denominator pair so the percentage
                # always matches the displayed MB values.
                used_ram_mb = sys_mem.total // (1024 ** 2) - sys_mem.available // (1024 ** 2)
                ram_pct = round((sys_mem.total - sys_mem.available) / sys_mem.total * 100, 1) if sys_mem.total else sys_mem.percent

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

            # 3. Disk Usage - measure the disk the bot actually lives on
            disk_path = os.path.dirname(os.path.abspath(__file__))
            disk_path = os.path.dirname(disk_path) if os.path.basename(disk_path) == "handlers" else disk_path
            try:
                disk = psutil.disk_usage(disk_path)
                disk_pct = disk.percent
                disk_used_gb = round(disk.used / (1024 ** 3), 1)
                disk_total_gb = round(disk.total / (1024 ** 3), 1)
            except Exception:
                disk_pct, disk_used_gb, disk_total_gb = 0, 0, 0

            # Downloads folder size (resolved relative to the bot root)
            dl_mb = 0
            downloads_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
            if os.path.exists(downloads_dir):
                try:
                    for root, dirs, files in os.walk(downloads_dir):
                        for f in files:
                            try:
                                dl_mb += os.path.getsize(os.path.join(root, f))
                            except Exception:
                                pass
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
            from villain_ads_bot.services import userbot_manager
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
                f"🧵 <b>System Threads :</b> <code>{threads_count} Active</code>\n"
                f"📂 <b>Disk Path :</b> <code>{disk_path}</code></blockquote>"
            )
            
            buttons = [
                [utils.styled_button("🔄 Refresh", "admin_vps_usage", style="primary")],
                [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="danger")]
            ]
            
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception as e:
            logger.error(f"VPS Usage error: {e}")
            utils.report_error("admin.vps_usage", e)
            await event.answer("Failed to fetch VPS stats. The error was reported to the log group.", alert=True)

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
        
        from villain_ads_bot.services import userbot_manager
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
        from villain_ads_bot.services import userbot_manager
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
        from villain_ads_bot.handlers.my_bots import show_all_slots_dashboard, set_admin_impersonation
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

    @client.on(events.CallbackQuery(pattern=r"^admin_(set_(price|fj|lg|bu|bd|imgs|upi|usdt|ton|usdt_rate|ton_rate|ub_joins|comm|music_key|music_url|music_backup_url)|join_all_sessions|add_admin|rem_admin|add_api_cred)$"))
    async def admin_setting_callback(event):
        action = event.pattern_match.group(1)
        user_id = event.sender_id
        user = database.get_user(user_id)
        lang = user.get("language", "en") if user else "en"
        
        if not check_admin(user_id):
            await event.respond(utils.get_text("error_not_admin", lang))
            return

        # Pricing, branding, API keys and backups are owner-only.
        if action in ("set_price", "set_upi", "set_usdt", "set_ton",
                      "set_usdt_rate", "set_ton_rate", "set_bu", "set_bd",
                      "set_music_key", "set_music_url", "set_music_backup_url",
                      "add_api_cred"):
            if not check_role(user_id, ROLE_OWNER):
                await event.respond("\u274c <b>Only the owner can change this setting.</b>", parse_mode="html")
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
            "rem_admin": "prompt_rem_admin",
            "set_price": "prompt_set_price",
            "set_music_key": "prompt_set_music_key",
            "add_api_cred": "prompt_add_api_cred"
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
            prompt_text = "<blockquote><b>📊 Send The New Referral Commission Rate</b> (0.01 - 0.99 for 1%-99%):</blockquote>"
        elif action == "set_price":
            prompt_text = (
                f"{ui.card('Set Price Per Slot', '', 'The single source of truth for every slot price', '💸')}\n\n"
                f"{ui.kv('Current price', f'₹{pricing.get_price_per_slot():.2f}', '💸')}\n"
                f"{ui.kv('USDT rate', f'₹{pricing.get_usdt_rate():.2f} / USDT', '🪙')}\n"
                f"{ui.kv('TON rate', f'₹{pricing.get_ton_rate():.2f} / TON', '💎')}\n\n"
                f"{ui.help_hint('Send the new price of ONE slot in INR (e.g. 25).')}\n"
                f"{ui.help_hint('USDT and TON amounts are calculated automatically from the rates above.')}"
            )
        elif action == "set_music_key":
            prompt_text = (
                f"{ui.card('Set Music API Key', '', 'Yuki / Meow music gateway credentials', '🎵')}\n\n"
                f"{ui.help_hint('Send the new API key (or the word none to clear it).')}\n"
                f"{ui.help_hint('Example : yuki_8641adadf64870f3065dd502c8ee30f1')}"
            )
        elif action == "add_api_cred":
            prompt_text = (
                f"{ui.card('Add API Credentials', '', 'Add an extra Telegram API_ID / API_HASH pair', '🔐')}\n\n"
                f"{ui.help_hint('Send them in the format:')}\n"
                f"<code>12345678:0123456789abcdef0123456789abcdef</code>\n\n"
                f"{ui.help_hint('The pair is used round-robin so your accounts never hit a flood-wait.')}"
            )
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
        from villain_ads_bot.services import userbot_manager
        from telethon.errors import (
            FloodWaitError, ChatWriteForbiddenError, UserBannedInChannelError,
            ChatAdminRequiredError, ChannelPrivateError, SlowModeWaitError
        )
        import asyncio
        import time
        import os

        total_bots = len(active_sessions)
        stats = {
            "groups": 0,
            "dms": 0,
            "failed": 0,
            "bots_done": 0,
            "active_running": 0
        }
        lock = asyncio.Lock()
        media_path = None

        try:
            # Download media once to disk if the broadcast message has media
            if broadcast_msg.media:
                try:
                    media_path = await client.download_media(broadcast_msg)
                    logger.info(f"Downloaded broadcast media to {media_path}")
                except Exception as dl_err:
                    logger.error(f"Failed to download broadcast media: {dl_err}")

            # Independent background reporter to update progress every 3.5 seconds
            # This completely avoids main bot client contention and eliminates message edit flood limits
            stop_reporter = asyncio.Event()

            async def _reporter_loop():
                while not stop_reporter.is_set():
                    try:
                        await asyncio.sleep(3.5)
                        if stop_reporter.is_set():
                            break
                        async with lock:
                            done_cnt = stats["bots_done"]
                            active_cnt = stats["active_running"]
                            grp_cnt = stats["groups"]
                            dm_cnt = stats["dms"]
                            err_cnt = stats["failed"]
                        try:
                            await prog_msg.edit(
                                f"<blockquote><b>» ⏳ GLOBAL USERBOT BROADCAST IN PROGRESS</b>\n\n"
                                f"🎯 <b>Target:</b> <b>{target_title}</b>\n"
                                f"⚡ <b>Mode:</b> <code>⚡ Parallel Execution</code>\n"
                                f"🤖 <b>UserBots Active:</b> <code>[{done_cnt} / {total_bots}] ({active_cnt} broadcasting)</code>\n"
                                f"👥 <b>Groups Sent:</b> <code>{grp_cnt}</code>\n"
                                f"👤 <b>DMs Sent:</b> <code>{dm_cnt}</code>\n"
                                f"⚠️ <b>Skipped / Errors:</b> <code>{err_cnt}</code>\n\n"
                                f"⚡ <i>All UserBots broadcasting concurrently in parallel...</i></blockquote>"
                            )
                        except Exception:
                            pass
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        pass

            reporter_task = asyncio.create_task(_reporter_loop())

            # Semaphore to control parallel concurrent userbots (up to 10 userbots broadcasting at once)
            ub_sem = asyncio.Semaphore(10)

            async def _broadcast_single_bot(sess):
                sess_id = sess.get("session_id") or sess.get("phone")
                phone_num = sess.get("phone") or sess_id

                async with ub_sem:
                    async with lock:
                        stats["active_running"] += 1

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
                            async with lock:
                                stats["failed"] += 1
                            return

                        ub_client = bot_obj.client
                        if not ub_client.is_connected():
                            try:
                                await asyncio.wait_for(ub_client.connect(), timeout=10.0)
                            except Exception as conn_err:
                                logger.error(f"Userbot {phone_num} connect error: {conn_err}")
                                async with lock:
                                    stats["failed"] += 1
                                return

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

                                async with lock:
                                    if is_grp:
                                        stats["groups"] += 1
                                    else:
                                        stats["dms"] += 1

                                # Paced delay between sends for this userbot to keep individual account safe
                                await asyncio.sleep(1.2)

                            except FloodWaitError as fwe:
                                if fwe.seconds <= 20:
                                    await asyncio.sleep(fwe.seconds + 1)
                                else:
                                    logger.warning(f"Long FloodWait ({fwe.seconds}s) on userbot {phone_num}, finishing for this bot...")
                                    async with lock:
                                        stats["failed"] += 1
                                    break
                            except (ChatWriteForbiddenError, UserBannedInChannelError, ChatAdminRequiredError, ChannelPrivateError, SlowModeWaitError):
                                async with lock:
                                    stats["failed"] += 1
                            except Exception as send_err:
                                logger.debug(f"Failed to send from {phone_num} to {dialog.id}: {send_err}")
                                async with lock:
                                    stats["failed"] += 1

                    except Exception as bot_err:
                        logger.error(f"Error processing broadcast for bot {phone_num}: {bot_err}")
                        async with lock:
                            stats["failed"] += 1
                    finally:
                        async with lock:
                            stats["bots_done"] += 1
                            stats["active_running"] -= 1

            # Launch all userbot broadcast coroutines concurrently in parallel!
            bot_tasks = [asyncio.create_task(_broadcast_single_bot(s)) for s in active_sessions]
            if bot_tasks:
                await asyncio.gather(*bot_tasks, return_exceptions=True)

            stop_reporter.set()
            reporter_task.cancel()

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
            f"⚡ <b>Execution Mode:</b> <b>Parallel Concurrency</b>\n"
            f"🤖 <b>UserBots Used:</b> <b>{stats['bots_done']} / {total_bots}</b>\n"
            f"👥 <b>Groups Delivered:</b> <b>{stats['groups']}</b>\n"
            f"👤 <b>DMs Delivered:</b> <b>{stats['dms']}</b>\n"
            f"⚠️ <b>Errors / Skipped:</b> <b>{stats['failed']}</b>\n"
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
                        audit("user.gift_slots", str(target_uid), f"+{slots_to_gift} slots", user_id)
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
                buttons = [
                    [utils.styled_button("♾ Unlimited Slots (No ID Limit)", "admin_plan_unlimited_opt", style="success")],
                    [utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]
                ]
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
                
            # 0.1.b.2 An unlimited plan (no slot limit at all)
            elif action == "WAITING_FOR_PLAN_UNLIMITED":
                _admin_plan_temp.setdefault(user_id, {})["slots"] = 0
                _admin_plan_temp.setdefault(user_id, {})["unlimited"] = True
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
                unlimited = bool(temp_data.get("unlimited", False))
                
                import uuid
                plan_id = "plan_" + str(uuid.uuid4())[:6]
                
                plans = global_settings.setdefault("subscription_plans", [])
                plans.append({
                    "id": plan_id,
                    "days": days,
                    "slots": slots,
                    "unlimited": unlimited,
                    "price": price,
                    "price_per_slot": temp_data.get("price_per_slot") or price,
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

            # ── #2  COUPON CREATION FLOW (3 steps) ──
            elif action == "WAITING_FOR_COUPON_AMOUNT":
                try:
                    amount = float(val_str)
                    if amount <= 0:
                        raise ValueError
                except ValueError:
                    await event.reply("❌ <b>Invalid amount.</b>\n\n<i>Send a positive number (e.g. 50).</i>", parse_mode="html")
                    return
                _admin_plan_temp.setdefault(user_id, {})["coupon_amount"] = amount
                _admin_action_states[user_id] = "WAITING_FOR_COUPON_USES"
                await event.reply(
                    f"🏷️ <b>Coupon : Step 2 of 3</b>\n\n"
                    f"💰 <b>Amount :</b> ₹{amount:.2f}\n\n"
                    f"👥 <i>How many times may this coupon be used?</i>\n"
                    f"<i>Send a number (e.g. 10).</i>",
                    parse_mode="html"
                )
                return

            elif action == "WAITING_FOR_COUPON_USES":
                try:
                    max_uses = int(val_str)
                    if max_uses < 1:
                        raise ValueError
                except ValueError:
                    await event.reply("❌ <b>Invalid number of uses.</b>", parse_mode="html")
                    return
                temp = _admin_plan_temp.setdefault(user_id, {})
                temp["coupon_max_uses"] = max_uses
                _admin_action_states[user_id] = "WAITING_FOR_COUPON_EXPIRY"
                await event.reply(
                    f"🏷️ <b>Coupon : Step 3 of 3</b>\n\n"
                    f"💰 <b>Amount :</b> ₹{temp.get('coupon_amount', 0):.2f}\n"
                    f"👥 <b>Max uses :</b> {max_uses}\n\n"
                    f"📅 <i>After how many days should it expire? Send 0 for never.</i>",
                    parse_mode="html"
                )
                return

            elif action == "WAITING_FOR_COUPON_EXPIRY":
                try:
                    days = int(val_str)
                    if days < 0:
                        raise ValueError
                except ValueError:
                    await event.reply("❌ <b>Invalid number of days.</b>", parse_mode="html")
                    return
                temp = _admin_plan_temp.setdefault(user_id, {})
                amount = float(temp.get("coupon_amount", 0) or 0)
                max_uses = int(temp.get("coupon_max_uses", 1) or 1)
                code = "SLOT-" + secrets.token_hex(4).upper()
                import time as _time
                coupon = {
                    "code": code,
                    "amount_inr": amount,
                    "max_uses": max_uses,
                    "current_uses": 0,
                    "is_active": True,
                    "created_at": _time.time(),
                    "created_by": user_id,
                    "expires_at": (_time.time() + days * 86400) if days else None,
                    "max_per_user": 1,
                }
                database.save_coupon(coupon)
                audit("coupon.create", code, f"amount={amount} uses={max_uses} days={days}", user_id)
                _admin_plan_temp.pop(user_id, None)

                deep_link = f"https://t.me/{_bot_username(event)}?start=coupon_{code}"
                await event.reply(
                    f"✅ <b>Coupon created!</b>\n\n"
                    f"🏷️ <b>Code :</b> <code>{code}</code>\n"
                    f"💰 <b>Amount :</b> ₹{amount:.2f}\n"
                    f"👥 <b>Max uses :</b> {max_uses}\n"
                    f"📅 <b>Expires :</b> {'never' if not days else str(days) + ' days'}\n\n"
                    f"🔗 <b>One-tap redeem link :</b>\n<code>{deep_link}</code>\n\n"
                    f"💡 <i>Share this link — the user only has to tap it and the credit is added to their wallet.</i>",
                    parse_mode="html"
                )
                return

            # ── #11  BACKUP RESTORE (file upload) ──
            elif action == "WAITING_FOR_BACKUP_FILE":
                if not event.media:
                    await event.reply("❌ <b>Please upload the backup zip file.</b>", parse_mode="html")
                    return
                try:
                    data = await event.client.download_media(event.media, file=bytes)
                except Exception as e:
                    logger.error(f"Could not download the backup file: {e}")
                    utils.report_error("admin_restore_download", e)
                    await event.reply("❌ <b>Could not read the file.</b>", parse_mode="html")
                    return

                from villain_ads_bot.core import backup as _backup
                preview = _backup.inspect_backup(data)
                if not preview.get("ok"):
                    await event.reply(f"❌ <b>{preview.get('error') or 'Invalid backup file.'}</b>", parse_mode="html")
                    return

                counts = preview.get("counts", {})
                _admin_plan_temp.setdefault(user_id, {})["backup_bytes"] = data
                _admin_action_states[user_id] = "WAITING_FOR_BACKUP_CONFIRM"
                await event.reply(
                    f"🔎 <b>Backup preview</b>\n\n"
                    f"💰 <b>Created :</b> <code>{(preview.get('manifest') or {}).get('created_at', '?')}</code>\n"
                    f"👥 <b>Users :</b> <code>{counts.get('users', 0)}</code>\n"
                    f"📜 <b>Sessions :</b> <code>{counts.get('sessions', 0)}</code>\n"
                    f"🎫 <b>Session files :</b> <code>{counts.get('session_files', 0)}</code>\n"
                    f"🎟️ <b>Coupons :</b> <code>{counts.get('coupons', 0)}</code>\n"
                    f"⚙️ <b>Settings :</b> <code>{'yes' if counts.get('has_settings') else 'no'}</code>\n\n"
                    f"⚠️ <b>Restoring overwrites the current data.</b>\n"
                    f"👉 <i>Send <b>CONFIRM</b> to restore, or /cancel to abort.</i>",
                    parse_mode="html"
                )
                return

            elif action == "WAITING_FOR_BACKUP_CONFIRM":
                if val_str.upper() not in ("CONFIRM", "YES", "RESTORE"):
                    await event.reply("❌ <b>Aborted.</b>\n\n<i>Send CONFIRM to restore the backup.</i>", parse_mode="html")
                    _admin_plan_temp.pop(user_id, None)
                    return
                data = _admin_plan_temp.get(user_id, {}).pop("backup_bytes", None)
                if not data:
                    await event.reply("❌ <b>The backup expired. Please upload it again.</b>", parse_mode="html")
                    return
                from villain_ads_bot.core import backup as _backup
                result = _backup.restore_backup(data)
                audit("backup.restore", None,
                      f"users={result.get('users')} sessions={result.get('sessions')} coupons={result.get('coupons')}",
                      user_id)
                if not result.get("ok"):
                    await event.reply(f"❌ <b>Restore failed :</b> {result.get('error')}", parse_mode="html")
                    return
                await event.reply(
                    f"✅ <b>Backup restored!</b>\n\n"
                    f"👥 <b>Users :</b> <code>{result.get('users', 0)}</code>\n"
                    f"📜 <b>Sessions :</b> <code>{result.get('sessions', 0)}</code>\n"
                    f"🎫 <b>Coupons :</b> <code>{result.get('coupons', 0)}</code>\n"
                    f"💽 <b>Session files written :</b> <code>{result.get('session_files', 0)}</code>\n"
                    f"🔐 <b>Settings :</b> <code>{'restored' if result.get('settings') else 'skipped'}</code>\n\n"
                    f"⚠️ <i>Restart the bot so every restored session is picked up.</i>",
                    parse_mode="html"
                )
                return

            # ── The source channel of the proxies ──
            elif action == "WAITING_FOR_PROXY_SOURCE":
                ok, msg = proxy_tool.set_channel(val_str)
                audit("proxy.source_channel", None, msg, user_id)
                await event.reply(msg, parse_mode="html")
                await admin_proxy_mirror_callback(event)
                return

            # ── The mirror channel (the owner's own channel) ──
            elif action == "WAITING_FOR_PROXY_MIRROR":
                ok, msg = proxy_mirror.set_mirror_channel(val_str)
                audit("proxy.mirror_channel", None, msg, user_id)
                await event.reply(msg, parse_mode="html")
                await admin_proxy_mirror_callback(event)
                return

            # ── Add a proxy to the pool ──
            elif action == "WAITING_FOR_PROXY":
                raw = val_str.strip()
                if raw in ("\u271d", "remove", "delete"):
                    pool = proxy_tool.get_pool()
                    if not pool:
                        await event.reply("\u2139\ufe0f The proxy pool is already empty.", parse_mode="html")
                        return
                    ok, msg = proxy_tool.remove_proxy(len(pool) - 1)
                else:
                    ok, msg = proxy_tool.add_proxy(raw)
                audit("proxy.add", None, msg, user_id)
                await event.reply(msg, parse_mode="html")
                class _MockProxyEvent:
                    def __init__(self, uid, ev):
                        self.sender_id = uid
                        self.respond = ev.respond
                        self.edit = ev.respond
                    async def answer(self, *args, **kwargs):
                        pass
                await admin_proxies_callback(_MockProxyEvent(user_id, event))
                return

            # 1. Set the global price per slot (INR) - USDT/TON are derived
            elif action == "WAITING_FOR_SET_PRICE":
                new_price = float(val_str)
                if new_price <= 0:
                    raise ValueError
                global_settings["price_per_id"] = new_price
                global_settings["price_per_slot"] = new_price
                success = True

            # 1.b Set the music (Yuki / Meow) API key
            elif action == "WAITING_FOR_SET_MUSIC_KEY":
                if val_str.lower() == "none":
                    global_settings["music_api_key"] = None
                else:
                    global_settings["music_api_key"] = val_str.strip()
                success = True

            # 1.c Add a new API_ID / API_HASH pair
            elif action == "WAITING_FOR_ADD_API_CRED":
                raw = val_str.replace(" ", "")
                if ":" not in raw:
                    raise ValueError
                api_id_part, api_hash_part = raw.split(":", 1)
                if not api_id_part.isdigit() or len(api_hash_part) < 16:
                    raise ValueError
                creds = list(global_settings.get("api_credentials", []) or [])
                creds = [c for c in creds if str(c.get("api_id")) != api_id_part]
                creds.append({"api_id": int(api_id_part), "api_hash": api_hash_part})
                global_settings["api_credentials"] = creds
                success = True

            # 1.d Set the primary / backup music gateway urls
            elif action == "WAITING_FOR_SET_MUSIC_URL":
                url = val_str.strip().rstrip("/")
                if not url.startswith("http"):
                    raise ValueError
                global_settings["music_api_url"] = url
                success = True

            elif action == "WAITING_FOR_SET_MUSIC_BACKUP_URL":
                url = val_str.strip().rstrip("/")
                if not url.startswith("http"):
                    raise ValueError
                global_settings["music_backup_api_url"] = url
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
                
            # Set branding username (from the branding screen)
            elif action == "WAITING_FOR_BRAND_USERNAME_TXT":
                if val_str.lower() == "none":
                    global_settings["branding_username"] = None
                else:
                    global_settings["branding_username"] = val_str.replace("@", "").strip()
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
                
                from villain_ads_bot.services.userbot import join_channel_single, leave_channel_single
                from villain_ads_bot.services import userbot_manager
                
                async def _sys_action_one_db_account(s):
                    phone_num = s["phone"]
                    if not userbot_manager.is_bot_running(phone_num):
                        await userbot_manager.start_userbot(phone_num)
                    
                    bot_obj = userbot_manager._running_bots.get(phone_num)
                    if not bot_obj or not bot_obj.client:
                        return False
                        
                    if action == "WAITING_FOR_SYS_JOIN_GRP":
                        try:
                            ok = await join_channel_single(bot_obj.client, link)
                        except Exception as join_exc:
                            # A flood limit on one account must not break the
                            # whole system action - the account waits and retries.
                            seconds = getattr(join_exc, "seconds", 0) or 0
                            if seconds:
                                seconds = min(int(seconds), 900)
                                logger.warning(
                                    f"Flood wait of {seconds}s on {phone_num} - waiting."
                                )
                                await asyncio.sleep(seconds)
                                ok = await join_channel_single(bot_obj.client, link)
                            else:
                                raise
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
                    if not total_users:
                        try:
                            await prog_msg.edit("❌ No users found in database to broadcast.")
                        except Exception:
                            pass
                        return

                    stats = {
                        "success": 0,
                        "failed": 0,
                        "processed": 0
                    }
                    lock = asyncio.Lock()
                    stop_reporter = asyncio.Event()

                    # Queue-based worker pool (parallel, but strictly rate-limited so bot never freezes)
                    queue = asyncio.Queue()
                    for u in all_users:
                        queue.put_nowait(u)

                    NUM_WORKERS = 4  # 4 parallel workers
                    # 4 workers with 0.15s sleep between sends = ~20 msgs/second max (safe under Telegram 30/s limit)

                    async def _worker():
                        while not queue.empty():
                            try:
                                u = queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break

                            uid = u.get("user_id")
                            if not uid:
                                async with lock:
                                    stats["failed"] += 1
                                    stats["processed"] += 1
                                queue.task_done()
                                continue

                            target_peer = None
                            # 1. Stored access_hash is fastest (0 network roundtrips)
                            if u.get("access_hash"):
                                try:
                                    from telethon.tl.types import InputPeerUser
                                    target_peer = InputPeerUser(int(uid), int(u["access_hash"]))
                                except Exception:
                                    pass

                            # 2. Try Telethon internal cache / entity lookup safely
                            if not target_peer:
                                try:
                                    target_peer = await asyncio.wait_for(client.get_input_entity(int(uid)), timeout=2.0)
                                except Exception:
                                    if u.get("username"):
                                        try:
                                            target_peer = await asyncio.wait_for(client.get_input_entity(u["username"]), timeout=2.0)
                                        except Exception:
                                            pass

                            if not target_peer:
                                async with lock:
                                    stats["failed"] += 1
                                    stats["processed"] += 1
                                queue.task_done()
                                continue

                            sent = False
                            try:
                                await client.send_message(target_peer, broadcast_message)
                                sent = True
                            except FloodWaitError as fwe:
                                wait_time = min(fwe.seconds, 20)
                                logger.warning(f"Broadcast flood wait {wait_time}s on bot token, pausing worker...")
                                await asyncio.sleep(wait_time)
                                try:
                                    await client.send_message(target_peer, broadcast_message)
                                    sent = True
                                except Exception:
                                    pass
                            except Exception as err:
                                logger.debug(f"Failed to send broadcast to user {uid}: {err}")

                            async with lock:
                                if sent:
                                    stats["success"] += 1
                                else:
                                    stats["failed"] += 1
                                stats["processed"] += 1
                            queue.task_done()

                            # Delay to maintain smooth rate limiting and keep event loop free
                            await asyncio.sleep(0.15)

                    # Isolated progress updater loop
                    async def _reporter_loop():
                        while not stop_reporter.is_set():
                            try:
                                await asyncio.sleep(3.5)
                                if stop_reporter.is_set():
                                    break
                                async with lock:
                                    p_cnt = stats["processed"]
                                    s_cnt = stats["success"]
                                    f_cnt = stats["failed"]
                                await prog_msg.edit(
                                    f"<blockquote><b>» 📢 MAIN BOT BROADCAST IN PROGRESS</b>\n\n"
                                    f"👥 <b>Processed:</b> <code>[{p_cnt} / {total_users}]</code>\n"
                                    f"✅ <b>Delivered:</b> <code>{s_cnt}</code>\n"
                                    f"❌ <b>Failed / Skipped:</b> <code>{f_cnt}</code>\n\n"
                                    f"⚡ <i>Broadcasting in background in parallel without blocking bot...</i></blockquote>"
                                )
                            except asyncio.CancelledError:
                                break
                            except Exception:
                                pass

                    reporter_task = asyncio.create_task(_reporter_loop())
                    workers = [asyncio.create_task(_worker()) for _ in range(NUM_WORKERS)]
                    
                    await asyncio.gather(*workers, return_exceptions=True)
                    stop_reporter.set()
                    reporter_task.cancel()

                    async with lock:
                        s_cnt = stats["success"]
                        f_cnt = stats["failed"]

                    report = (
                        f"<blockquote><b>» 📢 MAIN BOT BROADCAST COMPLETED</b>\n\n"
                        f"👥 <b>Total Target Users:</b> <b>{total_users}</b>\n"
                        f"✅ <b>Successfully Sent:</b> <b>{s_cnt}</b>\n"
                        f"❌ <b>Failed (Blocked/Deleted/Unresolved):</b> <b>{f_cnt}</b>\n"
                        f"⚡ <b>Status:</b> <b>Completed Smoothly in Parallel</b></blockquote>"
                    )
                    try:
                        await prog_msg.edit(report)
                    except Exception:
                        try:
                            await event.reply(report)
                        except Exception:
                            pass
                        
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
            # Everything that changes the global configuration is audited.
            audit(f"settings.{action.lower()}", None, str(global_settings.get(action.lower(), ""))[:200], user_id)
            
            # A new music gateway configuration must take effect immediately.
            if action in ("WAITING_FOR_SET_MUSIC_KEY", "WAITING_FOR_SET_MUSIC_URL",
                          "WAITING_FOR_SET_MUSIC_BACKUP_URL"):
                try:
                    from villain_ads_bot.core import music as _music
                    _music.apply_configured_credentials()
                except Exception as m_err:
                    logger.warning(f"Could not apply the music credentials: {m_err}")
                    
            if action in ("WAITING_FOR_SET_BU", "WAITING_FOR_BRAND_NAME_TXT", "WAITING_FOR_BRAND_BIO_TXT", "WAITING_FOR_BRAND_USERNAME_TXT"):
                from villain_ads_bot.services import userbot_manager
                client.loop.create_task(userbot_manager.broadcast_branding_update())
                
            await event.reply(utils.get_text("admin_updated", lang))
            if action in ("WAITING_FOR_BRAND_NAME_TXT", "WAITING_FOR_BRAND_BIO_TXT", "WAITING_FOR_BRAND_USERNAME_TXT"):
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
        
        from villain_ads_bot.handlers.my_bots import show_all_slots_dashboard, set_admin_impersonation
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
        if not check_role(event.sender_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can change the branding.", alert=True)
            return
        await show_branding_settings(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern="^admin_load_sessions_from_disk$"))
    async def admin_load_sessions_from_disk_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return

        await event.answer("📂 Scanning session files...", alert=True)
        from villain_ads_bot.services import userbot_manager
        try:
            files = userbot_manager.list_session_files()
            imported, skipped, records = userbot_manager.import_sessions_from_disk()
            known = database.get_sessions()
            lines = [
                "<blockquote><b>» 📂 Session Files On Disk</b>\n",
                f"📁 <b>Files Found :</b> <code>{len(files)}</code>\n",
                f"🆕 <b>Newly Registered :</b> <code>{imported}</code>\n",
                f"♻️ <b>Already Known :</b> <code>{skipped}</code>\n",
                f"🗄️ <b>Total Accounts In DB :</b> <code>{len(known)}</code>\n",
            ]
            if files:
                lines.append("\n<b>Accounts :</b>")
                for f in files[:60]:
                    owner = f.get("owner_id")
                    dc = f.get("dc_id")
                    key = "✅" if f.get("has_auth_key") else "⚠️"
                    lines.append(f"\n{key} <code>{f['session_id']}</code> <i>(owner {owner} | DC {dc})</i>")
                if len(files) > 60:
                    lines.append(f"\n<i>…and {len(files) - 60} more</i>")
            lines.append("\n\n💡 <i>Use <b>🚀 Apply Branding</b> / <b>Start</b> to log these accounts in.</i></blockquote>")

            buttons = [
                [utils.styled_button("🔑 Login All Accounts From Disk", "admin_login_sessions_from_disk", style="success")],
                [utils.styled_button("🔄 Refresh", "admin_load_sessions_from_disk", style="primary")],
                [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="danger")]
            ]
            await event.edit("\n".join(lines), buttons=buttons, parse_mode="html")
        except Exception as e:
            logger.error(f"Load sessions from disk failed: {e}")
            utils.report_error("admin_load_sessions_from_disk", e)
            await event.answer("❌ Failed to scan the session files.", alert=True)

    @client.on(events.CallbackQuery(pattern="^admin_login_sessions_from_disk$"))
    async def admin_login_sessions_from_disk_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return

        status_msg = await event.edit("<blockquote><b>» 🔑 Logging In All Accounts From Session Files...</b>\n\n<i>Please wait, this can take a moment.</i></blockquote>", parse_mode="html")
        from villain_ads_bot.services import userbot_manager
        try:
            result = await userbot_manager.login_all_sessions_from_disk()
            lines = [
                "<blockquote><b>» 🔑 Session Login Results</b>\n",
                f"🆕 <b>Newly Registered :</b> <code>{result['imported']}</code>\n",
                f"♻️ <b>Already Known :</b> <code>{result['already_known']}</code>\n",
                f"✅ <b>Logged In (Running) :</b> <code>{result['started']}</code>\n",
                f"❌ <b>Failed :</b> <code>{result['failed']}</code>\n",
                f"📁 <b>Total Session Files :</b> <code>{result['total']}</code>\n",
            ]
            if result.get("details"):
                lines.append("\n<b>Details :</b>")
                for sid, note in result["details"][:60]:
                    lines.append(f"\n• <code>{sid}</code> — <i>{note}</i>")
            lines.append("</blockquote>")
            buttons = [
                [utils.styled_button("🔄 Refresh", "admin_load_sessions_from_disk", style="primary")],
                [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="danger")]
            ]
            await event.edit("\n".join(lines), buttons=buttons, parse_mode="html")
        except Exception as e:
            logger.error(f"Login all sessions from disk failed: {e}")
            utils.report_error("admin_login_sessions_from_disk", e)
            try:
                await status_msg.edit("<blockquote><b>» ❌ Login Failed</b>\n\n⚠️ <i>The error was reported to the admin team.</i></blockquote>", parse_mode="html")
            except Exception:
                pass

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
            await event.answer("❌ Access denied.", alert=True)
            return
        
        # Answer immediately so button doesn't feel frozen
        await event.answer()
            
        global_settings = database.get_global_settings()
        key = f"branding_{element}_enabled"
        global_settings[key] = not global_settings.get(key, True)
        database.save_global_settings(global_settings)
        
        from villain_ads_bot.services import userbot_manager
        client.loop.create_task(userbot_manager.broadcast_branding_update())
        
        await show_branding_settings(event, user_id)

    # ==================== PROXY CHANNEL READER ====================
    @client.on(events.CallbackQuery(pattern="^admin_proxy_channel$"))
    async def admin_proxy_channel_callback(event):
        """
        The proxy source channel.

        The owner does not need a channel of their own: one of their
        accounts joins the proxy channel and reads the proxies from there.
        """
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can change the proxy source.", alert=True)
            return

        source = proxy_tool.get_channel()
        reader = proxy_channel.get_reader_account()
        pool = proxy_tool.get_pool()
        free = proxy_rotation.get_unused_proxies()
        sessions = database.get_sessions()
        last = proxy_channel.get_last_scan()

        reader_name = reader
        for sess in sessions:
            if (sess.get("session_id") or sess.get("phone")) == reader:
                reader_name = sess.get("name") or reader
                break

        text = (
            f"{ui.card_open('Proxy Source Channel', 'The channel the proxies are dropped in', 'One of your accounts reads it', chr(128233))}\n\n"
            f"{ui.kv('Source channel', f'@{source}' if source else 'Not set', chr(128233))}\n"
            f"{ui.kv('Reader account', reader_name or 'Not set', chr(128100))}\n"
            f"{ui.kv('Proxies in the pool', str(len(pool)), chr(127752))}\n"
            f"{ui.kv('Free proxies', str(len(free)), chr(128176))}\n"
            f"{ui.kv('Last scan', (time.strftime('%d %b %H:%M', time.localtime(last)) if last else 'Never'), chr(128337))}\n\n"
            f"{ui.help_hint('The reader account JOINS the channel and reads the proxies. Every other account then gets one proxy of its own (one IP per account).')}"
            f"\n{ui.card_close()}"
        )

        buttons = [
            [utils.styled_button("\U0001f4e4 Set Proxy Channel", "admin_set_proxy_source", style="primary")],
            [utils.styled_button("\U0001f464 Set Reader Account", "admin_set_proxy_reader", style="primary")],
            [utils.styled_button("\U0001f517 Join & Read Now", "admin_proxy_channel_read", style="success")],
            [utils.styled_button("\U0001f504 Refresh Pool", "admin_proxy_pool_refresh", style="primary")],
            [utils.styled_button("\U0001f519 Back To Admin Panel", "menu_admin", style="danger")]
        ]
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_set_proxy_reader$"))
    async def admin_set_proxy_reader_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            return
        sessions = database.get_sessions()
        if not sessions:
            await event.answer("\u26a0\ufe0f There is no account yet.", alert=True)
            return
        text = (
            f"{ui.card('Reader Account', 'Who reads the proxy channel', 'The account that joins the channel', chr(128100))}\n\n"
            f"{ui.help_hint('Tap the account that should join and read the proxy channel.')}"
        )
        buttons = []
        for sess in sessions[:20]:
            sid = sess.get("session_id") or sess.get("phone")
            name = sess.get("name") or sid
            running = userbot_manager.is_bot_running(sid)
            mark = "\U0001f7e2" if running else "\U0001f534"
            buttons.append([utils.styled_button(f"{mark} {name}", f"admin_proxy_reader_{sid}", style="primary")])
        buttons.append([utils.styled_button("\U0001f519 Cancel", "admin_proxy_channel", style="danger")])
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=r"^admin_proxy_reader_(.+)$"))
    async def admin_proxy_reader_pick_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            return
        sid = event.pattern_match.group(1).strip()
        ok, msg = proxy_channel.set_reader_account(sid)
        audit("proxy.reader_account", sid, msg, user_id)
        await event.answer(msg, alert=True)
        await admin_proxy_channel_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_proxy_channel_read$"))
    async def admin_proxy_channel_read_callback(event):
        """
        Joins the proxy channel with the reader account and reads the proxies.
        """
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can do this.", alert=True)
            return

        source = proxy_tool.get_channel()
        if not source:
            await event.answer("\u26a0\ufe0f Set the proxy channel first.", alert=True)
            return

        from villain_ads_bot.services import userbot_manager
        sessions = database.get_sessions()
        reader = proxy_channel.get_reader_account()
        if not reader or not any((s.get("session_id") or s.get("phone")) == reader for s in sessions):
            reader = proxy_channel.pick_reader_account(sessions)
            if reader:
                proxy_channel.set_reader_account(reader)
        if not reader:
            await event.answer("\u26a0\ufe0f There is no account that could read the channel.", alert=True)
            return

        bot_obj = userbot_manager.find_running_bot(reader)
        if not bot_obj or not bot_obj.is_running:
            await event.answer("\u23f3\ufe0f Starting the reader account...", alert=True)
            await userbot_manager.start_userbot(reader)
            await asyncio.sleep(3)
            bot_obj = userbot_manager.find_running_bot(reader)
        if not bot_obj or not bot_obj.client:
            await event.answer("\u274c The reader account could not be started.", alert=True)
            return

        status = await event.respond("\U0001f517 <b>Joining the channel and reading the proxies...</b>", parse_mode="html")

        joined_ok, joined_msg = await proxy_channel.join_channel(bot_obj.client, source)
        if not joined_ok:
            await status.edit(f"\u274c <b>Could not join the channel</b>\n\n<code>{joined_msg}</code>", parse_mode="html")
            return

        result = await proxy_channel.refresh_from_channel(bot_obj.client, source)
        audit("proxy.channel_read", source,
              f"found={result.get('found')} added={result.get('added')}", user_id)

        if not result.get("ok"):
            await status.edit(
                f"\u26a0\ufe0f <b>Joined, but no proxy was found</b>\n\n"
                f"<code>{result.get('error') or ''}</code>\n\n"
                f"{ui.help_hint('Make sure the channel posts t.me/proxy links.')}",
                parse_mode="html"
            )
            return

        await status.edit(
            f"\u2705 <b>Proxies read</b>\n\n"
            f"\U0001f4dc <b>Found in the channel :</b> <code>{result.get('found', 0)}</code>\n"
            f"\U0001f517 <b>New in the pool :</b> <code>{result.get('added', 0)}</code>\n"
            f"\U0001f5c2 <b>Pool total :</b> <code>{result.get('total', 0)}</code>\n\n"
            f"{ui.help_hint('Assign them to the accounts now - every account gets its own IP.')}",
            parse_mode="html"
        )

    @client.on(events.CallbackQuery(pattern="^admin_proxy_pool_refresh$"))
    async def admin_proxy_pool_refresh_callback(event):
        """Reads the channel again and refreshes the pool."""
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            return
        from villain_ads_bot.services import userbot_manager
        reader = proxy_channel.get_reader_account()
        bot_obj = userbot_manager.find_running_bot(reader) if reader else None
        if not bot_obj or not bot_obj.client:
            await event.answer("\u26a0\ufe0f Start the reader account first.", alert=True)
            return
        result = await proxy_channel.refresh_from_channel(bot_obj.client)
        await event.answer(
            f"\u2705 Found {result.get('found', 0)} \u00b7 new {result.get('added', 0)} \u00b7 pool {result.get('total', 0)}",
            alert=True
        )

    # ==================== PROXY MIRROR ====================
    # ==================== /copyproxies ====================
    @client.on(events.NewMessage(pattern=r"^/copyproxies"))
    async def copy_proxies_cmd(event):
        """
        Copies the fresh proxies of the drop channel into the owner's own
        channel, with working "Connect N" buttons.
        """
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.reply("\u274c Only the owner can do this.")
            return

        status = await event.reply("\U0001f504 <b>Copying the proxies...</b>", parse_mode="html")
        try:
            result = await proxy_mirror.mirror_once(client)
        except Exception as e:
            result = {"ok": False, "mirrored": 0, "proxies": 0, "error": str(e)}

        if not result.get("ok"):
            await status.edit(f"\u274c <b>Could not copy</b>\n\n<code>{result.get('error')}</code>", parse_mode="html")
            return
        await status.edit(
            f"\u2705 <b>Proxy copy done</b>\n\n"
            f"\U0001f4dc <b>Messages copied :</b> <code>{result.get('mirrored', 0)}</code>\n"
            f"\U0001f517 <b>Proxies :</b> <code>{result.get('proxies', 0)}</code>\n\n"
            f"{ui.help_hint('The Connect buttons in your channel now work with this bot.')}",
            parse_mode="html"
        )

    @client.on(events.CallbackQuery(pattern="^admin_proxy_mirror$"))
    async def admin_proxy_mirror_callback(event):
        """
        Mirrors the proxy drop channel into the owner's own channel, with
        our own "Connect N" buttons - so a click reaches THIS bot.
        """
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can use the mirror.", alert=True)
            return

        source = proxy_tool.get_channel()
        mirror = proxy_mirror.get_mirror_channel()
        pool = proxy_tool.get_pool()

        text = (
            f"{ui.card_open('Proxy Mirror', 'Auto copy the drop channel', 'Your channel gets fresh proxies with working Connect buttons', chr(128225))}\n\n"
            f"{ui.kv('Source channel (drops)', f'@{source}' if source else 'Not set', chr(128233))}\n"
            f"{ui.kv('Your mirror channel', f'@{mirror}' if mirror else 'Not set', chr(127968))}\n"
            f"{ui.kv('Proxies in the pool', str(len(pool)), chr(127752))}\n\n"
            f"{ui.help_hint('Set the source channel, then set your own channel. The bot copies the messages and rebuilds the Connect buttons.')}"
            f"\n{ui.card_close()}"
        )

        buttons = [
            [utils.styled_button("\U0001f4e4 Set Source Channel", "admin_set_proxy_source", style="primary")],
            [utils.styled_button("\U0001f4e1 Set My Mirror Channel", "admin_set_proxy_mirror", style="primary")],
            [utils.styled_button("\U0001f504 Copy Now", "admin_proxy_mirror_now", style="success")],
            [utils.styled_button("\U0001f519 Back To Admin Panel", "menu_admin", style="primary")]
        ]
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_set_proxy_source$"))
    async def admin_set_proxy_source_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            return
        _admin_action_states[user_id] = "WAITING_FOR_PROXY_SOURCE"
        prompt = (
            f"{ui.card('Source Channel', 'Where the proxies are dropped', 'The bot must be able to read it', chr(128233))}\n\n"
            f"{ui.help_hint('Send the channel username or link (e.g. @proxygram or https://t.me/proxygram).')}"
        )
        buttons = [[utils.styled_button("\U0001f519 Cancel", "admin_proxy_mirror", style="danger")]]
        try:
            await event.edit(prompt, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_set_proxy_mirror$"))
    async def admin_set_proxy_mirror_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            return
        _admin_action_states[user_id] = "WAITING_FOR_PROXY_MIRROR"
        prompt = (
            f"{ui.card('Your Mirror Channel', 'Where the proxies are copied to', 'The bot must be an ADMIN of that channel', chr(127968))}\n\n"
            f"{ui.help_hint('Send your channel username or link (e.g. @myproxies).')}"
        )
        buttons = [[utils.styled_button("\U0001f519 Cancel", "admin_proxy_mirror", style="danger")]]
        try:
            await event.edit(prompt, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_proxy_mirror_now$"))
    async def admin_proxy_mirror_now_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can use the mirror.", alert=True)
            return
        status = await event.respond("\U0001f504 <b>Copying the proxies...</b>", parse_mode="html")
        try:
            result = await proxy_mirror.mirror_once(client)
        except Exception as e:
            result = {"ok": False, "mirrored": 0, "proxies": 0, "error": str(e)}
        if not result.get("ok"):
            await status.edit(f"\u274c <b>Could not copy</b>\n\n<code>{result.get('error')}</code>", parse_mode="html")
            return
        await status.edit(
            f"\u2705 <b>Proxy mirror done</b>\n\n"
            f"\U0001f4dc <b>Messages copied :</b> <code>{result.get('mirrored', 0)}</code>\n"
            f"\U0001f517 <b>Proxies found :</b> <code>{result.get('proxies', 0)}</code>\n"
            f"\U0001f510 <i>They are also in the pool now - assign them to the accounts.</i>",
            parse_mode="html"
        )

    # ==================== PROXY MANAGER ====================
    @client.on(events.CallbackQuery(pattern="^admin_proxies$"))
    async def admin_proxies_callback(event):
        """
        The proxy manager.

        Telegram counts every account of an IP address together, so without a
        proxy only a handful of accounts can be active at the same time. One
        proxy per account removes that limit.
        """
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can manage the proxies.", alert=True)
            return

        pool = proxy_tool.get_pool()
        global_settings = database.get_global_settings()
        default_idx = global_settings.get("default_proxy", -1)

        text = f"{ui.card_open('Proxy Manager', 'One proxy = one IP address', 'Run an unlimited number of Telegram accounts', chr(127760))}\n\n"
        if not pool:
            text += ui.help_hint("No proxy in the pool yet. Add one below.")
        else:
            for idx, p in enumerate(pool):
                mark = " \u2b50 DEFAULT" if idx == default_idx else ""
                text += f"\n{ui.kv(f'{idx + 1}', proxy_tool.redact(p) + mark, chr(127752))}"
        accounts = len(database.get_sessions())
        text += (
            f"\n\n{ui.kv('Accounts in the system', str(accounts), chr(128100))}"
            f"\n{ui.kv('Free (unused) proxies', str(len(proxy_rotation.get_unused_proxies())), chr(128176))}"
            f"\n{ui.help_hint('Every account gets its OWN proxy, so Telegram never counts them together.')}"
            f"\n{ui.help_hint(chr(9888) + ' Never rotate the IP of a running account on its own - the session would be revoked (AUTH_KEY_DUPLICATED).')}"
            f"\n{ui.help_hint('The rotation button disconnects the account first, waits and only then connects with the new IP.')}"
            f"\n{ui.card_close()}"
        )

        buttons = [[utils.styled_button("\u2795 Add Proxy", "admin_add_proxy", style="success")]]
        for idx in range(len(pool)):
            buttons.append([
                utils.styled_button(f"\u2b50 {idx + 1}", f"admin_proxy_default_{idx}", style="primary"),
                utils.styled_button(f"\U0001f5d1 {idx + 1}", f"admin_proxy_remove_{idx}", style="danger"),
            ])
        if pool:
            buttons.append([utils.styled_button("\U0001f504 Assign Proxies To All Accounts", "admin_proxy_assign_all", style="success")])
            buttons.append([utils.styled_button("\U0001f504 Rotate Proxies (Safe)", "admin_proxy_rotate", style="success")])
        buttons.append([utils.styled_button("\U0001f519 Back To Admin Panel", "menu_admin", style="primary")])

        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_add_proxy$"))
    async def admin_add_proxy_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can manage the proxies.", alert=True)
            return
        _admin_action_states[user_id] = "WAITING_FOR_PROXY"
        remove_hint = "Send the word remove to delete the last one."
        prompt = (
            f"{ui.card('Add Proxy', 'One proxy per account', 'SOCKS5 is recommended by Telegram', chr(127760))}\n\n"
            f"{ui.help_hint('Send the proxy in this format:')}\n"
            f"<code>socks5://user:password@host:port</code>\n\n"
            f"{ui.help_hint(remove_hint)}"
        )
        buttons = [[utils.styled_button("\U0001f519 Cancel", "admin_proxies", style="danger")]]
        try:
            await event.edit(prompt, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=r"^admin_proxy_remove_(\d+)$"))
    async def admin_proxy_remove_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can manage the proxies.", alert=True)
            return
        index = int(event.pattern_match.group(1))
        ok, msg = proxy_tool.remove_proxy(index)
        audit("proxy.remove", str(index), msg, user_id)
        await event.answer(msg, alert=True)
        await admin_proxies_callback(event)

    @client.on(events.CallbackQuery(pattern=r"^admin_proxy_default_(\d+)$"))
    async def admin_proxy_default_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can manage the proxies.", alert=True)
            return
        index = int(event.pattern_match.group(1))
        ok, msg = proxy_tool.set_default_proxy(index)
        audit("proxy.set_default", str(index), msg, user_id)
        await event.answer(msg, alert=True)
        await admin_proxies_callback(event)

    @client.on(events.CallbackQuery(pattern="^admin_proxy_rotate$"))
    async def admin_proxy_rotate_callback(event):
        """
        Rotates the proxies SAFELY: every account is disconnected first, the
        new proxy is written and only then the account starts again.
        """
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can do this.", alert=True)
            return

        sessions = database.get_sessions()
        if not sessions:
            await event.answer("\u26a0\ufe0f There is no account.", alert=True)
            return

        await event.answer("\u23f3\ufe0f Rotating - the accounts are restarted one by one.", alert=True)
        status = await event.respond(
            "\U0001f504 <b>Rotating the proxies...</b>\n\n"
            "<i>Every account is disconnected first, then connected with its new IP. "
            "That gap is what keeps Telegram from revoking the session.</i>",
            parse_mode="html"
        )

        ids = [s.get("session_id") or s.get("phone") for s in sessions]
        ids = [i for i in ids if i]
        result = await proxy_rotation.rotate_account_proxies(ids, restart=True)

        audit("proxy.rotate", None,
              f"rotated={result.get('rotated')} failed={result.get('failed')}", user_id)

        lines = []
        for line in (result.get("details") or [])[:40]:
            lines.append(f"\u2022 {line}")
        summary = (
            f"\u2705 <b>Rotation done</b>\n\n"
            f"\U0001f504 <b>Rotated :</b> <code>{result.get('rotated', 0)}</code>\n"
            f"\u274c <b>Failed :</b> <code>{result.get('failed', 0)}</code>\n"
        )
        if lines:
            summary += "\n<pre>" + "\n".join(lines) + "</pre>"
        try:
            await status.edit(summary, parse_mode="html")
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^proxy_connect_(\d+)_(\d+)$"))
    async def proxy_connect_callback(event):
        """
        The "Connect N" button of a mirrored proxy message.

        The proxy of that message is extracted and offered to the user - with
        the option to move one of their accounts onto it.
        """
        message_id = int(event.pattern_match.group(1))
        index = int(event.pattern_match.group(2))
        user_id = event.sender_id

        mirror = proxy_mirror.get_mirror_channel()
        proxy, error = await proxy_mirror.resolve_connect(client, mirror, message_id, index)
        if not proxy:
            await event.answer(f"\u274c {error or 'No proxy found.'}", alert=True)
            return

        buttons = []
        sessions = database.get_sessions(user_id)
        for sess in sessions[:8]:
            phone = sess.get("phone") or sess.get("session_id")
            name = sess.get("name") or phone
            buttons.append([
                utils.styled_button(f"\U0001f680 Use For {name}", f"proxy_apply_{phone}_{message_id}_{index}", style="success")
            ])
        buttons.append([utils.styled_button("\U0001f517 Open In Telegram", url=proxy.get("raw", ""), style="primary")])

        server = f"{proxy.get('host')}:{proxy.get('port')}"
        await event.answer("\u2705 Proxy found!", alert=True)
        await event.respond(
            f"{ui.card('Proxy Ready', f'Connect {index}', 'Use it for one of your accounts', chr(128225))}\n\n"
            f"{ui.kv('Proxy', proxy_tool.redact(proxy), chr(127752))}\n"
            f"{ui.kv('Server', server, chr(128225))}\n\n"
            f"{ui.help_hint('Pick an account below - it is disconnected and then connected with this IP.')}",
            buttons=buttons, parse_mode="html"
        )

    @client.on(events.CallbackQuery(pattern=r"^proxy_apply_(.+)_(\d+)_(\d+)$"))
    async def proxy_apply_callback(event):
        """Moves one account onto the proxy of the mirrored message."""
        raw = event.pattern_match.group(1)
        message_id = int(event.pattern_match.group(2))
        index = int(event.pattern_match.group(3))
        user_id = event.sender_id

        parts = raw.rsplit("_", 2)
        phone = parts[0] if len(parts) == 3 else raw

        mirror = proxy_mirror.get_mirror_channel()
        proxy, error = await proxy_mirror.resolve_connect(client, mirror, message_id, index)
        if not proxy:
            await event.answer(f"\u274c {error or 'No proxy found.'}", alert=True)
            return

        sess = database.get_session(phone)
        if not sess:
            await event.answer("\u274c Account not found.", alert=True)
            return
        if str(sess.get("user_id")) != str(user_id) and not check_admin(user_id):
            await event.answer("\u274c This is not your account.", alert=True)
            return

        # Disconnect first - the old IP must be closed before the new one is
        # used, otherwise Telegram revokes the session (AUTH_KEY_DUPLICATED).
        was_running = userbot_manager.is_bot_running(phone)
        if was_running:
            await userbot_manager.stop_userbot(phone)
            await asyncio.sleep(proxy_rotation.RECONNECT_DELAY)

        sess["proxy"] = proxy
        sess["proxy_auto"] = False
        database.save_session(sess)

        if was_running:
            await userbot_manager.start_userbot(phone)

        await event.answer("\u2705 Done - the account is on the new IP.", alert=True)
        try:
            await event.edit(
                f"\u2705 <b>Proxy applied</b>\n\n"
                f"{ui.kv('Account', sess.get('name') or phone, chr(128100))}\n"
                f"{ui.kv('Proxy', proxy_tool.redact(proxy), chr(127752))}\n\n"
                f"{ui.help_hint('The account was disconnected first and then connected with the new IP.')}",
                parse_mode="html"
            )
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern="^admin_proxy_assign_all$"))
    async def admin_proxy_assign_all_callback(event):
        """Assigns a proxy to EVERY account (round robin)."""
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can manage the proxies.", alert=True)
            return
        pool = proxy_tool.get_pool()
        if not pool:
            await event.answer("\u26a0\ufe0f There is no proxy in the pool.", alert=True)
            return

        status = await event.respond("\U0001f504 <b>Assigning proxies to every account...</b>", parse_mode="html")
        sessions = database.get_sessions()
        done = 0
        for s in sessions:
            sid = s.get("session_id") or s.get("phone")
            if not sid:
                continue
            assigned = proxy_tool.assign_round_robin(sid)
            if not assigned:
                continue
            s["proxy"] = assigned
            database.save_session(s)
            done += 1
        audit("proxy.assign_all", None, f"accounts={done}", user_id)
        try:
            await status.edit(
                f"\u2705 <b>Proxies assigned</b>\n\n"
                f"\U0001f4dc <b>Accounts updated :</b> <code>{done}</code>\n"
                f"\U0001f504 <i>Restart the accounts to apply them.</i>",
                parse_mode="html"
            )
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern="^admin_toggle_unlimited_accounts$"))
    async def admin_toggle_unlimited_accounts_callback(event):
        """Removes the concurrent userbot limit completely."""
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can change this.", alert=True)
            return
        global_settings = database.get_global_settings()
        current = bool(global_settings.get("unlimited_accounts", False))
        global_settings["unlimited_accounts"] = not current
        database.save_global_settings(global_settings)
        audit("accounts.unlimited_toggle", None, f"unlimited_accounts={not current}", user_id)
        state = "ON - no account is limited" if not current else "OFF - the limit applies again"
        await event.answer(f"\U0001f4be Unlimited accounts: {state}", alert=True)

    # ==================== #2  COUPON GENERATOR UI ====================
    @client.on(events.CallbackQuery(pattern="^admin_plan_unlimited_opt$"))
    async def admin_plan_unlimited_opt_callback(event):
        """Switches the plan that is being created to 'unlimited slots'."""
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("❌ Only the owner can do this.", alert=True)
            return
        _admin_action_states[user_id] = "WAITING_FOR_PLAN_UNLIMITED"
        await event.answer("♾ Unlimited slots selected.", alert=True)
        prompt = (
            f"{ui.card('Unlimited Plan', 'No ID limit at all', 'The user can connect as many accounts as they want', '♾')}\n\n"
            f"{ui.help_hint('Now send the price of the plan in INR.')}"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "cancel_admin_plan", style="danger")]]
        try:
            await event.edit(prompt, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_coupons$"))
    async def admin_coupons_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_MODERATOR):
            await event.answer("❌ Access denied.", alert=True)
            return

        coupons = database.get_coupons()
        coupons = sorted(coupons, key=lambda c: c.get("created_at", 0) or 0, reverse=True)

        import time as _time
        now = _time.time()

        text = f"{ui.card_open('Coupons', 'Wallet credit codes', 'Create and manage discount codes', chr(127903))}\n\n"
        if not coupons:
            text += ui.help_hint("No coupon created yet. Use the button below to create one.")
        else:
            for c in coupons[:30]:
                code = c.get("code", "?")
                amount = float(c.get("amount_inr", 0) or 0)
                used = int(c.get("current_uses", 0) or 0)
                max_uses = int(c.get("max_uses", 0) or 0)
                active = bool(c.get("is_active", True))
                expires = c.get("expires_at")
                expired = bool(expires and expires < now)
                state = "🔴 EXPIRED" if expired else ("🟢 ON" if active else "🔴 OFF")
                text += (
                    f"\n{ui.kv('Code', f'<code>{code}</code>', chr(127903))}"
                    f"\n  💰 <b>₹{amount:.2f}</b>  👥 <b>{used}/{max_uses}</b>  {state}"
                )
        text += f"\n\n{ui.card_close()}"

        buttons = [[utils.styled_button("➕ Create Coupon", "admin_coupon_create_amount", style="success")]]
        for c in coupons[:15]:
            code = c.get("code", "")
            buttons.append([
                utils.styled_button(f"🔄 {code}", f"admin_coupon_toggle_{code}", style="primary"),
                utils.styled_button(f"🗑 {code}", f"admin_coupon_delete_{code}", style="danger"),
            ])
        buttons.append([utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")])

        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_coupon_create_amount$"))
    async def admin_coupon_create_amount_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_MODERATOR):
            await event.answer("❌ Access denied.", alert=True)
            return
        _admin_action_states[user_id] = "WAITING_FOR_COUPON_AMOUNT"
        prompt = (
            f"{ui.card('Create Coupon', 'Step 1 of 3', 'How much wallet credit is this coupon worth?', chr(127903))}\n\n"
            f"{ui.help_hint('Send the amount in INR (e.g. 50).')}"
        )
        buttons = [[utils.styled_button("❌ Cancel", "admin_coupons", style="danger")]]
        try:
            await event.edit(prompt, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=r"^admin_coupon_toggle_(.+)$"))
    async def admin_coupon_toggle_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_MODERATOR):
            await event.answer("❌ Access denied.", alert=True)
            return
        code = event.pattern_match.group(1).strip()
        coupon = database.get_coupon(code)
        if not coupon:
            await event.answer("❌ Coupon not found.", alert=True)
            return
        coupon["is_active"] = not coupon.get("is_active", True)
        database.save_coupon(coupon)
        audit("coupon.toggle", code, f"is_active={coupon['is_active']}", user_id)
        await event.answer(f"🔄 {code} is now {'ON' if coupon['is_active'] else 'OFF'}", alert=True)
        await admin_coupons_callback(event)

    @client.on(events.CallbackQuery(pattern=r"^admin_coupon_delete_(.+)$"))
    async def admin_coupon_delete_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("❌ Only the owner can delete coupons.", alert=True)
            return
        code = event.pattern_match.group(1).strip()
        coupon = database.get_coupon(code)
        if not coupon:
            await event.answer("❌ Coupon not found.", alert=True)
            return
        database.delete_coupon(code)
        audit("coupon.delete", code, f"amount={coupon.get('amount_inr')}", user_id)
        await event.answer(f"🗑 {code} deleted", alert=True)
        await admin_coupons_callback(event)

    # ==================== #3  AUDIT LOG ====================
    @client.on(events.CallbackQuery(pattern="^admin_audit_log$"))
    async def admin_audit_log_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_MODERATOR):
            await event.answer("❌ Access denied.", alert=True)
            return

        entries = database.get_audit_log(limit=60)
        total = database.count_audit_log()
        text = f"{ui.card_open('Audit Log', 'Every admin action is recorded', f'{total} entries in total', chr(128220))}\n\n"
        if not entries:
            text += ui.help_hint("No action has been recorded yet.")
        else:
            import time as _t
            for e in entries:
                when = _t.strftime("%d %b %H:%M", _t.localtime(e.get("ts", 0)))
                who = e.get("actor_id") or "system"
                text += (
                    f"\n{ui.kv(when, e.get('action', '?'), chr(128196))}"
                    f"\n  👤 <code>{who}</code>"
                    + (f" 🎯 <code>{e.get('target')}</code>" if e.get("target") else "")
                    + (f"\n  💬 {e.get('details')}" if e.get("details") else "")
                )
        text += f"\n\n{ui.card_close()}"

        buttons = [
            [utils.styled_button("🗑 Purge Old Entries (90d)", "admin_purge_audit", style="danger")],
            [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")]
        ]
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_purge_audit$"))
    async def admin_purge_audit_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("❌ Only the owner can purge the audit log.", alert=True)
            return
        removed = database.purge_audit_log(90)
        audit("audit.purge", None, f"removed={removed}", user_id)
        await event.answer(f"🗑 {removed} old entries removed", alert=True)
        await admin_audit_log_callback(event)

    # ==================== #10  ADMIN ROLES ====================
    @client.on(events.CallbackQuery(pattern="^admin_roles$"))
    async def admin_roles_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("❌ Only the owner can manage roles.", alert=True)
            return

        global_settings = database.get_global_settings()
        admins = list(global_settings.get("admins", []) or [])
        for uid in config.ORIGINAL_ADMIN_IDS or set():
            if uid not in admins:
                admins.append(uid)
        roles = database.get_admin_roles()

        text = f"{ui.card_open('Admin Roles', 'Who may do what', 'Tap an admin to change the role', chr(128104))}\n\n"
        for uid in admins:
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue
            if uid in (config.ORIGINAL_ADMIN_IDS or set()):
                role = ROLE_OWNER
            else:
                role = roles.get(str(uid), ROLE_SUPPORT)
            label = ROLE_LABELS.get(role, role)
            text += f"\n{ui.kv(f'ID {uid}', label, chr(128100))}"
        text += (
            f"\n\n{ui.help_hint('owner = everything · moderator = payments + userbot control · support = read only')}"
            f"\n{ui.card_close()}"
        )

        buttons = []
        for uid in admins:
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                continue
            if uid in (config.ORIGINAL_ADMIN_IDS or set()):
                continue
            buttons.append([utils.styled_button(f"🏴 {uid}", f"admin_role_set_{uid}", style="primary")])
        buttons.append([utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="primary")])

        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern=r"^admin_role_set_(\d+)$"))
    async def admin_role_set_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("❌ Only the owner can manage roles.", alert=True)
            return
        target_id = int(event.pattern_match.group(1))
        if target_id in (config.ORIGINAL_ADMIN_IDS or set()):
            await event.answer("❌ The root owner can never be downgraded.", alert=True)
            return

        current = database.get_admin_role(target_id)
        order = [ROLE_SUPPORT, ROLE_MODERATOR, ROLE_OWNER]
        try:
            new_role = order[(order.index(current) + 1) % len(order)]
        except ValueError:
            new_role = ROLE_SUPPORT

        database.set_admin_role(target_id, new_role)
        audit("admin.role_change", str(target_id), f"{current} -> {new_role}", user_id)
        await event.answer(f"🏴 {target_id} is now {ROLE_LABELS.get(new_role, new_role)}", alert=True)
        await admin_roles_callback(event)

    # ==================== #11  BACKUP / RESTORE ====================
    @client.on(events.CallbackQuery(pattern="^admin_backup_now$"))
    async def admin_backup_now_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("❌ Only the owner can create backups.", alert=True)
            return

        status = await event.respond("🔄 <b>Building the backup...</b>\n\n<i>Please wait…</i>", parse_mode="html")
        try:
            from villain_ads_bot.core import backup as _backup
            data = _backup.build_backup(include_session_files=True)
            filename = _backup.backup_filename()
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            utils.report_error("admin_backup_now", e)
            await status.edit("❌ <b>Backup failed</b>", parse_mode="html")
            return

        global_settings = database.get_global_settings()
        log_group_id = global_settings.get("log_group_id")
        sent = 0
        if log_group_id:
            try:
                await event.client.send_message(
                    log_group_id,
                    f"💾 <b>Automatic backup</b>\n<code>{filename}</code>",
                    file=filename,
                    attributes=None,
                )
                sent += 1
            except Exception as e:
                logger.warning(f"Could not send the backup to the log group: {e}")

        try:
            await event.client.send_file(
                user_id, filename, caption="💾 <b>Your bot backup</b>\n<i>Keep it safe — it contains every session and setting.</i>"
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Could not send the backup to the owner: {e}")

        audit("backup.create", filename, f"size={len(data)} bytes, sent={sent}", user_id)
        await status.edit(
            f"✅ <b>Backup ready</b>\n\n"
            f"📄 <code>{filename}</code>\n"
            f"📦 <b>Size :</b> <code>{len(data) / 1024:.1f} KB</code>\n"
            f"📤 <b>Delivered to :</b> <code>{sent}</code> place(s)\n\n"
            f"🔐 <i>The session files are included. The bot session key and the GPT key are never exported.</i>",
            parse_mode="html"
        )

    @client.on(events.CallbackQuery(pattern="^admin_restore_backup$"))
    async def admin_restore_backup_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("❌ Only the owner can restore a backup.", alert=True)
            return

        _admin_action_states[user_id] = "WAITING_FOR_BACKUP_FILE"
        prompt = (
            f"{ui.card('Restore Backup', 'Step 1 of 2', 'Upload the backup zip file', chr(128190))}\n\n"
            f"{ui.help_hint('Send the villain_backup_*.zip file. You will get a preview before anything is restored.')}"
        )
        buttons = [[utils.styled_button("❌ Cancel", "menu_admin", style="danger")]]
        try:
            await event.edit(prompt, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_music_api$"))
    async def admin_music_api_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can change the music API key.", alert=True)
            return
        if not check_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return

        global_settings = database.get_global_settings()
        from villain_ads_bot.core import music as _music
        url, key, backup = _music.get_configured_credentials()
        masked = _music.mask_key(key)

        text = (
            f"{ui.card('Music API Settings', '', 'Yuki / Meow music gateway', '🎵')}\n\n"
            f"{ui.kv('API key', str(masked), '🔑')}\n"
            f"{ui.kv('Primary URL', str(url), '🌐')}\n"
            f"{ui.kv('Backup URL', str(backup), '🔗')}\n\n"
            f"{ui.help_hint('The key is used for streaming, search, lyrics and track info.')}"
        )
        buttons = [
            [utils.styled_button("🔑 Set API Key", "admin_set_music_key", style="primary")],
            [utils.styled_button("🔗 Set Primary URL", "admin_set_music_url", style="primary")],
            [utils.styled_button("🔗 Set Backup URL", "admin_set_music_backup_url", style="primary")],
            [utils.styled_button("✅ Test Connection", "admin_test_music_api", style="success")],
            [utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="danger")]
        ]
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_set_music_key$"))
    async def admin_set_music_key_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states[user_id] = "WAITING_FOR_SET_MUSIC_KEY"
        prompt_text = (
            f"{ui.card('Set Music API Key', '', 'Paste the new key', '🎵')}\n\n"
            f"{ui.help_hint('Send the API key or the word none to clear it.')}"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_music_api", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt_text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_test_music_api$"))
    async def admin_test_music_api_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        await event.answer("🧪 Testing the music gateway...", alert=True)
        ok, detail = await _test_music_api()
        await event.answer(("✅ " if ok else "❌ ") + detail[:180], alert=True)

    @client.on(events.CallbackQuery(pattern="^admin_set_music_url$"))
    async def admin_set_music_url_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states[user_id] = "WAITING_FOR_SET_MUSIC_URL"
        prompt_text = (
            f"{ui.card('Set Primary Music URL', '', 'The main Yuki / Meow gateway', '🎵')}\n\n"
            f"{ui.help_hint('Send the base url, e.g. https://music.yukiapi.site')}"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_music_api", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt_text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_set_music_backup_url$"))
    async def admin_set_music_backup_url_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states[user_id] = "WAITING_FOR_SET_MUSIC_BACKUP_URL"
        prompt_text = (
            f"{ui.card('Set Backup Music URL', '', 'The failover Yuki / Meow gateway', '🎵')}\n\n"
            f"{ui.help_hint('Send the base url, e.g. https://play.yukiapi.site')}"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_music_api", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt_text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_api_credentials$"))
    async def admin_api_credentials_callback(event):
        user_id = event.sender_id
        if not check_role(user_id, ROLE_OWNER):
            await event.answer("\u274c Only the owner can manage the API credentials.", alert=True)
            return
        if not check_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return

        global_settings = database.get_global_settings()
        creds = global_settings.get("api_credentials", []) or []
        env_pairs = config.get_all_api_pairs()

        lines = []
        if creds:
            for idx, cred in enumerate(creds):
                lines.append(
                    f"🔐 <code>{cred.get('api_id')}</code> • "
                    f"<code>{str(cred.get('api_hash'))[:6]}{'•' * 26}</code>"
                )
        if not lines:
            lines.append("📄 <i>No panel managed credentials yet.</i>")

        text = (
            f"{ui.card('API Credentials', '', 'Multiple API_ID / API_HASH pairs avoid flood-waits', '🔐')}\n\n"
            f"{ui.kv('Panel managed', str(len(creds)), '🔐')}\n"
            f"{ui.kv('Total available', str(len(env_pairs)), '✅')}\n\n"
            + "\n".join(lines)
            + "\n\n"
            f"{ui.help_hint('Credentials are picked deterministically per account so a session always keeps the same API id.')}"
        )
        buttons = [
            [utils.styled_button("🔐 Add API ID / Hash", "admin_add_api_cred", style="success")]
        ]
        if creds:
            buttons.append([utils.styled_button("🗑 Remove Last Credential", "admin_remove_api_cred", style="danger")])
        buttons.append([utils.styled_button("🔙 Back To Admin Panel", "menu_admin", style="danger")])
        try:
            await event.edit(text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_add_api_cred$"))
    async def admin_add_api_cred_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        _admin_action_states[user_id] = "WAITING_FOR_ADD_API_CRED"
        prompt_text = (
            f"{ui.card('Add API Credentials', '', 'Send API_ID:API_HASH in one line', '🔐')}\n\n"
            f"<code>12345678:0123456789abcdef0123456789abcdef</code>\n\n"
            f"{ui.help_hint('Send the word cancel to abort.')}"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_api_credentials", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons, parse_mode="html")
        except Exception:
            await event.respond(prompt_text, buttons=buttons, parse_mode="html")

    @client.on(events.CallbackQuery(pattern="^admin_remove_api_cred$"))
    async def admin_remove_api_cred_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return
        global_settings = database.get_global_settings()
        creds = list(global_settings.get("api_credentials", []) or [])
        if creds:
            removed = creds.pop()
            global_settings["api_credentials"] = creds
            database.save_global_settings(global_settings)
            await event.answer(f"🗑 Removed API ID {removed.get('api_id')}", alert=True)
        else:
            await event.answer("ℹ️ Nothing to remove.", alert=True)
        await admin_api_credentials_callback(event)

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

    @client.on(events.CallbackQuery(pattern=r"^admin_set_brand_username_txt$"))
    async def admin_set_branding_username_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            return

        _admin_action_states[user_id] = "WAITING_FOR_BRAND_USERNAME_TXT"

        prompt_text = (
            "<blockquote><b>🔗 Set Branding Username</b>\n\n"
            "Send the Telegram username (without @) that should be used as the fallback branding suffix.\n\n"
            "Example: <code>MySupportBot</code>\n\n"
            "💡 <i>Used as <code> via @MySupportBot</code> whenever no explicit name/bio suffix is configured.</i></blockquote>"
        )
        buttons = [[utils.styled_button("🔙 Cancel", "admin_branding_settings", style="danger")]]
        try:
            await event.edit(prompt_text, buttons=buttons)
        except Exception:
            await event.respond(prompt_text, buttons=buttons)

    @client.on(events.CallbackQuery(pattern=r"^admin_tgl_brand_lock_opt$"))
    async def admin_toggle_branding_lock_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return

        await event.answer()
        global_settings = database.get_global_settings()
        current = global_settings.get("branding_lock", True)
        global_settings["branding_lock"] = not current
        database.save_global_settings(global_settings)

        import userbot_manager
        client.loop.create_task(userbot_manager.broadcast_branding_update())

        state = "ON" if not current else "OFF"
        await event.answer(f"🔒 Branding lock turned {state}.", alert=True)
        await show_branding_settings(event, user_id)

    @client.on(events.CallbackQuery(pattern=r"^admin_apply_branding_now$"))
    async def admin_apply_branding_now_callback(event):
        user_id = event.sender_id
        if not check_admin(user_id):
            await event.answer("❌ Access denied.", alert=True)
            return

        await event.answer("🚀 Applying branding to all userbots...", alert=True)
        from villain_ads_bot.services import userbot_manager
        try:
            await userbot_manager.broadcast_branding_update()
            await event.answer("✅ Branding applied to all userbots.", alert=True)
        except Exception as e:
            logger.error(f"Manual branding apply failed: {e}")
            utils.report_error("admin_apply_branding_now", e)
            await event.answer("❌ Failed to apply branding.", alert=True)
        await show_branding_settings(event, user_id)


async def show_branding_settings(event, user_id: int):
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    if not check_admin(user_id):
        await event.respond(utils.get_text("error_not_admin", lang))
        return
        
    global_settings = database.get_global_settings()
    brand_name_val = "✅ ON" if global_settings.get("branding_name_enabled", True) else "❌ OFF"
    brand_bio_val = "✅ ON" if global_settings.get("branding_bio_enabled", True) else "❌ OFF"
    brand_lock_val = "🔒 LOCKED" if global_settings.get("branding_lock", True) else "🔓 UNLOCKED"
    brand_username = (global_settings.get("branding_username") or "").strip()
    name_text = global_settings.get("branding_name_text") or "Not Set"
    bio_text = global_settings.get("branding_bio_text") or "Not Set"
    fallback_note = f" via @{brand_username}" if brand_username else "Not Set"
    
    text = (
        f"{ui.card('Branding Configurations', '', 'Global branding for every hosted userbot', '🎨')}\n\n"
        f"{ui.kv('Name branding', brand_name_val, '📛')}\n"
        f"{ui.kv('Name suffix', str(name_text), '✏️')}\n\n"
        f"{ui.kv('Bio branding', brand_bio_val, '📝')}\n"
        f"{ui.kv('Bio suffix', str(bio_text), '✏️')}\n\n"
        f"{ui.kv('Branding username', brand_username or 'Not Set', '🔗')}\n"
        f"{ui.kv('Fallback suffix', fallback_note, '↪️')}\n"
        f"{ui.kv('Branding lock', brand_lock_val, '🔒')}\n\n"
        f"{ui.help_hint('The branding is applied to every userbot instantly when you save it, and to every userbot hosted in the future.')}\n"
        f"{ui.help_hint('With the lock enabled no user can remove the branding from their account.')}"
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
            utils.styled_button("🔗 Set Branding Username", "admin_set_brand_username_txt", style="primary"),
            utils.styled_button("🔒 Toggle Branding Lock", "admin_tgl_brand_lock_opt", style="primary")
        ],
        [
            utils.styled_button("🚀 Apply To All Userbots", "admin_apply_branding_now", style="success")
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
        from villain_ads_bot.handlers.my_bots import show_bots_list, show_all_slots_dashboard, set_admin_impersonation
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
        audit("user.ban", str(target_id), f"by admin {user_id}", user_id)
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
        audit("user.unban", str(target_id), f"by admin {user_id}", user_id)
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
