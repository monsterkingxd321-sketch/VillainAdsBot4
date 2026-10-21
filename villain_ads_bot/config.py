import os
import random
from dotenv import load_dotenv

load_dotenv()

# Bot Name Configuration
BOT_NAME = os.getenv("BOT_NAME", "𝗫𝗧𝗥 𝗔𝗗 𝗕𝗢𝗧")
DEFAULT_DB_NAME = "xtr_ad_bot"

def _clean_env(val: str, default: str = "") -> str:
    """Strips whitespace and surrounding quotes from environment variable values."""
    if val is None:
        return default
    s = str(val).strip()
    while len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
        s = s[1:-1].strip()
    return s

# Telegram API credentials for userbots
api_id_val = _clean_env(os.getenv("API_ID", ""))
API_ID = int(api_id_val) if api_id_val.isdigit() else 0
API_HASH = _clean_env(os.getenv("API_HASH", ""))


def get_admin_api_pairs() -> list:
    """
    Returns the API_ID / API_HASH pairs that were added from the ADMIN PANEL.

    They are stored inside the MongoDB global settings document under
    "api_credentials" as a list of {"api_id": ..., "api_hash": ...} objects.
    The import is done lazily so that `config` never imports `database`
    at module level (that would be a circular import).
    """
    pairs = []
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        for entry in settings.get("api_credentials", []) or []:
            if not isinstance(entry, dict):
                continue
            aid = str(entry.get("api_id", "")).strip()
            ahash = str(entry.get("api_hash", "")).strip()
            if aid.isdigit() and ahash:
                pairs.append((int(aid), ahash))
    except Exception:
        pass
    return pairs


def get_all_api_pairs():
    """
    Returns every usable (API_ID, API_HASH) pair.

    Order:
      1. API pairs configured from the admin panel (MongoDB)
      2. API_ID / API_HASH from the environment
      3. API_ID1 / API_HASH1 ... API_ID99 / API_HASH99 from the environment
    """
    pairs = []
    seen = set()

    def _add(aid, ahash):
        key = (int(aid), str(ahash))
        if key in seen:
            return
        seen.add(key)
        pairs.append(key)

    for aid, ahash in get_admin_api_pairs():
        _add(aid, ahash)

    if API_ID and API_HASH:
        _add(API_ID, API_HASH)

    for i in range(1, 100):
        aid = _clean_env(os.getenv(f"API_ID{i}", ""))
        ahash = _clean_env(os.getenv(f"API_HASH{i}", ""))
        if aid and ahash and aid.isdigit():
            _add(int(aid), ahash)
    return pairs

def get_random_api_id_hash():
    """
    Returns a random (API_ID, API_HASH) from the environment to avoid floodwaits.
    It checks API_ID, API_HASH, API_ID1, API_HASH1, ... up to 100.
    """
    pairs = get_all_api_pairs()
    if pairs:
        return random.choice(pairs)
    return (0, "")

def get_deterministic_api_id_hash(seed_key: str = None):
    """
    Returns a deterministic (API_ID, API_HASH) based on the seed_key (session_id/phone),
    ensuring that a session always uses the exact same API credentials across reconnects and restarts.
    """
    pairs = get_all_api_pairs()
    if not pairs:
        return (0, "")
    if seed_key:
        import hashlib
        h = int(hashlib.md5(str(seed_key).encode()).hexdigest(), 16)
        return pairs[h % len(pairs)]
    return pairs[0]

# Bot token for the main manager bot
BOT_TOKEN = _clean_env(os.getenv("BOT_TOKEN", ""))

# MongoDB connection URI
MONGODB_URI = _clean_env(os.getenv("MONGODB_URI") or os.getenv("MONGO_DB_URI") or "")

# Log group / channel used for audit logs AND for error reports.
LOG_GROUP_ID = int(_clean_env(os.getenv("LOG_GROUP_ID", "-1004354441869")) or -1004354441869)



# Parse original admin IDs (which can be whitelisted using ORIGINAL_ADMIN_IDS or OWNER_ID)
original_admin_ids_str = os.getenv("ORIGINAL_ADMIN_IDS") or os.getenv("original_admin_ids") or os.getenv("OWNER_ID") or os.getenv("owner_id") or ""
ORIGINAL_ADMIN_IDS = set()
if original_admin_ids_str:
    for x in original_admin_ids_str.split(","):
        x = x.strip().replace('"', '').replace("'", "")
        if x.isdigit():
            ORIGINAL_ADMIN_IDS.add(int(x))

# Directory for local session files (absolute so the bot works from any CWD)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "user_data")
DOWNLOADS_DIR = os.path.join(PROJECT_ROOT, "downloads")

# Limit the maximum number of running userbots to prevent Out of Memory (OOM) on platforms like Railway
MAX_RUNNING_USERBOTS = int(os.getenv("MAX_RUNNING_USERBOTS", "99999"))

# Default values for global settings
DEFAULT_GLOBAL_SETTINGS = {
    "price_per_id": float(os.getenv("PRICE_PER_ID", "10.0")),            # Price per extra slot (INR)
    "price_per_slot": float(os.getenv("PRICE_PER_SLOT", os.getenv("PRICE_PER_ID", "10.0"))),  # Price per slot in INR (admin managed)
    "force_join_links": [x.strip() for x in os.getenv("FORCE_JOIN_LINKS", "https://t.me/TheVillainActive,https://t.me/+WzyoJkg4bzhlNTFl").split(",") if x.strip()], # List of usernames/links to force join
    "log_group_id": int(os.getenv("LOG_GROUP_ID", "-1004354441869")),   # Log group/channel ID
    "branding_username": os.getenv("BRANDING_USERNAME", None),        # Bot username to append (e.g. via @MyBot)
    "branding_duration": int(os.getenv("BRANDING_DURATION", "30")),          # Duration of branding in days
    "branding_name_enabled": os.getenv("BRANDING_NAME_ENABLED", "True").lower() == "true",    # Enable name branding
    "branding_bio_enabled": os.getenv("BRANDING_BIO_ENABLED", "True").lower() == "true",     # Enable bio branding
    "branding_name_text": os.getenv("BRANDING_NAME_TEXT", None),       # Custom name branding suffix text
    "branding_bio_text": os.getenv("BRANDING_BIO_TEXT", None),        # Custom bio branding suffix text
    "branding_lock": True,                                          # Users can never remove the branding
    "anti_spam_protection": True,                                  # Warn the owner about a young account (never blocks)
    "api_credentials": [],                                         # API_ID / API_HASH pairs added from the admin panel
    "music_api_key": os.getenv("MEOW_API_KEY", None),               # Yuki / Meow music API key (admin managed)
    "music_api_url": os.getenv("MEOW_API_URL", "https://music.yukiapi.site"),  # Yuki / Meow music API base URL
    "music_backup_api_url": os.getenv("MEOW_BACKUP_API_URL", "https://play.yukiapi.site"),  # Failover gateway
    "start_image": os.getenv("START_IMAGE", "https://files.catbox.moe/jnlroe.jpg"),              # File ID of the start image
    "ping_image": os.getenv("PING_IMAGE", "https://files.catbox.moe/7qgokb.jpg"),               # File ID of the ping image
    "help_image": os.getenv("HELP_IMAGE", "https://files.catbox.moe/xxpn14.jpg"),               # File ID of the help image
    "admins": list(ORIGINAL_ADMIN_IDS), # List of admins
    "admin_roles": {},                # { "<user_id>": "owner" | "moderator" | "support" }
    "unlimited_slots": False,         # When True NOBODY is limited (global switch)
    "default_slot_limit": 1,          # The free slots of a new user (0 = unlimited)
    "gpt_api_key": os.getenv("GPT_API_KEY", None),               # Global OpenAI API Key for GPT mode (optional)
    "maintenance_mode": os.getenv("MAINTENANCE_MODE", "False").lower() == "true",         # Maintenance guard
    "upi_id": os.getenv("UPI_ID", "raunitkumar01@fam"),          # Admin UPI ID for payments
    "usdt_bep20_address": os.getenv("USDT_BEP20_ADDRESS", "0x0000000000000000000000000000000000000000"), # USDT BEP20 Address
    "ton_address": os.getenv("TON_ADDRESS", "UQ000000000000000000000000000000000000000000000000"), # TON Address
    "usdt_rate": float(os.getenv("USDT_RATE", "90.0")),              # 1 USDT = 90 INR
    "ton_rate": float(os.getenv("TON_RATE", "500.0")),               # 1 TON = 500 INR
    "support_channel": os.getenv("SUPPORT_CHANNEL") or os.getenv("support_channel") or "https://t.me/TheVillainActive",                 # Support channel invite link
    "support_group": os.getenv("SUPPORT_GROUP") or os.getenv("support_group") or "https://t.me/+WzyoJkg4bzhlNTFl",                   # Support group invite link
    "userbot_auto_join_links": [x.strip() for x in os.getenv("USERBOT_AUTO_JOIN_LINKS", "https://t.me/TheVillainActive,https://t.me/+WzyoJkg4bzhlNTFl").split(",") if x.strip()], # Auto-join links for new userbots
    "referral_commission": float(os.getenv("REFERRAL_COMMISSION", "0.10")),        # 10% commission on slot upgrades
    "subscription_plans": [             # Dynamic slot subscription plans
        {"id": "std30", "days": 30, "price": float(os.getenv("PRICE_PER_ID", "10.0")), "button_name": "Standard 30 Days"}
    ]
}

# Owner Button Configuration (Read from Env)
OWNER_NAME = os.getenv("OWNER_NAME") or os.getenv("owner_name") or os.getenv("OWNER_1_NAME") or "👑 Owner"
OWNER_URL = os.getenv("OWNER_URL") or os.getenv("owner_url") or os.getenv("OWNER_1_URL") or "https://t.me/v90001"
OWNER_1_NAME = OWNER_NAME
OWNER_1_URL = OWNER_URL

# Music API Configuration (Yuki API)
MEOW_API_URL = os.getenv("MEOW_API_URL", "https://music.yukiapi.site")
MEOW_BACKUP_API_URL = os.getenv("MEOW_BACKUP_API_URL", "https://play.yukiapi.site")
MEOW_API_KEY = os.getenv("MEOW_API_KEY", "yuki_8641adadf64870f3065dd502c8ee30f1") # 🔑 Active Yuki API Key
YUKI_API_URL = MEOW_API_URL
YUKI_BACKUP_API_URL = MEOW_BACKUP_API_URL
YUKI_API_KEY = MEOW_API_KEY



