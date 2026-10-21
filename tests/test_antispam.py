"""
Tests for the @SpamBot protection.

The three pillars:
  1. the content must REALLY differ between the groups
  2. a new account must warm up
  3. an account must not exceed its budget
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from villain_ads_bot.core import antispam

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


print("=" * 68)
print("1. THE CONTENT MUST REALLY DIFFER")
print("=" * 68)

text = "Hello {friends|guys|everyone}, join our {channel|group} now!"

variants = {antispam.vary_message(text, group_id=-1001234567890) for _ in range(40)}
check("the same message produces many variants", len(variants) >= 5, len(variants))

# two different groups must never get the same text
a = antispam.vary_message(text, group_id=-1001111111111)
b = antispam.vary_message(text, group_id=-1002222222222)
check("two groups get different messages", a != b, f"{a!r} == {b!r}")

# the spintax must be expanded
check("the spintax is expanded", "{" not in antispam.vary_message("{a|b}", group_id=1))

# the per group tag is visible and stable
t1 = antispam.group_tag(-1001234567890)
t2 = antispam.group_tag(-1001234567890)
t3 = antispam.group_tag(-1009876543210)
check("the group tag is stable", t1 == t2, f"{t1} vs {t2}")
check("the groups have different tags", t1 != t3, f"{t1} vs {t3}")
check("the tag is short", len(t1) == 4, t1)

# the tag must be VISIBLE (the old zero width trick was invisible)
with_tag = antispam.vary_message("Hello", group_id=-1001234567890, tag=True)
without = antispam.vary_message("Hello", group_id=-1001234567890, tag=False)
check("the tag is visible in the message", len(with_tag) > len(without))
check("no zero width characters are used",
      "\u200b" not in with_tag and "\u200c" not in with_tag and "\u200d" not in with_tag,
      repr(with_tag))

# the nested spintax
nested = antispam.expand_spintax("{a|{b|c}}")
check("the nested spintax works", nested in ("a", "b", "c"), nested)

print()
print("=" * 68)
print("2. THE ACCOUNT MUST WARM UP")
print("=" * 68)

now = time.time()
fresh = {"created_at": now}
old = {"created_at": now - 30 * 86400}

check("a brand new account has a tiny budget",
      antispam.daily_budget(fresh) <= 5, antispam.daily_budget(fresh))
check("a new account is marked as warming up",
      antispam.budget_state(fresh)["warming_up"] is True)
check("the budget grows every day",
      antispam.daily_budget({"created_at": now - 3 * 86400}) >
      antispam.daily_budget(fresh),
      antispam.daily_budget({"created_at": now - 3 * 86400}))
check("an old account has a big budget",
      antispam.daily_budget(old) >= 100, antispam.daily_budget(old))
check("an old account is not warming up",
      antispam.budget_state(old)["warming_up"] is False)
check("an account without a date is treated as old",
      antispam.daily_budget({}) >= 100)

print()
print("=" * 68)
print("3. THE BUDGET MUST BE RESPECTED")
print("=" * 68)

session = {"created_at": now, "stats": {}}
allowed, reason = antispam.can_send(session)
check("a fresh account may send at first", allowed is True, reason)

stats = antispam.record_send(session)
session["stats"].update(stats)
check("the send is counted", session["stats"]["sends_today"] == 1,
      session["stats"].get("sends_today"))

# fill the budget up
session["stats"]["sends_today"] = antispam.daily_budget(session)
allowed, reason = antispam.can_send(session)
check("the daily budget stops the broadcast", allowed is False, reason)
check("the reason mentions the budget", "budget" in reason.lower(), reason)

session["stats"]["sends_this_hour"] = antispam.hourly_budget(session)
allowed2, reason2 = antispam.can_send(session)
check("the hourly budget stops it as well", allowed2 is False, reason2)

# the counters reset with a new day / hour
session["stats"]["send_day"] = int(time.time() // 86400) - 1
session["stats"]["send_hour"] = int(time.time() // 3600) - 1
stats2 = antispam.record_send(session)
check("the daily counter resets", stats2["sends_today"] == 1, stats2)
check("the hourly counter resets", stats2["sends_this_hour"] == 1, stats2)

print()
print("=" * 68)
print("4. READING THE @Spambot ANSWER")
print("=" * 68)

r1 = antispam.parse_spam_status("You're not limited right now. Enjoy!")
check("no restriction is detected", r1["limited"] is False, r1)

r2 = antispam.parse_spam_status("Your account was limited until 12 March 2026.")
check("a temporary limit is detected", r2["limited"] is True, r2)
check("the severity is temporary", r2["severity"] == "temporary", r2)
check("the until date is found", "12 march" in str(r2["until"]), r2)

r3 = antispam.parse_spam_status("Your account has been permanently limited.")
check("a permanent limit is detected", r3["limited"] is True, r3)
check("the severity is permanent", r3["severity"] == "permanent", r3)

print()
print("=" * 68)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL ANTI-SPAM TESTS PASSED")
