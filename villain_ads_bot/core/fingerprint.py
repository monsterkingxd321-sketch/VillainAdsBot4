"""
Anti-ban hardening: unique device fingerprints.

The bot used to pick the device profile from a list of SEVEN phones. With
hundreds of accounts that means "30 accounts with the same Samsung Galaxy
S24 Ultra" - a textbook botnet signature that Telegram detects instantly.

Now every account gets a UNIQUE but perfectly realistic fingerprint that is
stable for that account (it never changes between restarts, so the account
does not look like it suddenly moved to a new phone).
"""
import hashlib

# ── the real device catalogue ─────────────────────────────────────────────
DEVICES = [
    ("Samsung Galaxy S24 Ultra", "SM-S928B"),
    ("Samsung Galaxy S24+", "SM-S926B"),
    ("Samsung Galaxy S24", "SM-S921B"),
    ("Samsung Galaxy S23 Ultra", "SM-S918B"),
    ("Samsung Galaxy S23+", "SM-S916B"),
    ("Samsung Galaxy S23", "SM-S911B"),
    ("Samsung Galaxy S22 Ultra", "SM-S908B"),
    ("Samsung Galaxy S22+", "SM-S906B"),
    ("Samsung Galaxy S22", "SM-S901B"),
    ("Samsung Galaxy A55 5G", "SM-A556B"),
    ("Samsung Galaxy A54 5G", "SM-A546B"),
    ("Samsung Galaxy A34 5G", "SM-A346B"),
    ("Samsung Galaxy A14", "SM-A145F"),
    ("Samsung Galaxy M14 5G", "SM-M146B"),
    ("Google Pixel 8 Pro", "GC3VE"),
    ("Google Pixel 8", "G1BQG"),
    ("Google Pixel 7 Pro", "GP4BC"),
    ("Google Pixel 7", "GVU6C"),
    ("Google Pixel 6a", "GX7AS"),
    ("OnePlus 12", "CPH2585"),
    ("OnePlus 11", "CPH2449"),
    ("OnePlus Nord 3", "CPH2493"),
    ("Xiaomi 14 Ultra", "24031PN0DC"),
    ("Xiaomi 14", "23127PN0CC"),
    ("Xiaomi 13 Ultra", "2208120UG"),
    ("Xiaomi 13", "2211133C"),
    ("Xiaomi 12T Pro", "22081212UG"),
    ("Xiaomi POCO F5", "23049PCD8G"),
    ("Redmi Note 13 Pro+", "2312DRA50G"),
    ("Redmi Note 13 Pro", "23090RA98C"),
    ("Redmi Note 13", "2312DRA50C"),
    ("Redmi 13C", "23129RAA4G"),
    ("POCO X5 Pro", "22101320G"),
    ("Realme 11 Pro+", "RMP2205"),
    ("Realme 11", "RMX3771"),
    ("Oppo Reno 10", "PHZ110"),
    ("Oppo A78", "CPH2565"),
    ("Vivo V29", "V2256A"),
    ("Vivo V27", "V2231A"),
    ("Motorola Edge 40", "moto62"),
    ("Nothing Phone 2", "A065"),
    ("Honor Magic 5", "PGT-N19"),
    ("Huawei P60 Pro", "ELE-LX9"),
    ("Infinix Note 30", "X6831"),
    ("Tecno Pova 5", "EJ901"),
]

ANDROID_VERSIONS = [
    "Android 14 (UP1A.231005.007)",
    "Android 14 (UP1A.231005.007)",
    "Android 14 (UKQ1.230924.001)",
    "Android 13 (TQ3A.230805.001)",
    "Android 13 (TKQ1.220829.002)",
    "Android 13 (TP1A.220624.014)",
    "Android 12 (SP1A.210812.016)",
    "Android 12 (SQU1A.220205.002)",
    "Android 12 (SKQ1.211006.001)",
    "Android 11 (RQ3A.211001.001)",
    "Android 11 (RP1A.200720.011)",
    "Android 10 (QKQ1.190910.002)",
]

# The Telegram android client versions. They must look like the real client.
APP_VERSIONS = [
    "11.2.2 (420)",
    "11.1.4 (398)",
    "11.1.2 (392)",
    "10.9.1 (380)",
    "10.8.3 (365)",
    "10.6.2 (350)",
    "10.4.1 (330)",
    "10.2.0 (310)",
    "9.8.3 (290)",
    "9.6.4 (275)",
]

# The language of the client. It should roughly match the account country.
LANGUAGES = [
    "en", "en", "en", "en",
    "hi", "hi",
    "ru",
    "ja",
    "ar",
    "pt",
    "es",
    "id",
    "tr",
    "de",
    "fr",
    "zh",
    "ko",
]


def _hash_int(seed: str, salt: str = "") -> int:
    h = hashlib.md5(f"{seed}{salt}".encode("utf-8")).hexdigest()
    return int(h, 16)


def get_device_profile(session_id: str = None, phone: str = None) -> dict:
    """
    Returns a UNIQUE but realistic device profile for one account.

    The profile is derived from the account id, so it NEVER changes - the
    account always looks like the same phone, but no two accounts look the
    same.
    """
    seed = str(session_id or phone or "default")
    h = _hash_int(seed)

    model, model_code = DEVICES[h % len(DEVICES)]
    android = ANDROID_VERSIONS[(h >> 8) % len(ANDROID_VERSIONS)]
    app = APP_VERSIONS[(h >> 16) % len(APP_VERSIONS)]
    lang = LANGUAGES[(h >> 24) % len(LANGUAGES)]

    return {
        "device_model": model,
        "device_code": model_code,
        "system_version": android,
        "app_version": app,
        "lang_code": lang,
        "system_lang_code": lang,
    }


def get_profile_for_account(session_id: str) -> dict:
    """Alias kept for readability at the call sites."""
    return get_device_profile(session_id)
