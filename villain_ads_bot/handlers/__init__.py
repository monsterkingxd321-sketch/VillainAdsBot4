def register_all_handlers(client):
    """
    Registers all bot event handlers onto the client instance.
    """
    from villain_ads_bot.core import utils
    from telethon.tl.types import PeerUser, InputPeerUser, User
    
    client.parse_mode = "html"

    from . import start, callbacks, add_bot, my_bots, settings, admin, status, payments, payments_extended, player
    
    # Register the callbacks handler first to intercept queries and display helper popups
    callbacks.register_handlers(client)
    
    # Register core command and workflow handlers
    start.register_handlers(client)
    add_bot.register_handlers(client)
    my_bots.register_handlers(client)
    settings.register_handlers(client)
    admin.register_handlers(client)
    status.register_handlers(client)
    payments.register_handlers(client)
    payments_extended.register_handlers(client)
    player.register_handlers(client)

