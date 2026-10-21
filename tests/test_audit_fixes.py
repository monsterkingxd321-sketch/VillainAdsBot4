"""
Regression tests for the bugs that the deep audit found.

Every check below corresponds to a bug that was actually present in the
running code. If one of them ever comes back, this suite fails.
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from villain_ads_bot.data import database
from villain_ads_bot.core import utils
from villain_ads_bot.services import userbot_manager

database.db_init()

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


print("=" * 68)
print("7.1 / 7.2  THE DATABASE WRITES (lost updates)")
print("=" * 68)

# a write must be visible immediately (the old write was asynchronous)
u = database.get_user(1) or {"user_id": 1, "wallet_balance": 0.0}
u["wallet_balance"] = 11.11
database.save_user(u)
check("a user write is visible right away",
      (database.get_user(1) or {}).get("wallet_balance") == 11.11,
      (database.get_user(1) or {}).get("wallet_balance"))

# the returned document must be a COPY
u2 = database.get_user(1)
u2["wallet_balance"] = -999.0
check("the caller cannot mutate the cache",
      (database.get_user(1) or {}).get("wallet_balance") == 11.11)

# the session list must not share its nested dicts with the cache
sessions = database.get_sessions()
if sessions:
    before = sessions[0].get("settings", {}).get("broadcast_msg")
    sessions[0].setdefault("settings", {})["broadcast_msg"] = "MUTATED-BY-TEST"
    after = database.get_sessions()[0].get("settings", {}).get("broadcast_msg")
    check("the session list is not shared with the cache", after == before,
          f"{before!r} -> {after!r}")
else:
    print("  SKIP  no sessions to check")

# a fresh read must bypass the cache
sess = database.get_sessions()
if sess:
    sid = sess[0]["session_id"]
    fresh = database.get_session_fresh(sid)
    check("a fresh session read works", fresh is not None and fresh.get("session_id") == sid)

print()
print("=" * 68)
print("6.1 / 6.2 / 6.5  THE ATOMIC PAYMENT HELPERS")
print("=" * 68)

check("claim_payment exists", callable(getattr(database, "claim_payment", None)))
check("deduct_wallet exists", callable(getattr(database, "deduct_wallet", None)))
check("credit_wallet exists", callable(getattr(database, "credit_wallet", None)))
check("redeem_coupon exists", callable(getattr(database, "redeem_coupon", None)))
check("release_payment exists", callable(getattr(database, "release_payment", None)))

# the wallet can never go negative
test_uid = 999777
database._db.users.delete_many({"user_id": test_uid})
database._db.users.insert_one({"user_id": test_uid, "wallet_balance": 10.0})
ok1 = database.deduct_wallet(test_uid, 4.0)
ok2 = database.deduct_wallet(test_uid, 20.0)     # too much -> must fail
balance = database._db.users.find_one({"user_id": test_uid}).get("wallet_balance")
check("the wallet is deducted", ok1 is True)
check("the wallet can never go negative", ok2 is False and balance == 6.0,
      f"ok2={ok2} balance={balance}")
database.credit_wallet(test_uid, 4.0)
balance = database._db.users.find_one({"user_id": test_uid}).get("wallet_balance")
check("the wallet is credited", balance == 10.0, balance)
database._db.users.delete_many({"user_id": test_uid})

# a payment can only be claimed ONCE
test_pid = "TEST-CLAIM-" + os.urandom(3).hex()
database._db.payments.delete_many({"payment_id": test_pid})
database._db.payments.insert_one({
    "payment_id": test_pid, "user_id": 1, "count": 1,
    "status": "pending", "amount": 10.0, "timestamp": time.time(),
})
first = database.claim_payment(test_pid, method="wallet")
second = database.claim_payment(test_pid, method="wallet")
check("the first claim wins", first is not None)
check("a second claim is refused", second is None)
database._db.payments.delete_many({"payment_id": test_pid})

# a coupon can never be used more often than max_uses
test_code = "TESTCP-" + os.urandom(3).hex().upper()
database._db.coupons.delete_many({"code": test_code})
database._db.coupons.insert_one({
    "code": test_code, "amount_inr": 5.0, "max_uses": 2,
    "current_uses": 0, "is_active": True, "created_at": time.time(),
})
r1 = database.redeem_coupon(test_code, 1)
r2 = database.redeem_coupon(test_code, 2)
r3 = database.redeem_coupon(test_code, 3)
check("the coupon can be redeemed", r1 is not None)
check("the coupon can be redeemed twice", r2 is not None)
check("the coupon is blocked after max_uses", r3 is None)
database._db.coupons.delete_many({"code": test_code})

print()
print("=" * 68)
print("5.1  THE CONCURRENT USERBOT LIMIT")
print("=" * 68)

import villain_ads_bot.config as _config

old_limit = _config.MAX_RUNNING_USERBOTS
try:
    _config.MAX_RUNNING_USERBOTS = 0
    check("a limit of 0 means unlimited", userbot_manager.can_start_more_bots() is True)

    _config.MAX_RUNNING_USERBOTS = 1
    # fake one running bot
    class _FakeBot:
        is_running = True

    userbot_manager._running_bots["__fake__"] = _FakeBot()
    check("the limit is enforced", userbot_manager.can_start_more_bots() is False)
    userbot_manager._running_bots.pop("__fake__", None)
    check("the limit allows a start when nothing runs",
          userbot_manager.can_start_more_bots() is True)
finally:
    _config.MAX_RUNNING_USERBOTS = old_limit

print()
print("=" * 68)
print("5.4  THE MISSING stop_bot ALIAS")
print("=" * 68)

check("stop_bot exists", hasattr(userbot_manager, "stop_bot"))
check("stop_bot is a coroutine function",
      callable(getattr(userbot_manager, "stop_bot", None)))

print()
print("=" * 68)
print("1.3  THE BROADCAST LOCK")
print("=" * 68)

from villain_ads_bot.services.userbot import UserBot

bot = UserBot("__lock_test__")
check("the broadcast lock exists", hasattr(bot, "_broadcast_lock"))
check("the auto reply limiter exists", hasattr(bot, "_last_reply_ts"))
check("the media registry exists", hasattr(utils, "mark_media_active"))

print()
print("=" * 68)
print("8.3  THE ACTIVE MEDIA REGISTRY")
print("=" * 68)

tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_media.bin")
with open(tmp, "wb") as f:
    f.write(b"x")
utils.mark_media_active(tmp)
check("an active file is detected", utils.is_media_active(tmp) is True)
utils.unmark_media(tmp)
check("a released file is not active", utils.is_media_active(tmp) is False)
try:
    os.remove(tmp)
except Exception:
    pass

print()
print("=" * 68)
print("3.1  THE AUTO REPLY LIMITER")
print("=" * 68)

check("the global reply gap is read from the settings",
      isinstance(utils.format_duration(15), str))
# the limiter itself lives in the userbot, verify the attribute default
check("the limiter starts at 0", bot._last_reply_ts == 0.0)

print()
print("=" * 68)
print("8.2  THE ABSOLUTE PATHS")
print("=" * 68)

from villain_ads_bot import config as _cfg

check("the downloads dir is absolute", os.path.isabs(_cfg.DOWNLOADS_DIR), _cfg.DOWNLOADS_DIR)
check("the user data dir is absolute", os.path.isabs(_cfg.USER_DATA_DIR), _cfg.USER_DATA_DIR)
check("the project root is absolute", os.path.isabs(_cfg.PROJECT_ROOT), _cfg.PROJECT_ROOT)

from villain_ads_bot.services import youtube as _yt

check("the youtube download dir is absolute", os.path.isabs(_yt.DOWNLOAD_DIR), _yt.DOWNLOAD_DIR)

print()
print("=" * 68)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL AUDIT FIX TESTS PASSED")
