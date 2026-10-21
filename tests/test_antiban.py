"""
Tests for the anti-ban hardening and the proxy channel reader.

Anti-ban means:
  * every account has a UNIQUE device fingerprint (not 7 shared phones)
  * the LOGIN runs through a proxy (that is where Telegram counts the
    accounts per IP)
  * the IP of a running account never changes on its own
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from villain_ads_bot.data import database
from villain_ads_bot.core import utils
from villain_ads_bot.core import fingerprint
from villain_ads_bot.core import proxy as proxy_tool
from villain_ads_bot.services import proxy_channel

database.db_init()

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


print("=" * 68)
print("THE UNIQUE DEVICE FINGERPRINT (the botnet signature)")
print("=" * 68)

accounts = [f"+91{i:08d}" for i in range(1, 301)]
profiles = [utils.get_device_profile(a) for a in accounts]
models = {p["device_model"] for p in profiles}
apps = {p["app_version"] for p in profiles}
androids = {p["system_version"] for p in profiles}
langs = {p["lang_code"] for p in profiles}

check("300 accounts do not share one phone", len(models) >= 40, len(models))
check("the app versions differ too", len(apps) >= 8, len(apps))
check("the android versions differ too", len(androids) >= 8, len(androids))
check("the client language differs too", len(langs) >= 8, len(langs))

# The combination must be spread out. Real clients of the same phone model
# share the version strings, so the goal is not "zero collision" but
# "no fingerprint is shared by a crowd".
combos = [(p["device_model"], p["system_version"], p["app_version"], p["lang_code"])
           for p in profiles]
from collections import Counter
usage = Counter(combos)
worst = max(usage.values())
check("no fingerprint is shared by a crowd", worst <= 2,
      f"the most used combination is used {worst} times")
check("the fingerprints are spread over many combinations",
      len(usage) >= len(accounts) * 0.9, f"{len(usage)} of {len(accounts)}")
model_usage = Counter(p["device_model"] for p in profiles)
worst_model = max(model_usage.values())
check("no phone model is used by many accounts",
      worst_model <= len(accounts) * 0.05,
      f"the most used model is used {worst_model} times")

# and it must NEVER change for the same account
p1 = utils.get_device_profile("+919876543210")
p2 = utils.get_device_profile("+919876543210")
check("the fingerprint is stable for an account", p1 == p2)
p3 = utils.get_device_profile("+919876543211")
check("a different account gets a different fingerprint", p1 != p3)

check("the device list is realistic", len(fingerprint.DEVICES) >= 30,
      len(fingerprint.DEVICES))
check("the device model has a code", bool(p1.get("device_code")), p1)

print()
print("=" * 68)
print("THE PROXY CHANNEL READER")
print("=" * 68)

check("the channel can be set", proxy_tool.set_channel("@ProxyMTProto")[0] is True)
check("the channel is stored", proxy_tool.get_channel() == "ProxyMTProto",
      proxy_tool.get_channel())
ok, msg = proxy_tool.set_channel("https://t.me/ProxyMTProto?x=1")
check("a channel link is normalised",
      proxy_tool.get_channel() == "ProxyMTProto", proxy_tool.get_channel())

# the reader account
test_sid = "READER-TEST"
proxy_channel.set_reader_account(test_sid)
check("the reader account is stored", proxy_channel.get_reader_account() == test_sid)
proxy_channel.set_reader_account("")
check("the reader account can be cleared", proxy_channel.get_reader_account() == "")

# the extraction of a message
msg_text = (
    "drop 1: https://t.me/proxy?server=aaa.lol&port=853&secret=" + "11" * 32 + "\n"
    "drop 2: https://t.me/proxy?server=bbb.lol&port=853&secret=" + "22" * 32
)
found = proxy_channel._extract_proxies(msg_text)
check("both proxies are found", len(found) == 2, len(found))
check("the order is kept", [x["host"] for x in found] == ["aaa.lol", "bbb.lol"])
check("a message without a proxy gives nothing",
      proxy_channel._extract_proxies("just a chat message") == [])

print()
print("=" * 68)
print("THE PROXY SECTION IS ADMIN ONLY")
print("=" * 68)

import re as _re

admin_src = open("villain_ads_bot/handlers/admin.py", encoding="utf-8").read()
user_src = open("villain_ads_bot/handlers/my_bots.py", encoding="utf-8").read()

check("the admin panel has the proxy channel button",
      "admin_proxy_channel" in admin_src)
check("the admin panel has the reader account button",
      "admin_set_proxy_reader" in admin_src)
check("the admin panel has the join & read button",
      "admin_proxy_channel_read" in admin_src)
check("the user dashboard has NO proxy button",
      "set_proxy_" not in user_src)
check("the user dashboard has NO proxy text handler",
      "WAITING_FOR_PROXY" not in user_src)

print()
print("=" * 68)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL ANTI-BAN + CHANNEL TESTS PASSED")
