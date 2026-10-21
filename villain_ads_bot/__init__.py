"""
Villain Ads Bot — a Telegram userbot management suite.

Package layout
--------------
    villain_ads_bot/
        config.py          Environment / default settings
        translations.py    Multi-language message catalogue
        thumb_gen.py       Now-playing thumbnail renderer
        core/              Framework-agnostic helpers
            utils.py           Shared helpers (buttons, guards, text tools)
            error_reporter.py  Log-group + owner error reporting
            ui.py              Premium UI card builders
            pricing.py         INR <-> USDT / TON conversion
        data/               Persistence layer
            database.py        MongoDB access layer
            models.py          Document factories
        services/           Domain services
            userbot.py         The UserBot runtime (Telethon + PyTgCalls)
            userbot_manager.py UserBot registry / clone / branding / sessions
            youtube.py         YouTube / Yuki music API
        handlers/           Telegram event handlers
"""

__version__ = "4.1.0"
__all__ = ["config", "translations", "core", "data", "services", "handlers"]
