"""
Tests for the MTProto proxy support, the channel mirror and the SAFE
rotation (the part that protects the accounts from a ban).
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from villain_ads_bot.data import database
from villain_ads_bot.core import proxy as proxy_tool
from villain_ads_bot.services import proxy_mirror
from villain_ads_bot.services import proxy_rotation

database.db_init()

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


# The real link the owner uses.
REAL_LINK = ("https://t.me/proxy?server=mtproxygram.lol&port=853"
             "&secret=ee608f40eb7d1aad1fbc6405c5ed2669616d742e6d7470726f78796772616d2e6f6e6c696e65")

print("=" * 68)
print("THE MTPROTO LINK (the real one from the owner)")
print("=" * 68)

p = proxy_tool.parse_proxy(REAL_LINK)
check("the real mtproto link is parsed", p is not None)
check("the type is mtproto", p and p["type"] == "mtproto", p)
check("the server is parsed", p and p["host"] == "mtproxygram.lol", p)
check("the port is parsed", p and p["port"] == 853, p)
check("the secret is parsed", p and p["secret"].startswith("ee608f40"), p)

tp = proxy_tool.build_telethon_proxy(p)
check("the telethon tuple has 3 parts", isinstance(tp, tuple) and len(tp) == 3, tp)
check("the telethon tuple has the host", tp and tp[0] == "mtproxygram.lol", tp)
check("the telethon tuple has the port", tp and tp[1] == 853, tp)
check("the telethon tuple has the secret", tp and tp[2].startswith("ee608f40"), tp)

conn = proxy_tool.get_connection_class(p)
check("an mtproxy uses the mtproxy connection",
      "MTProxy" in conn.__name__, conn.__name__)
socks = proxy_tool.get_connection_class({"type": "socks5", "host": "1.2.3.4", "port": 1080})
check("a socks proxy uses the obfuscated connection",
      "Obfuscated" in socks.__name__, socks.__name__)

check("the tg:// form is parsed",
      proxy_tool.parse_proxy("tg://proxy?server=a.lol&port=443&secret=" + "ab" * 20) is not None)
check("the bare host:port:secret form is parsed",
      proxy_tool.parse_proxy("a.lol:443:" + "ab" * 20) is not None)
check("a too short secret is refused",
      proxy_tool.parse_proxy("a.lol:443:abcd") is None)
check("a mtproto link is not treated as socks",
      (proxy_tool.parse_proxy(REAL_LINK) or {}).get("username") is None)

print()
print("=" * 68)
print("EXTRACTING THE PROXIES OF A MESSAGE")
print("=" * 68)

message = (
    "Here are the proxies:\n"
    "https://t.me/proxy?server=one.lol&port=853&secret=" + "11" * 32 + "\n"
    "https://t.me/proxy?server=two.lol&port=853&secret=" + "22" * 32 + "\n"
    "https://t.me/proxy?server=three.lol&port=853&secret=" + "33" * 32
)
found = proxy_mirror._extract_proxies(message)
check("three proxies are found", len(found) == 3, len(found))
check("the order is kept", [x["host"] for x in found] == ["one.lol", "two.lol", "three.lol"],
      [x["host"] for x in found])
check("duplicates are removed",
      len(proxy_mirror._extract_proxies(message + message.split("\n")[1])) == 3)

buttons = proxy_mirror.build_connect_buttons(555, 3)
check("three connect buttons are built", len(buttons[0]) == 3, buttons)
check("the callback data carries the message and the index",
      buttons[0][1].data.decode() == "proxy_connect_555_2",
      buttons[0][1].data)

single = proxy_mirror.build_connect_buttons(9, 1)
check("one button for one proxy", len(single) == 1 and len(single[0]) == 1)

print()
print("=" * 68)
print("THE SAFE ROTATION (the anti ban part)")
print("=" * 68)

check("the reconnect delay is set", proxy_rotation.RECONNECT_DELAY >= 5,
      proxy_rotation.RECONNECT_DELAY)

# an account that already has a proxy KEEPS it
test_sid = "ROT-TEST-" + os.urandom(3).hex()
database._db.sessions.delete_many({"session_id": test_sid})
database._db.sessions.insert_one({
    "session_id": test_sid, "user_id": 1, "phone": test_sid,
    "status": "stopped", "settings": {},
    "proxy": {"type": "mtproto", "host": "keep.lol", "port": 853, "secret": "aa" * 32},
})
assigned = proxy_rotation.assign_fresh_proxies([test_sid])
check("an account with a proxy keeps it", test_sid not in assigned, assigned)
check("the kept proxy is untouched",
      (database.get_session(test_sid) or {}).get("proxy", {}).get("host") == "keep.lol")

# an account without a proxy gets a free one
pool_before = proxy_tool.get_pool()
added_hosts = []
for i in range(2):
    proxy_tool.add_proxy(f"socks5://u:p@192.168.77.{i + 1}:1080")
    added_hosts.append(f"192.168.77.{i + 1}")
free = proxy_rotation.get_unused_proxies()
check("the free proxies are detected", len(free) >= 2, len(free))

test_sid2 = "ROT-TEST2-" + os.urandom(3).hex()
database._db.sessions.delete_many({"session_id": test_sid2})
database._db.sessions.insert_one({
    "session_id": test_sid2, "user_id": 1, "phone": test_sid2,
    "status": "stopped", "settings": {},
})
assigned2 = proxy_rotation.assign_fresh_proxies([test_sid2])
check("an account without a proxy gets one", test_sid2 in assigned2, assigned2)
assigned_host = (database.get_session(test_sid2) or {}).get("proxy", {}).get("host")
pool_hosts = [p.get("host") for p in proxy_tool.get_pool()]
check("the assigned proxy is persisted and comes from the pool",
      assigned_host in pool_hosts, f"{assigned_host} not in {pool_hosts}")

# the pool never silently changes a running account
check("the pool refresh only adds", len(proxy_tool.get_pool()) >= len(pool_before))

database._db.sessions.delete_many({"session_id": {"$in": [test_sid, test_sid2]}})
# clean the test proxies out of the pool again
for host in added_hosts:
    for idx, p in enumerate(list(proxy_tool.get_pool())):
        if p.get("host") == host:
            proxy_tool.remove_proxy(idx)
            break

print()
print("=" * 68)
print("THE STICKY BEHAVIOUR (the IP must not change on its own)")
print("=" * 68)

test_sid3 = "STICKY-" + os.urandom(3).hex()
database._db.sessions.delete_many({"session_id": test_sid3})
database._db.sessions.insert_one({
    "session_id": test_sid3, "user_id": 1, "phone": test_sid3,
    "status": "stopped", "settings": {},
    "proxy": {"type": "mtproto", "host": "sticky.lol", "port": 853, "secret": "bb" * 32},
})
resolved = proxy_tool.resolve_session_proxy(test_sid3)
check("a fixed proxy is returned as it is", resolved and resolved["host"] == "sticky.lol", resolved)
resolved2 = proxy_tool.resolve_session_proxy(test_sid3)
check("resolving it twice gives the same proxy",
      resolved2 and resolved2["host"] == "sticky.lol", resolved2)
database._db.sessions.delete_many({"session_id": test_sid3})

print()
print("=" * 68)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL MTPROXY + MIRROR + ROTATION TESTS PASSED")
