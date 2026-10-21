"""
The anti spam protection must NEVER block a broadcast.

This test guards that promise: the protection only advises, the owner
decides.
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from villain_ads_bot.core import antispam
from villain_ads_bot.core import antispam_guard

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


now = time.time()

print("=" * 68)
print("THE PROTECTION MUST NEVER BLOCK")
print("=" * 68)

fresh = {"created_at": now, "stats": {}}

# a fresh account that already sent a lot
fresh["stats"]["sends_today"] = 50
fresh["stats"]["sends_this_hour"] = 50

allowed, reason = antispam.can_send(fresh)
check("the raw helper still reports the limit", allowed is False, reason)

# the guard (what the bot actually uses) must NOT block
advice = antispam_guard.advice(fresh)
check("the guard gives an advice", bool(advice), advice)
check("the advice mentions @SpamBot", "SpamBot" in advice, advice)

# the advice must be empty for an old account
old = {"created_at": now - 40 * 86400, "stats": {"sends_today": 500}}
check("an old account gets no advice", antispam_guard.advice(old) == "")

# the health text always works
health = antispam_guard.health_text(fresh)
check("the health text is produced", bool(health), health)
check("the health text mentions the age", "day" in health, health)

print()
print("=" * 68)
print("THE SWITCH")
print("=" * 68)

from villain_ads_bot.data import database
database.db_init()
settings = database.get_global_settings() or {}
old_value = settings.get("anti_spam_protection", True)
try:
    settings["anti_spam_protection"] = False
    database.save_global_settings(settings)
    check("the protection can be switched off", antispam_guard.is_enabled() is False)
    check("no advice when it is off", antispam_guard.advice(fresh) == "")
    check("the health text says so",
          "switched off" in antispam_guard.health_text(fresh).lower())
finally:
    settings = database.get_global_settings() or {}
    settings["anti_spam_protection"] = old_value
    database.save_global_settings(settings)

print()
print("=" * 68)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL ANTI-SPAM GUARD TESTS PASSED")
