"""
The premium UI kit must never break a screen.

The admin panels used to call card_open() with FOUR arguments while the
function only accepted three - every one of those screens crashed with a
TypeError. This test locks the signature down.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from villain_ads_bot.core import ui

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  OK    {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILED.append(name)


print("=" * 68)
print("THE CARD KIT")
print("=" * 68)

# the four argument call that used to crash
out = ui.card_open("Audit Log", "Every admin action is recorded",
                   "12 entries in total", chr(128220))
check("card_open accepts 4 arguments", True)
check("it opens a blockquote", out.startswith("<blockquote>"), out[:40])
check("it carries the title", "Audit Log" in out, out)

closed = ui.card_close()
check("card_close closes the blockquote", closed.rstrip().endswith("</blockquote>"), closed)

full = out + "\n\nbody here\n\n" + closed
ok, msg = ui.validate_html(full)
check("the whole card is valid HTML", ok, msg)

# the three argument call still works
out3 = ui.card_open("Proxy Manager", "One proxy = one IP address", chr(127760))
ok3, msg3 = ui.validate_html(out3 + closed)
check("the 3 argument call still works", ok3, msg3)

# a card that is built with card() is valid too
c = ui.card("Admin Control Panel", "Owner tools", "slot price", chr(127380))
ok4, msg4 = ui.validate_html(c)
check("card() is valid HTML", ok4, msg4)

print()
print("=" * 68)
print("THE @SpamBot CHECK (no GetBotRequest any more)")
print("=" * 68)

import inspect

from villain_ads_bot.core import antispam

source = inspect.getsource(antispam.check_spam_status)
# the broken call was: functions.messages.GetBotRequest(bot_username=...)
check("the broken GetBotRequest call is gone",
      "GetBotRequest(" not in source.replace("GetBotRequest\"", ""),
      source)

# the parsing itself
res = antispam.parse_spam_status(
    "No restrictions are placed on your account. "
    "You can send messages to as many users as you like.")
check("a clean account is not limited", res["limited"] is False, res)

res = antispam.parse_spam_status(
    "You're limited until 21 Oct 2026 10:00 - spam flood")
check("a limited account is detected", res["limited"] is True, res)
check("the severity is temporary", res["severity"] == "temporary", res)
check("the until date is found", bool(res["until"]), res)

res = antispam.parse_spam_status("Your account is permanently limited.")
check("a permanent limit is detected", res["severity"] == "permanent", res)

print()
print("=" * 68)
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED:")
    for f in FAILED:
        print(f"  - {f}")
    raise SystemExit(1)
print("ALL UI / SPAM STATUS TESTS PASSED")
