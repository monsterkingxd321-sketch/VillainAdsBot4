import logging
from telethon import events
from villain_ads_bot.core import utils

logger = logging.getLogger(__name__)

def register_handlers(client):
    @client.on(events.CallbackQuery)
    async def global_callback_handler(event):
        """
        Global callback guard: applies security filters and cleans up transient states.
        """
        try:
            # Decode callback data
            data = event.data.decode("utf-8") if isinstance(event.data, bytes) else event.data
            if not data:
                return
                
            if data.startswith("approve_payment_") or data.startswith("reject_payment_") or data.startswith("payment_info_"):
                return
                
            # Fast Ban/Maintenance Guard for callbacks
            from villain_ads_bot.data import database
            from villain_ads_bot import config
            user_id = event.sender_id
            if user_id:
                user = database.get_user(user_id)
                if user and user.get("is_banned", False):
                    try:
                        await event.answer("🚫 You are banned from using this bot.", alert=True)
                    except Exception:
                        pass
                    raise events.StopPropagation
                    
                global_settings = database.get_global_settings()
                admins = global_settings.get("admins", [])
                is_admin = user_id in admins or user_id in config.ORIGINAL_ADMIN_IDS
                if global_settings.get("maintenance_mode", False) and not is_admin:
                    try:
                        await event.answer("🔧 Bot is under maintenance. Please try again later.", alert=True)
                    except Exception:
                        pass
                    raise events.StopPropagation
            
            # Clean up active states only on main menu navigation or explicit exit
            if data in ("menu_start", "admin_exit_impersonation"):
                try:
                    from .admin import _admin_action_states, _admin_plan_temp
                    from .my_bots import _bot_action_states
                    from .payments_extended import _payment_user_states
                    from .add_bot import clean_login_state
                    
                    _admin_action_states.pop(user_id, None)
                    _admin_plan_temp.pop(user_id, None)
                    _bot_action_states.pop(user_id, None)
                    _payment_user_states.pop(user_id, None)
                    await clean_login_state(user_id)
                except Exception as cleanup_err:
                    logger.error(f"Error cleaning up states on callback: {cleanup_err}")

        except events.StopPropagation:
            raise
        except Exception as e:
            logger.error(f"Error in global callback handler: {e}")


