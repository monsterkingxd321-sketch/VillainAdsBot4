"""
Repeatable test suite for the five features that were added:

  #2  Coupon generator UI (+ the one tap redeem deep link)
  #3  Audit log
  #10 Admin roles
  #11 Backup / restore
  +   the automatic "multiple messages" mode
  +   the correct broadcast timing
"""
import os
import sys
import time
import zipfile
import io

sys.path.insert(0, os.path.abspath("."))

from villain_ads_bot.data import database
from villain_ads_bot.core import backup as backup_mod
from villain_ads_bot.core import utils

database.db_init()

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


# ══════════════════════════════════════════════════════════════════════════
print("=" * 66)
print("#10  ADMIN ROLES")
print("=" * 66)

check("role levels are ordered",
      database.ROLE_LEVELS["owner"] > database.ROLE_LEVELS["moderator"] > database.ROLE_LEVELS["support"])

database.set_admin_role(999001, "moderator")
check("role can be set", database.get_admin_role(999001) == "moderator")
database.set_admin_role(999001, "support")
check("role can be changed", database.get_admin_role(999001) == "support")
check("an unknown role is rejected", database.set_admin_role(999001, "wizard") is False)
database.remove_admin_role(999001)
check("a removed role falls back to support",
      database.get_admin_role(999001) == "support")

# the root owner can never be downgraded
import villain_ads_bot.config as _config
if _config.ORIGINAL_ADMIN_IDS:
    root = list(_config.ORIGINAL_ADMIN_IDS)[0]
    check("the root owner is always an owner", database.get_admin_role(root) == "owner")
else:
    print("  SKIP  root owner test (ORIGINAL_ADMIN_IDS is empty)")

# ══════════════════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("#3  AUDIT LOG")
print("=" * 66)

before = database.count_audit_log()
# start from a clean slate so the counts are exact
database._db.audit_log.delete_many({"action": "test.audit"})
before = database.count_audit_log()
database.log_audit(424242, "test.audit", "target-x", "some details", {"k": "v"})
entries = database.get_audit_log(limit=5)
check("an entry is written", database.count_audit_log() == before + 1)
check("the newest entry is first", entries and entries[0].get("action") == "test.audit")
check("the actor is stored", entries and entries[0].get("actor_id") == 424242)
check("the target is stored", entries and entries[0].get("target") == "target-x")
check("the details are stored", entries and entries[0].get("details") == "some details")
check("the meta is stored", entries and entries[0].get("meta", {}).get("k") == "v")

filtered = database.get_audit_log(limit=5, action="test.audit")
check("the log can be filtered by action", len(filtered) == 1)
filtered = database.get_audit_log(limit=5, actor_id=424242)
check("the log can be filtered by actor", len(filtered) == 1)
filtered = database.get_audit_log(limit=5, target="target-x")
check("the log can be filtered by target", len(filtered) == 1)
check("an unknown action returns nothing", len(database.get_audit_log(limit=5, action="nope.nope")) == 0)

# cleanup
database._db.audit_log.delete_many({"action": "test.audit"})

# ══════════════════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("#2  COUPONS")
print("=" * 66)

code = "TEST-" + os.urandom(4).hex().upper()
coupon = {
    "code": code,
    "amount_inr": 25.0,
    "max_uses": 2,
    "current_uses": 0,
    "is_active": True,
    "created_at": time.time(),
    "expires_at": time.time() + 3600,
    "max_per_user": 1,
}
database.save_coupon(coupon)
loaded = database.get_coupon(code)
check("a coupon can be stored and read", loaded is not None and loaded["code"] == code)
check("the amount is kept", loaded["amount_inr"] == 25.0)

loaded["current_uses"] = 2
loaded["is_active"] = False
database.save_coupon(loaded)
reloaded = database.get_coupon(code)
check("the usage counter is persisted", reloaded["current_uses"] == 2)
check("the active flag is persisted", reloaded["is_active"] is False)
check("the coupon shows up in the list", any(c["code"] == code for c in database.get_coupons()))

database.delete_coupon(code)
check("a coupon can be deleted", database.get_coupon(code) is None)

# ══════════════════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("#11  BACKUP / RESTORE")
print("=" * 66)

data = backup_mod.build_backup(include_session_files=True)
check("a backup is produced", len(data) > 1000, f"size={len(data)}")
check("the file name looks right", backup_mod.backup_filename().startswith("villain_backup_"))

preview = backup_mod.inspect_backup(data)
check("the backup can be inspected", preview.get("ok") is True)
check("the manifest is present", bool(preview.get("manifest")))
check("the manifest has a timestamp", "created_at" in (preview.get("manifest") or {}))
check("the counts are present", "users" in preview.get("counts", {}))
check("the settings are included", preview["counts"].get("has_settings") is True)

with zipfile.ZipFile(io.BytesIO(data)) as zf:
    names = zf.namelist()
    settings_raw = zf.read("global_settings.json").decode("utf-8")
check("global_settings.json is in the archive", "global_settings.json" in names)
check("users.json is in the archive", "users.json" in names)
check("sessions.json is in the archive", "sessions.json" in names)
check("coupons.json is in the archive", "coupons.json" in names)
check("the bot session key is NEVER exported", "bot_session_str" not in settings_raw)
check("the gpt key is NEVER exported", "gpt_api_key" not in settings_raw)

# a broken file must be rejected, not crash
broken = backup_mod.inspect_backup(b"not a zip at all")
check("a broken file is rejected safely", broken.get("ok") is False and broken.get("error"))
result = backup_mod.restore_backup(b"not a zip at all")
check("a broken restore fails safely", result.get("ok") is False and result.get("error"))

# restore of a real backup into the live db must not raise
restored = backup_mod.restore_backup(data, restore_users=False, restore_sessions=False,
                                     restore_settings=False, restore_coupons=False,
                                     write_session_files=False)
check("a valid backup restores without an error", restored.get("ok") is True, restored.get("error"))

# ══════════════════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("AUTOMATIC MESSAGE MODE  (the bug the owner reported)")
print("=" * 66)

s = {"broadcast_msg": "single default", "broadcast_messages": ["A", "B", "C"], "broadcast_mode": "single"}
check("broadcast uses the multiple list", utils.get_broadcast_messages(s) == ["A", "B", "C"])
check("the broadcast mode is corrected", utils.resolve_modes(s)["broadcast_mode"] == "multiple")

s = {"welcome_msg": "single default", "welcome_messages": ["W1", "W2"], "welcome_mode": "single"}
check("welcome uses the multiple list", utils.get_welcome_messages(s) == ["W1", "W2"])
check("the welcome mode is corrected", utils.resolve_modes(s)["welcome_mode"] == "multiple")

s = {"auto_reply_msg": "r", "auto_reply_messages": ["R1", "R2", "R3"], "auto_reply_mode": "single"}
check("auto reply uses the multiple list", utils.get_auto_reply_messages(s) == ["R1", "R2", "R3"])
check("the auto reply mode is corrected", utils.resolve_modes(s)["auto_reply_mode"] == "multiple")

s = {"broadcast_msg": "only one", "broadcast_messages": [], "broadcast_mode": "multiple"}
check("a single message stays single", utils.resolve_modes(s)["broadcast_mode"] == "single")
check("a single message is used", utils.get_broadcast_messages(s) == ["only one"])

s = {"broadcast_messages": ["X", "Y"], "broadcast_mode": "single"}
check("a list without a single message still works",
      utils.get_broadcast_messages(s) == ["X", "Y"])

s = {"broadcast_msg": "  ", "broadcast_messages": []}
check("empty messages are dropped", utils.get_broadcast_messages(s) == [])

# ══════════════════════════════════════════════════════════════════════════
print()
print("=" * 66)
print("BROADCAST TIMING  (the second reported bug)")
print("=" * 66)

t = utils.get_broadcast_timing({"inter_group_delay": 30, "broadcast_interval": 300}, 45)
check("the group delay is shown", t["group_delay_text"] == "30s", t)
check("the round duration is estimated", t["round_text"] == "24m", t)
check("the repeat interval is shown", t["repeat_text"] == "5m", t)
check("the REAL period = round + repeat", t["real_period_text"] == "29m", t)
check("the real period is bigger than the repeat alone",
      t["real_period"] > t["repeat_interval"])

t2 = utils.get_broadcast_timing({"inter_group_delay": 5, "broadcast_interval": 60}, 10)
check("the repeat interval has a 60s floor",
      utils.get_broadcast_interval({"broadcast_interval": 5}) == 60)
check("the group delay has a 2s floor",
      utils.get_inter_group_delay({"inter_group_delay": 1}) == 2.0)
check("a zero group delay falls back to the default",
      utils.get_inter_group_delay({"inter_group_delay": 0}) == 30.0)

check("durations are human readable", utils.format_duration(5400) == "1h 30m",
      utils.format_duration(5400))
check("short durations stay in seconds", utils.format_duration(45) == "45s")
check("an invalid duration does not crash", utils.format_duration("abc") == "0s")

# ══════════════════════════════════════════════════════════════════════════
print()
print("=" * 66)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL FEATURE TESTS PASSED")
