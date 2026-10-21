"""
Tests for the proxy system and the unlimited slot support.

The proxy is what removes Telegram's "too many accounts from one IP" limit,
so it is the most important part for running many accounts.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from villain_ads_bot.data import database
from villain_ads_bot.core import proxy as proxy_tool
from villain_ads_bot.core import utils
from villain_ads_bot.core import pricing

database.db_init()

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


print("=" * 68)
print("THE PROXY PARSER")
print("=" * 68)

p = proxy_tool.parse_proxy("socks5://user:pass@1.2.3.4:1080")
check("a full proxy is parsed", p is not None)
check("the type is socks5", p and p["type"] == "socks5")
check("the host is parsed", p and p["host"] == "1.2.3.4", p)
check("the port is parsed", p and p["port"] == 1080, p)
check("the user is parsed", p and p["username"] == "user", p)
check("the password is parsed", p and p["password"] == "pass", p)

p2 = proxy_tool.parse_proxy("1.2.3.4:9050")
check("host:port defaults to socks5", p2 and p2["type"] == "socks5" and p2["port"] == 9050, p2)

p3 = proxy_tool.parse_proxy("http://1.2.3.4:8080")
check("an http proxy is parsed", p3 and p3["type"] == "http", p3)

p4 = proxy_tool.parse_proxy("socks5://[2001:db8::1]:1080")
check("an ipv6 proxy is parsed", p4 and p4["host"] == "2001:db8::1", p4)

check("an invalid proxy returns None", proxy_tool.parse_proxy("not a proxy") is None)
check("an empty proxy returns None", proxy_tool.parse_proxy("") is None)
check("a missing port returns None", proxy_tool.parse_proxy("1.2.3.4") is None)
check("a bad port returns None", proxy_tool.parse_proxy("1.2.3.4:abc") is None)

print()
print("=" * 68)
print("THE TELTHON PROXY ARGUMENT")
print("=" * 68)

tp = proxy_tool.build_telethon_proxy(p)
check("the telethon tuple is built", isinstance(tp, tuple) and len(tp) == 6, tp)
check("the host is right", tp and tp[1] == "1.2.3.4", tp)
check("the port is right", tp and tp[2] == 1080, tp)
check("the credentials are right", tp and tp[4] == "user" and tp[5] == "pass", tp)
check("no proxy gives None", proxy_tool.build_telethon_proxy(None) is None)

tp_http = proxy_tool.build_telethon_proxy(p3)
check("an http proxy becomes socks5 for telethon",
      tp_http and tp_http[0] == "socks5", tp_http)

print()
print("=" * 68)
print("THE PROXY POOL")
print("=" * 68)

pool = proxy_tool.get_pool()
before = len(pool)
ok, msg = proxy_tool.add_proxy("socks5://pooltest:pooltest@10.9.8.7:1080")
check("a proxy can be added", ok is True, msg)
pool = proxy_tool.get_pool()
check("the pool grew", len(pool) == before + 1)
check("the added proxy is in the pool",
      any(x["host"] == "10.9.8.7" for x in pool))

ok2, msg2 = proxy_tool.add_proxy("socks5://pooltest:pooltest@10.9.8.7:1080")
check("the same proxy is not added twice", ok2 is False, msg2)

ok3, msg3 = proxy_tool.remove_proxy(len(pool) - 1)
check("a proxy can be removed", ok3 is True, msg3)
check("the pool is back to its size", len(proxy_tool.get_pool()) == before)

# the round robin gives different proxies to different accounts
pool2 = []
for i in range(3):
    proxy_tool.add_proxy(f"socks5://u:p@10.0.0.{i + 1}:1080")
assigned = {}
for sid in ("+111", "+222", "+333", "+444"):
    a = proxy_tool.assign_round_robin(sid)
    if a:
        assigned[sid] = a["host"]
check("the round robin assigns proxies", len(assigned) > 0, assigned)
check("the round robin uses different IPs",
      len(set(assigned.values())) > 1, assigned)
for i in range(3):
    proxy_tool.remove_proxy(0)

print()
print("=" * 68)
print("THE PER ACCOUNT PROXY")
print("=" * 68)

test_sid = "PROXY-TEST-" + os.urandom(3).hex()
database._db.sessions.delete_many({"session_id": test_sid})
database._db.sessions.insert_one({
    "session_id": test_sid, "user_id": 1, "phone": test_sid,
    "status": "stopped", "settings": {},
})
check("a new account has no proxy",
      proxy_tool.get_session_proxy(test_sid) is None)

ok, msg = proxy_tool.set_session_proxy(test_sid, "socks5://uu:pp@8.8.4.4:1080")
check("a proxy can be set for an account", ok is True, msg)
saved = proxy_tool.get_session_proxy(test_sid)
check("the proxy is stored", saved and saved["host"] == "8.8.4.4", saved)

ok, msg = proxy_tool.set_session_proxy(test_sid, "garbage")
check("an invalid proxy is refused", ok is False, msg)
check("the old proxy is kept after a refusal",
      proxy_tool.get_session_proxy(test_sid)["host"] == "8.8.4.4")

ok, msg = proxy_tool.set_session_proxy(test_sid, "")
check("the proxy can be removed", ok is True, msg)
check("the proxy is really gone", proxy_tool.get_session_proxy(test_sid) is None)

database._db.sessions.delete_many({"session_id": test_sid})

print()
print("=" * 68)
print("THE UNLIMITED SLOT SUPPORT")
print("=" * 68)

check("the unlimited constant exists", utils.UNLIMITED_SLOTS > 0)
check("a normal limit is formatted as a number",
      utils.format_slots(5) == "5", utils.format_slots(5))
check("the unlimited limit is formatted as infinity",
      utils.format_slots(utils.UNLIMITED_SLOTS) == "∞",
      utils.format_slots(utils.UNLIMITED_SLOTS))

# a global switch removes the limit for everybody
global_settings = database.get_global_settings()
old_unlimited = global_settings.get("unlimited_slots", False)
old_default = global_settings.get("default_slot_limit", 1)
try:
    global_settings["unlimited_slots"] = True
    database.save_global_settings(global_settings)
    check("the global switch removes the limit for everybody",
          utils.get_allowed_slots(1) == utils.UNLIMITED_SLOTS,
          utils.get_allowed_slots(1))
    check("is_unlimited reports it", utils.is_unlimited(1) is True)

    global_settings["unlimited_slots"] = False
    database.save_global_settings(global_settings)
    check("without the switch the limit applies again",
          utils.get_allowed_slots(1) < utils.UNLIMITED_SLOTS)
finally:
    global_settings = database.get_global_settings()
    global_settings["unlimited_slots"] = old_unlimited
    global_settings["default_slot_limit"] = old_default
    database.save_global_settings(global_settings)

# an unlimited subscription lifts the limit for one user
test_uid = 999444
database._db.users.delete_many({"user_id": test_uid})
database._db.users.insert_one({
    "user_id": test_uid, "language": "en", "tos_accepted": True,
    "allowed_slots": 1, "wallet_balance": 0.0,
    "subscriptions": [{
        "subscription_id": "unlim", "qty": 0, "unlimited": True,
        "expires_at": 9999999999, "plan_name": "Unlimited",
    }],
})
database._user_cache.pop(test_uid, None)
check("an unlimited subscription removes the limit",
      utils.get_allowed_slots(test_uid) == utils.UNLIMITED_SLOTS,
      utils.get_allowed_slots(test_uid))
check("is_unlimited detects it", utils.is_unlimited(test_uid) is True)

# a per user override
database._db.users.update_one({"user_id": test_uid}, {"$set": {"slot_limit_override": 0}})
database._user_cache.pop(test_uid, None)
check("a per user override removes the limit",
      utils.get_allowed_slots(test_uid) == utils.UNLIMITED_SLOTS,
      utils.get_allowed_slots(test_uid))
database._db.users.delete_many({"user_id": test_uid})

# the plan helpers
check("a plan can be unlimited",
      pricing.plan_is_unlimited({"unlimited": True}) is True)
check("a normal plan is not unlimited",
      pricing.plan_is_unlimited({"unlimited": False, "slots": 5}) is False)
check("a plan can define its own price per slot",
      pricing.plan_price_per_slot({"price_per_slot": 42}) == 42)
check("a plan without a price uses the global one",
      pricing.plan_price_per_slot({}) == pricing.get_price_per_slot())
b = pricing.breakdown(2, 50)
check("the breakdown uses the plan price", b["inr"] == 100.0, b)

print()
print("=" * 68)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL PROXY + UNLIMITED TESTS PASSED")
