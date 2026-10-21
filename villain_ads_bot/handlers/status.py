import logging
from telethon import events
from villain_ads_bot.data import database
from villain_ads_bot.core import utils
from villain_ads_bot.core import ui

logger = logging.getLogger(__name__)

async def render_status_dashboard(event, user_id: int):
    """
    Renders the Status Dashboard summarizing statistics for all bots owned by this user.
    """
    user = database.get_user(user_id)
    lang = user.get("language", "en") if user else "en"
    
    sessions = database.get_sessions(user_id)
    
    total_sessions = len(sessions)
    total_groups = sum(s.get("stats", {}).get("group_count", 0) for s in sessions)
    total_users = sum(s.get("stats", {}).get("user_count", 0) for s in sessions)
    total_broadcasts = sum(s.get("stats", {}).get("broadcast_count", 0) for s in sessions)
    total_welcomed = sum(len(s.get("stats", {}).get("welcomed_users", [])) for s in sessions)
    
    # Text formatting using blockquotes and bold
    text = (
        f"{ui.card(utils.get_text('premium_status_title', lang), utils.get_text('premium_status_sub', lang), 'Everything your userbots did so far', '📊')}\n\n"
        f"{ui.kv('Connected bots', str(total_sessions), '📱')}\n"
        f"{ui.kv('Managed groups', str(total_groups), '👥')}\n"
        f"{ui.kv('Contacts / users', str(total_users), '👤')}\n"
        f"{ui.kv('Broadcasts done', str(total_broadcasts), '✉️')}\n"
        f"{ui.kv('Users welcomed', str(total_welcomed), '👋')}\n\n"
        f"{ui.footer()}"
    )
    
    buttons = [[utils.styled_button(utils.get_text("back_to_menu", lang), "menu_start", style="primary")]]
    
    global_settings = database.get_global_settings()
    ping_image = global_settings.get("ping_image")
    
    try:
        if ping_image:
            await event.respond(text, file=ping_image, buttons=buttons)
        else:
            await event.respond(text, buttons=buttons)
    except Exception as e:
        logger.error(f"Error rendering status screen: {e}")
        await event.respond(text, buttons=buttons)

def register_handlers(client):
    
    @client.on(events.NewMessage(pattern="/status"))
    async def status_command(event):
        if not event.is_private:
            return
        # Verify onboarding
        from villain_ads_bot.handlers.start import check_onboarding
        onboarded = await check_onboarding(client, event)
        if onboarded:
            await render_status_dashboard(event, event.sender_id)

    @client.on(events.CallbackQuery(pattern="^menu_status$"))
    async def status_callback(event):
        await render_status_dashboard(event, event.sender_id)
