"""
Tests for the agreement (TOS) screen with the decline button.
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from villain_ads_bot.data import database
from villain_ads_bot.core import utils
from villain_ads_bot.translations import TRANSLATIONS

database.db_init()

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


print("=" * 68)
print("THE AGREEMENT SCREEN (accept + decline)")
print("=" * 68)

LANGS = ("en", "hi", "ru", "ja", "fr", "de", "zh", "ar")

for key in ("tos_accept_btn", "tos_decline_btn", "tos_declined_msg", "tos_declined_alert"):
    missing = [lang for lang in LANGS if key not in TRANSLATIONS.get(lang, {})]
    check(f"'{key}' exists in all 8 languages", not missing, missing)

check("the decline button is a decline",
      "decline" in TRANSLATIONS["en"]["tos_decline_btn"].lower()
      or "refus" in TRANSLATIONS["fr"]["tos_decline_btn"].lower()
      or "reject" in TRANSLATIONS["zh"]["tos_decline_btn"].lower())

# the decline flow stores the state
test_uid = 999555
database._db.users.delete_many({"user_id": test_uid})
database._db.users.insert_one({
    "user_id": test_uid, "language": "en", "tos_accepted": False,
    "tos_declined": True, "tos_declined_at": time.time(),
})
user = database.get_user(test_uid)
check("a declined user is stored as declined", user.get("tos_declined") is True)
check("a declined user has not accepted", not user.get("tos_accepted"))

# the guard must block a declined user
class _FakeEvent:
    is_private = True
    sender_id = 999555
    def __init__(self):
        self.answered = []
        self.responded = []
        self.edited = []
    async def answer(self, text, **kw):
        self.answered.append(text)
    async def respond(self, text, **kw):
        self.responded.append(text)
    async def edit(self, text, **kw):
        self.edited.append(text)


import asyncio


async def _check_guard():
    ev = _FakeEvent()
    blocked = await utils.guard(ev, None)
    return blocked, ev


blocked, ev = asyncio.get_event_loop().run_until_complete(_check_guard())
check("a declined user is blocked by the guard", blocked is True)
check("the blocked user gets a message",
      bool(ev.responded or ev.edited), f"{ev.responded} {ev.edited}")

# an accepted user is not blocked
accepted = database.get_user(test_uid)
accepted["tos_accepted"] = True
accepted["tos_declined"] = False
database.save_user(accepted)
blocked2, ev2 = asyncio.get_event_loop().run_until_complete(_check_guard())
check("an accepted user is not blocked by the TOS guard", blocked2 is False)

database._db.users.delete_many({"user_id": test_uid})

print()
print("=" * 68)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL AGREEMENT TESTS PASSED")
