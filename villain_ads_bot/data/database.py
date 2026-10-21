import os
import logging
import time
from typing import Dict, Any, List, Optional
from pymongo import MongoClient
from concurrent.futures import ThreadPoolExecutor

_db_executor = ThreadPoolExecutor(max_workers=5)
from villain_ads_bot import config

logger = logging.getLogger(__name__)

# Global database variables
_mongo_client: Optional[MongoClient] = None
_db = None

# In-memory cache for settings and channels to avoid heavy synchronous MongoDB queries
_cached_global_settings = None
_cached_settings_time = 0.0
_cached_force_channels = None
_cached_channels_time = 0.0

def db_init():
    """
    Initializes the MongoDB connection.
    Raises an exception if the connection fails or if MONGODB_URI is not set.
    """
    global _mongo_client, _db
    if not config.MONGODB_URI:
        raise ValueError("MONGODB_URI is not set in the environment variables.")
        
    try:
        logger.info("Connecting to MongoDB...")
        _mongo_client = MongoClient(config.MONGODB_URI, serverSelectionTimeoutMS=5000, maxPoolSize=10)
        # Force a connection check
        _mongo_client.server_info()
        try:
            _db = _mongo_client.get_database()
        except Exception:
            _db = _mongo_client.get_database(config.DEFAULT_DB_NAME)
        logger.info("Successfully connected to MongoDB.")
        
        # Create indexes to prevent slow collection scans as database grows
        try:
            _db.users.create_index("user_id", unique=True)
        except Exception as idx_err:
            logger.warning(f"Could not create unique index on users.user_id: {idx_err}")
            _db.users.create_index("user_id")
            
        _db.users.create_index("username")
        
        try:
            _db.sessions.create_index("session_id", unique=True)
        except Exception as idx_err:
            logger.warning(f"Could not create unique index on sessions.session_id: {idx_err}")
            _db.sessions.create_index("session_id")
            
        _db.sessions.create_index("user_id")
        
        try:
            _db.payments.create_index("payment_id", unique=True)
        except Exception as idx_err:
            logger.warning(f"Could not create unique index on payments.payment_id: {idx_err}")
            _db.payments.create_index("payment_id")
            
        _db.payments.create_index("user_id")
        _db.payments.create_index("utr_code")
        
        try:
            _db.coupons.create_index("code", unique=True)
        except Exception as idx_err:
            logger.warning(f"Could not create unique index on coupons.code: {idx_err}")
            _db.coupons.create_index("code")
            
        _db.coupon_usage.create_index([("code", 1), ("user_id", 1)])
        
        try:
            _db.force_channels.create_index("channel_id", unique=True)
        except Exception as idx_err:
            logger.warning(f"Could not create unique index on force_channels.channel_id: {idx_err}")
            _db.force_channels.create_index("channel_id")
            
        logger.info("Database indexes checked/created successfully.")
        
        # Initialize default settings if not exists
        settings = _db.settings.find_one({"id": "global"})
        if not settings:
            settings = dict(config.DEFAULT_GLOBAL_SETTINGS)
            settings["id"] = "global"
            _db.settings.insert_one(settings)
            logger.info("Initialized default global settings in MongoDB.")
            
    except Exception as e:
        logger.critical(f"Failed to connect to MongoDB: {e}")
        raise e

# Cache dictionary for user records: { user_id: (expiry_timestamp, user_data_dict) }
_user_cache: Dict[int, tuple] = {}

# ==================== User CRUD Operations ====================
def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    global _user_cache
    now = time.time()
    uid = int(user_id)
    
    # Check cache
    if uid in _user_cache:
        expiry, cached_user = _user_cache[uid]
        if now < expiry:
            return dict(cached_user) if cached_user else None
            
    # Fetch from MongoDB
    user = _db.users.find_one({"user_id": {"$in": [uid, str(uid)]}})
    
    # Cache for 5 seconds
    _user_cache[uid] = (now + 300.0, dict(user) if user else None)
    return user

def save_user(user_data: Dict[str, Any]):
    global _user_cache
    uid = int(user_data["user_id"])
    user_data["user_id"] = uid
    
    # Update cache immediately for instant bot response
    _user_cache[uid] = (time.time() + 300.0, dict(user_data))
    
    # Save to MongoDB in background
    if _db is not None:
        _db_executor.submit(lambda: _db.users.replace_one({"user_id": uid}, user_data, upsert=True))

def get_all_users() -> List[Dict[str, Any]]:
    return list(_db.users.find({}))

_cached_sessions = {}

_cached_get_sessions = {}

def get_sessions(user_id: Optional[int] = None, include_bytes: bool = False) -> List[Dict[str, Any]]:
    global _cached_get_sessions
    cache_key = f"{user_id}_{include_bytes}"
    now = time.time()
    
    if cache_key in _cached_get_sessions:
        expiry, data = _cached_get_sessions[cache_key]
        if now < expiry:
            return [dict(d) for d in data]

    projection = None if include_bytes else {"session_bytes": 0}
    if user_id is not None:
        query = {"user_id": {"$in": [int(user_id), str(user_id)]}}
    else:
        query = {}
        
    results = list(_db.sessions.find(query, projection))
    _cached_get_sessions[cache_key] = (now + 15.0, [dict(r) for r in results])  # 15s cache
    return results

def get_session(session_id: str, include_bytes: bool = False) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
        
    session_id_str = str(session_id).strip()
    
    # Check cache if bytes not requested
    if not include_bytes:
        cached = _cached_sessions.get(session_id_str)
        if cached and (time.time() - cached['time'] < 60):  # 60s cache
            return cached['data']
            
    projection = None if include_bytes else {"session_bytes": 0}
    sess = _db.sessions.find_one({"session_id": session_id_str}, projection)
    if not sess:
        sess = _db.sessions.find_one({"phone": session_id_str}, projection)
    if not sess:
        alt = session_id_str.lstrip("+") if session_id_str.startswith("+") else f"+{session_id_str}"
        sess = _db.sessions.find_one({"$or": [{"session_id": alt}, {"phone": alt}]}, projection)
        
    if sess and not include_bytes:
        _cached_sessions[session_id_str] = {'data': sess, 'time': time.time()}
        if sess.get("phone"):
            _cached_sessions[str(sess["phone"])] = {'data': sess, 'time': time.time()}
        
    return sess

def save_session(session_data: Dict[str, Any]):
    global _cached_get_sessions
    _cached_get_sessions.clear()
    try:
        session_data["user_id"] = int(session_data["user_id"])
    except (TypeError, ValueError):
        # Non numeric owner ids (e.g. imported from a disk folder) are kept as-is.
        pass
    
    # Update cache
    sid = session_data.get("session_id")
    phone = session_data.get("phone")
    if sid:
        _cached_sessions[str(sid)] = {'data': session_data, 'time': time.time()}
    if phone:
        _cached_sessions[str(phone)] = {'data': session_data, 'time': time.time()}
        
    if _db is not None:
        _db_executor.submit(lambda: _db.sessions.update_one(
            {"session_id": session_data["session_id"]},
            {"$set": session_data},
            upsert=True
        ))

def delete_session(session_id: str):
    global _cached_get_sessions
    _cached_get_sessions.clear()
    if not session_id:
        return
    session_id_str = str(session_id).strip()
    
    # Clear cache
    _cached_sessions.pop(session_id_str, None)
    alt = session_id_str.lstrip("+") if session_id_str.startswith("+") else f"+{session_id_str}"
    _db.sessions.delete_many({
        "$or": [
            {"session_id": session_id_str},
            {"phone": session_id_str},
            {"session_id": alt},
            {"phone": alt}
        ]
    })

# ==================== Payment CRUD Operations ====================
def get_payment_requests(user_id: Optional[int] = None) -> List[Dict[str, Any]]:
    if user_id is not None:
        query = {"user_id": {"$in": [int(user_id), str(user_id)]}}
    else:
        query = {}
    return list(_db.payments.find(query))

def get_payment_request(payment_id: str) -> Optional[Dict[str, Any]]:
    return _db.payments.find_one({"payment_id": payment_id})

def save_payment_request(payment_data: Dict[str, Any]):
    try:
        payment_data["user_id"] = int(payment_data["user_id"])
    except (TypeError, ValueError):
        pass
    try:
        _db.payments.replace_one({"payment_id": payment_data["payment_id"]}, payment_data, upsert=True)
    except Exception as e:
        # A duplicate key (unique index) must never crash the payment flow.
        logger.warning(f"save_payment_request failed for {payment_data.get('payment_id')}: {e}")
        raise


def get_payment_request_by_reference(reference: str) -> Optional[Dict[str, Any]]:
    """Finds a payment request by its human friendly reference."""
    if not reference:
        return None
    try:
        return _db.payments.find_one({"reference": reference})
    except Exception:
        return None

# ==================== Audit Log CRUD ====================
def log_audit(actor_id, action: str, target=None, details: str = "", meta: Dict[str, Any] = None):
    """
    Records an action performed by an admin.

    `actor_id` may be None for system actions.
    """
    try:
        doc = {
            "ts": time.time(),
            "actor_id": int(actor_id) if actor_id else None,
            "actor_name": None,
            "action": str(action),
            "target": str(target) if target is not None else None,
            "details": str(details or "")[:1000],
            "meta": meta or {},
        }
        _db_executor.submit(lambda: _db.audit_log.insert_one(doc))
        return doc
    except Exception as e:
        logger.warning(f"Could not write the audit log: {e}")
        return None


def get_audit_log(limit: int = 100, action: str = None, actor_id: int = None,
                  target: str = None) -> List[Dict[str, Any]]:
    """Returns the newest audit entries first."""
    try:
        query = {}
        if action:
            query["action"] = action
        if actor_id:
            query["actor_id"] = int(actor_id)
        if target:
            query["target"] = str(target)
        return list(_db.audit_log.find(query).sort("ts", -1).limit(int(limit)))
    except Exception as e:
        logger.warning(f"Could not read the audit log: {e}")
        return []


def count_audit_log() -> int:
    try:
        return _db.audit_log.count_documents({})
    except Exception:
        return 0


def purge_audit_log(days: int = 90) -> int:
    """Deletes audit entries older than `days` days. Returns the amount removed."""
    try:
        cutoff = time.time() - (int(days) * 86400)
        res = _db.audit_log.delete_many({"ts": {"$lt": cutoff}})
        return getattr(res, "deleted_count", 0)
    except Exception as e:
        logger.warning(f"Could not purge the audit log: {e}")
        return 0


# ==================== Admin Roles ====================
ROLE_OWNER = "owner"
ROLE_MODERATOR = "moderator"
ROLE_SUPPORT = "support"
ROLE_LEVELS = {ROLE_OWNER: 3, ROLE_MODERATOR: 2, ROLE_SUPPORT: 1}
ROLE_LABELS = {ROLE_OWNER: "👑 Owner", ROLE_MODERATOR: "🧙 Moderator", ROLE_SUPPORT: "💎 Support"}


def get_admin_roles() -> Dict[str, str]:
    """Returns {str(user_id): role} for every admin that has an explicit role."""
    try:
        settings = get_global_settings() or {}
        roles = settings.get("admin_roles", {}) or {}
        return {str(k): str(v) for k, v in roles.items() if v in ROLE_LEVELS}
    except Exception:
        return {}


def get_admin_role(user_id: int) -> str:
    """
    Returns the role of an admin.

    The root owners (ORIGINAL_ADMIN_IDS) are ALWAYS owners so nobody can
    lock themselves out.
    """
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return ROLE_SUPPORT
    if uid in (config.ORIGINAL_ADMIN_IDS or set()):
        return ROLE_OWNER
    return get_admin_roles().get(str(uid), ROLE_SUPPORT)


def set_admin_role(user_id: int, role: str) -> bool:
    """Sets the role of an admin. Returns True on success."""
    if role not in ROLE_LEVELS:
        return False
    try:
        settings = get_global_settings() or {}
        roles = dict(settings.get("admin_roles", {}) or {})
        roles[str(int(user_id))] = role
        settings["admin_roles"] = roles
        save_global_settings(settings)
        log_audit(None, "admin.role_set", str(user_id), f"role={role}")
        return True
    except Exception as e:
        logger.warning(f"Could not set the admin role: {e}")
        return False


def remove_admin_role(user_id: int) -> bool:
    try:
        settings = get_global_settings() or {}
        roles = dict(settings.get("admin_roles", {}) or {})
        roles.pop(str(int(user_id)), None)
        settings["admin_roles"] = roles
        save_global_settings(settings)
        return True
    except Exception:
        return False


# ==================== Settings CRUD Operations ====================
def get_global_settings() -> Dict[str, Any]:
    global _cached_global_settings, _cached_settings_time
    now = time.time()
    
    # Cache settings for 10 seconds to reduce MongoDB round-trips
    if _cached_global_settings is None or (now - _cached_settings_time > 300.0):
        settings = _db.settings.find_one({"id": "global"})
        if not settings:
            settings = dict(config.DEFAULT_GLOBAL_SETTINGS)
            settings["id"] = "global"
            _db.settings.insert_one(settings)
        else:
            # Merge defaults to ensure new settings keys are backfilled
            updated = False
            for k, v in config.DEFAULT_GLOBAL_SETTINGS.items():
                if k not in settings:
                    settings[k] = v
                    updated = True
            
            # Self-healing: if start_image in DB is the old default, update it to the new one
            if settings.get("start_image") == "https://files.catbox.moe/syoba0.jpg":
                settings["start_image"] = config.DEFAULT_GLOBAL_SETTINGS["start_image"]
                updated = True

            # Self-healing: if help_image in DB is the old default, update it to the new one
            if settings.get("help_image") == "https://files.catbox.moe/f9b2f1.jpg":
                settings["help_image"] = config.DEFAULT_GLOBAL_SETTINGS["help_image"]
                updated = True
                
            if updated:
                _db.settings.replace_one({"id": "global"}, settings)
                
        # Override with current environment values at runtime so Railway environment changes take priority
        import os
        
        # The payment wallets are ADMIN managed: the environment value is only
        # used while the panel value is still empty or a placeholder, so the
        # admin panel can actually set a real address.
        for env_key, settings_key in [
            ("UPI_ID", "upi_id"),
            ("USDT_BEP20_ADDRESS", "usdt_bep20_address"),
            ("TON_ADDRESS", "ton_address"),
        ]:
            env_val = os.getenv(env_key) or os.getenv(env_key.lower())
            current = str(settings.get(settings_key) or "").strip()
            placeholder_chars = {"0", "x", "U", "Q"}
            is_placeholder = (not current) or (
                bool(current) and set(current) <= placeholder_chars
            )
            if env_val and env_val.strip() and is_placeholder:
                settings[settings_key] = env_val.strip()
                
        # String/URL overrides (exclude images so admin panel removals/sets persist in MongoDB)
        for env_key, settings_key in [
            ("SUPPORT_CHANNEL", "support_channel"),
            ("SUPPORT_GROUP", "support_group"),
            ("GPT_API_KEY", "gpt_api_key"),
            ("BRANDING_USERNAME", "branding_username"),
            ("BRANDING_NAME_TEXT", "branding_name_text"),
            ("BRANDING_BIO_TEXT", "branding_bio_text"),
        ]:
            env_val = os.getenv(env_key) or os.getenv(env_key.lower())
            # IMPORTANT: an EMPTY env var must NOT override the value that was
            # saved from the admin panel (otherwise branding could never be set).
            if env_val is not None and env_val.strip():
                settings[settings_key] = env_val.strip()

        # Numeric overrides
        for env_key, settings_key, val_type in [
            ("PRICE_PER_ID", "price_per_id", float),
            ("USDT_RATE", "usdt_rate", float),
            ("TON_RATE", "ton_rate", float),
            ("BRANDING_DURATION", "branding_duration", int),
            ("LOG_GROUP_ID", "log_group_id", int),
            ("REFERRAL_COMMISSION", "referral_commission", float),
        ]:
            env_val = os.getenv(env_key) or os.getenv(env_key.lower())
            if env_val is not None and env_val.strip():
                try:
                    settings[settings_key] = val_type(env_val.strip())
                except ValueError:
                    pass

        # Boolean overrides
        for env_key, settings_key in [
            ("BRANDING_NAME_ENABLED", "branding_name_enabled"),
            ("BRANDING_BIO_ENABLED", "branding_bio_enabled"),
            ("MAINTENANCE_MODE", "maintenance_mode"),
        ]:
            env_val = os.getenv(env_key) or os.getenv(env_key.lower())
            if env_val is not None and env_val.strip():
                settings[settings_key] = env_val.strip().lower() == "true"

        # List overrides
        for env_key, settings_key in [
            ("FORCE_JOIN_LINKS", "force_join_links"),
            ("USERBOT_AUTO_JOIN_LINKS", "userbot_auto_join_links"),
        ]:
            env_val = os.getenv(env_key) or os.getenv(env_key.lower())
            if env_val is not None:
                settings[settings_key] = [x.strip() for x in env_val.split(",") if x.strip()]
        
        # Ensure ORIGINAL_ADMIN_IDS are always whitelisted
        for admin_id in config.ORIGINAL_ADMIN_IDS:
            if admin_id not in settings.setdefault("admins", []):
                settings["admins"].append(admin_id)
                
        _cached_global_settings = settings
        _cached_settings_time = now
        
    return dict(_cached_global_settings)

def save_global_settings(settings_data: Dict[str, Any]):
    global _cached_global_settings, _cached_settings_time
    settings_data["id"] = "global"
    
    # Update cache immediately
    _cached_global_settings = dict(settings_data)
    _cached_settings_time = time.time()
    
    # Save in background
    if _db is not None:
        _db_executor.submit(lambda: _db.settings.replace_one({"id": "global"}, settings_data, upsert=True))

# ==================== Force Channels CRUD ====================
def get_force_channels() -> List[Dict[str, Any]]:
    global _cached_force_channels, _cached_channels_time
    now = time.time()
    
    # Cache force subscribe channels list for 10 seconds
    if _cached_force_channels is None or (now - _cached_channels_time > 10.0):
        _cached_force_channels = list(_db.force_channels.find({}))
        _cached_channels_time = now
        
    return _cached_force_channels

def add_force_channel(channel_id: str, invite_link: str, channel_name: str):
    global _cached_force_channels
    data = {
        "channel_id": channel_id,
        "channel_link": invite_link,
        "channel_name": channel_name
    }
    _db.force_channels.replace_one({"channel_id": channel_id}, data, upsert=True)
    # Invalidate cache
    _cached_force_channels = None

def delete_force_channel(channel_id: str):
    global _cached_force_channels
    _db.force_channels.delete_one({"channel_id": channel_id})
    # Invalidate cache
    _cached_force_channels = None

# ==================== Coupons CRUD ====================
def get_coupons() -> List[Dict[str, Any]]:
    return list(_db.coupons.find({}))

def get_coupon(code: str) -> Optional[Dict[str, Any]]:
    return _db.coupons.find_one({"code": code})

def save_coupon(coupon_data: Dict[str, Any]):
    _db.coupons.replace_one({"code": coupon_data["code"]}, coupon_data, upsert=True)

def delete_coupon(code: str):
    _db.coupons.delete_one({"code": code})

# ==================== Coupon Usage CRUD ====================
def has_used_coupon(code: str, user_id: int) -> bool:
    return _db.coupon_usage.find_one({"code": code, "user_id": {"$in": [int(user_id), str(user_id)]}}) is not None

def save_coupon_usage(code: str, user_id: int):
    data = {
        "code": code,
        "user_id": int(user_id),
        "timestamp": time.time()
    }
    _db.coupon_usage.insert_one(data)

# ==================== User Referral & Lookup Helpers ====================
def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """
    Looks up a user by username in a case-insensitive manner.
    """
    username = username.strip().replace("@", "")
    import re
    return _db.users.find_one({"username": re.compile(f"^{username}$", re.IGNORECASE)})

def count_referred_users(user_id: int) -> int:
    """
    Counts the number of users referred by the given user_id.
    """
    return _db.users.count_documents({"referred_by": {"$in": [int(user_id), str(user_id)]}})

def get_payment_request_by_utr_and_status(utr: str, status: str = "pending") -> Optional[Dict[str, Any]]:
    """
    Looks up a payment request by UTR and status directly using index.

    Used to make sure the same transaction reference is never used twice.
    """
    if not utr:
        return None
    try:
        return _db.payments.find_one({"utr_code": utr, "status": status})
    except Exception:
        return None

# ==================== Chat & Thumbnail Settings CRUD ====================
_thumbnail_settings_cache: Dict[int, bool] = {}

def get_thumbnail_setting(chat_or_user_id: int) -> bool:
    """
    Returns True if thumbnail previews are enabled for the chat/user (default True).
    """
    cid = int(chat_or_user_id)
    if cid in _thumbnail_settings_cache:
        return _thumbnail_settings_cache[cid]
        
    try:
        if _db is not None:
            doc = _db.chat_settings.find_one({"chat_id": cid})
            if doc and "thumbnail" in doc:
                res = bool(doc["thumbnail"])
                _thumbnail_settings_cache[cid] = res
                return res
    except Exception as e:
        logger.warning(f"Error reading thumbnail setting: {e}")
        
    _thumbnail_settings_cache[cid] = True
    return True

def set_thumbnail_setting(chat_or_user_id: int, enabled: bool):
    """
    Enables or disables thumbnail previews for the specified chat/user.
    """
    cid = int(chat_or_user_id)
    _thumbnail_settings_cache[cid] = bool(enabled)
    try:
        if _db is not None:
            _db.chat_settings.update_one(
                {"chat_id": cid},
                {"$set": {"thumbnail": bool(enabled)}},
                upsert=True
            )
    except Exception as e:
        logger.error(f"Error saving thumbnail setting: {e}")

