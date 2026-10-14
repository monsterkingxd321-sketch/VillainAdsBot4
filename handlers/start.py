import logging
from telethon import events, Button
import database
import models
import config
from utils import styled_button, get_text

logger = logging.getLogger(__name__)

async def check_onboarding(client, event) -> bool:
    """
    Checks if user is onboarded. If not, kicks off the onboarding flow and returns False.
    """
    user_id = event.sender_id
    user = database.get_user(user_id)
    
    if not user:
        user = models.create_default_user(user_id)
        
    try:
        sender = await event.get_sender()
        if sender:
            if getattr(sender, "username", None):
                user["username"] = sender.username
            if getattr(sender, "first_name", None):
                user["first_name"] = sender.first_name
            if getattr(sender, "last_name", None):
                user["last_name"] = sender.last_name
            if getattr(sender, "access_hash", None):
                user["access_hash"] = sender.access_hash
    except Exception:
        pass
        
    database.save_user(user)
        
    if not user.get("language"):
        # Show Language Selection Buttons
        buttons = [
            [
                styled_button("English 🇬🇧", "set_lang_en", style="primary"),
                styled_button("Hindi 🇮🇳", "set_lang_hi", style="primary"),
                styled_button("Russian 🇷🇺", "set_lang_ru", style="primary")
            ],
            [
                styled_button("Japanese 🇯🇵", "set_lang_ja", style="primary"),
                styled_button("French 🇫🇷", "set_lang_fr", style="primary"),
                styled_button("German 🇩🇪", "set_lang_de", style="primary")
            ],
            [
                styled_button("Chinese 🇨🇳", "set_lang_zh", style="primary"),
                styled_button("Arabic 🇸🇦", "set_lang_ar", style="primary")
            ]
        ]
        msg = get_text("select_lang", "en")
        await event.respond(msg, buttons=buttons)
        return False
        
    if not user.get("tos_accepted"):
        # Show TOS Agreement
        lang = user.get("language")
        msg = get_text("tos_text", lang)
        buttons = [
            [styled_button(get_text("tos_accept_btn", lang), "accept_tos", style="success")]
        ]
        await event.respond(msg, buttons=buttons)
        return False
        
    return True

async def show_main_menu(event, user_id):
    """
    Displays the primary bot dashboard containing main actions.
    """
    user = database.get_user(user_id)
    if not user:
        return
        
    lang = user.get("language", "en") or "en"
    import utils
    allowed = utils.get_allowed_slots(user_id)
    sessions = database.get_sessions(user_id)
    used = len(sessions)
    
    text = get_text("main_menu", lang, allowed=allowed, used=used)
    
    global_settings = database.get_global_settings()
    admins_list = global_settings.get("admins", [])
    is_admin = user_id in admins_list or user_id in config.ORIGINAL_ADMIN_IDS
    
    support_channel = global_settings.get("support_channel") or "https://t.me/TheVillainActive"
    support_group = global_settings.get("support_group") or "https://t.me/+WzyoJkg4bzhlNTFl"
    
    # Configure main menu dashboard buttons
    buttons = [
        [
            styled_button(get_text("btn_add_bot", lang), "menu_add_bot", style="primary"),
            styled_button(get_text("btn_my_bots", lang), "menu_my_bots", style="primary")
        ],
        [
            styled_button(get_text("btn_settings", lang), "menu_settings", style="primary"),
            styled_button(get_text("btn_status", lang), "menu_status", style="primary")
        ],
        [
            styled_button("👫 Refer & Earn", "settings_referrals", style="success")
        ]
    ]
    
    buttons.append([Button.url(config.OWNER_NAME, config.OWNER_URL)])
    buttons.append([
        Button.url("📢 Support Channel", support_channel),
        Button.url("💬 Support Group", support_group)
    ])
    
    if is_admin:
        buttons.append([styled_button(get_text("btn_admin_panel", lang), "menu_admin", style="primary")])
        
    start_image = global_settings.get("start_image")
    try:
        if start_image:
            if hasattr(event, "edit"):
                try:
                    await event.delete()
                except Exception:
                    pass
            try:
                # Timeout image delivery to fall back instantly if network or upload is busy
                await asyncio.wait_for(event.respond(text, file=start_image, buttons=buttons), timeout=4.0)
            except Exception as img_err:
                logger.warning(f"Start image delayed or failed ({img_err}), falling back to text dashboard.")
                await event.respond(text, buttons=buttons)
        else:
            if hasattr(event, "edit"):
                await event.edit(text, buttons=buttons)
            else:
                await event.respond(text, buttons=buttons)
    except Exception as e:
        logger.error(f"Error rendering main menu: {e}")
        try:
            await event.respond(text, buttons=buttons)
        except Exception:
            pass

def register_handlers(client):
    import asyncio

    @client.on(events.NewMessage(pattern="/start"))
    async def start_cmd(event):
        try:
            # Handle group start commands by offering a start in DM button
            if not event.is_private:
                try:
                    me = await client.get_me()
                    bot_username = me.username
                    text = (
                        f"<blockquote><b>» ⚡ {config.BOT_NAME.upper()} Manager</b>\n\n"
                        f"Configure Your Own Automated Userbots, Group Broadcasts, Auto-Welcome, Vc Streaming, And Ai Automations!\n\n"
                        f"Tap Below To Get Help In Pm With Everything You Need.</blockquote>"
                    )
                    buttons = [[Button.url("🚀 Start In Pm", url=f"https://t.me/{bot_username}?start=true")]]
                    await event.reply(text, buttons=buttons)
                except Exception as e:
                    logger.error(f"Failed to respond to start command in group: {e}")
                return
                
            user_id = event.sender_id
            ref_id = None
            
            # Parse referral details if present
            if event.text and len(event.text.split()) > 1:
                arg = event.text.split()[1]
                if arg.startswith("ref_"):
                    try:
                        ref_id = int(arg.replace("ref_", ""))
                    except ValueError:
                        pass
                        
            # Get sender details safely with timeout so busy connection never hangs /start
            sender = None
            try:
                sender = await asyncio.wait_for(event.get_sender(), timeout=2.0)
            except Exception:
                sender = getattr(event, "sender", None)

            sender_username = getattr(sender, "username", None) if sender else None
            sender_first = getattr(sender, "first_name", None) if sender else None
            sender_last = getattr(sender, "last_name", None) if sender else None
            
            user = database.get_user(user_id)
            if not user:
                user = models.create_default_user(user_id)
                user["username"] = sender_username
                user["first_name"] = sender_first
                user["last_name"] = sender_last
                
                if ref_id and ref_id != user_id:
                    ref_parent = database.get_user(ref_id)
                    if ref_parent:
                        user["referred_by"] = ref_id
                        # Instantly credit ₹1.00 referral bonus to referrer
                        ref_parent["wallet_balance"] = ref_parent.get("wallet_balance", 0.0) + 1.0
                        ref_parent["referral_earnings"] = ref_parent.get("referral_earnings", 0.0) + 1.0
                        database.save_user(ref_parent)
                        logger.info(f"New user {user_id} referred by {ref_id}. Credited ₹1 to referrer.")
                        
                        try:
                            name_str = f"@{sender_username}" if sender_username else f"{sender_first or ''} {sender_last or ''}".strip()
                            if not name_str:
                                name_str = f"User {user_id}"
                                
                            # Notify referrer in background task so it doesn't block the /start response
                            asyncio.create_task(client.send_message(
                                ref_id,
                                f"<blockquote><b>» 🎁 New Referral!</b>\n\n"
                                f"👤 <b>{name_str}</b> Joined The Bot Using Your Link.\n"
                                f"💰 Your Reward <b>₹1.00</b> Has Been Credited To Your Wallet.</blockquote>"
                            ))
                        except Exception as ref_err:
                            logger.warning(f"Could not notify referrer {ref_id}: {ref_err}")
                database.save_user(user)
            else:
                # Update username details if changed
                if user.get("username") != sender_username or user.get("first_name") != sender_first or user.get("last_name") != sender_last:
                    user["username"] = sender_username
                    user["first_name"] = sender_first
                    user["last_name"] = sender_last
                    database.save_user(user)
                
            # Security verification checks (bans, maintenance, force subscribe)
            import utils
            if await utils.guard(event, client):
                return
                
            # Check if deep link is for managing a specific userbot e.g. /start bot_918518814006
            if event.text and len(event.text.split()) > 1:
                arg = event.text.split()[1].strip()
                if arg.startswith("bot_"):
                    phone_target = arg.replace("bot_", "").strip()
                    global_settings = database.get_global_settings()
                    admins_list = global_settings.get("admins", [])
                    is_admin = user_id in admins_list or user_id in config.ORIGINAL_ADMIN_IDS
                    
                    if not is_admin:
                        await event.reply("<blockquote><b>» ⚠️ Access Denied</b>\n\nOnly The Bot Owner Can Control This Userbot.</blockquote>", parse_mode="html")
                        return
                        
                    sess = database.get_session(phone_target)
                    if not sess:
                        await event.reply(f"<blockquote><b>» ❌ Userbot Session Not Found :</b> <code>{phone_target}</code></blockquote>", parse_mode="html")
                        return
                        
                    target_user_id = sess.get("user_id")
                    from .my_bots import show_bot_dashboard
                    # Admins have full access to control without replacing their own userbot dashboard
                    await show_bot_dashboard(
                        event, 
                        sess.get("phone", phone_target), 
                        user_id, 
                        flash_message=f"<blockquote><b>» 👑 Owner Access :</b> Controlling Userbot <code>{sess.get('phone', phone_target)}</code> (User: <code>{target_user_id}</code>)</blockquote>"
                    )
                    return

            onboarded = await check_onboarding(client, event)
            if onboarded:
                await show_main_menu(event, event.sender_id)
        except Exception as top_err:
            logger.error(f"Error handling /start: {top_err}", exc_info=True)
            try:
                await event.respond(
                    f"<blockquote><b>» ⚡ {config.BOT_NAME} Dashboard</b>\n\nTap below to open your control panel:</blockquote>",
                    buttons=[[styled_button("🚀 Open Menu", "menu_start", style="primary")]]
                )
            except Exception:
                pass

    @client.on(events.CallbackQuery(pattern="^menu_start$"))
    async def menu_start_callback(event):
        await event.answer()
        await show_main_menu(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern="^close_menu$"))
    async def close_menu_callback(event):
        await event.answer()
        try:
            await event.delete()
        except Exception:
            pass

    @client.on(events.CallbackQuery(pattern=r"^set_lang_(en|hi|ru|ja|fr|de|zh|ar)$"))
    async def set_lang_callback(event):
        lang_code = event.pattern_match.group(1)
        user_id = event.sender_id
        
        user = database.get_user(user_id)
        if not user:
            user = models.create_default_user(user_id)
            
        user["language"] = lang_code
        database.save_user(user)
        
        # Respond to callback query
        await event.answer()
        
        # Progress to next step of onboarding (TOS)
        await check_onboarding(client, event)

    @client.on(events.CallbackQuery(pattern="^accept_tos$"))
    async def accept_tos_callback(event):
        user_id = event.sender_id
        user = database.get_user(user_id)
        
        if user:
            user["tos_accepted"] = True
            database.save_user(user)
            
        await event.answer()
        
        # Display congratulations and open main menu
        lang = user.get("language", "en") if user else "en"
        await event.respond(get_text("tos_accepted_msg", lang))
        await show_main_menu(event, user_id)
