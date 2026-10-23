"""
Anti-spam protection against @SpamBot.

@SpamBot does NOT care about your IP. It looks at the ACCOUNT and at the
CONTENT:

  * the same message in 40 groups  -> the "SPAM" watermark + user reports
  * a brand new account that suddenly messages 40 strangers -> limited
  * too many messages per hour / per day -> limited
  * a reported account -> limited for days / months / permanently

So the protection is threefold:

1. THE CONTENT MUST REALLY DIFFER
   The old code only appended 3-8 zero width characters to the end of the
   message. Telegram detects that trick exactly (it is why the watermark
   appears in the first place) and it also destroys the user's own text.
   Now the message is really varied: the spintax is expanded, the emoji
   and the separator rotate and - optionally - every group gets its own
   short visible tag, so no two groups ever receive the same text.

2. THE ACCOUNT MUST WARM UP
   A fresh account that broadcasts to 40 groups on day one is the classic
   spam pattern. The send budget of a new account therefore starts tiny
   and grows every day.

3. THE ACCOUNT MUST NOT OVERDO IT
   There is a per account budget per hour and per day.

The @SpamBot status of an account can be checked directly: sending
``/start`` to @SpamBot returns the current restriction of that account.
"""

import hashlib
import logging
import random
import re
import time
logger = logging.getLogger(__name__)

SPAM_BOT = "SpamBot"

# ── the send budget per account age (in days) ────────────────────────────
# The budget is the maximum number of messages per DAY.
WARMUP_BUDGET = {
    0: 3,      # day 1
    1: 3,      # day 2
    2: 5,
    3: 8,
    4: 12,
    5: 18,
    6: 25,
    7: 35,
}
FREE_BUDGET = 1000          # from day 8 on
DEFAULT_HOURLY_BUDGET = 60   # max messages per hour and per account

# The emoji that is rotated at the end of a message.
TRAILING_EMOJI = ["", "🚀", "⚡", "🔥", "💎", "✨", "🎯", "💫", "🌟", "🏆"]
SEPARATORS = ["|", "•", "—", "::", "·", "/"]


# ── the content variation ─────────────────────────────────────────────────
def expand_spintax(text: str) -> str:
    """
    Expands {a|b|c} into a random option. Supports nesting.
    """
    if not text or "{" not in text:
        return text or ""

    result = []
    i = 0
    depth = 0
    start = -1
    while i < len(text):
        ch = text[i]
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                inner = text[start + 1:i]
                options = _split_options(inner)
                chosen = random.choice(options) if options else ""
                # The chosen option can contain another spintax -> expand it.
                result.append(expand_spintax(chosen) if "{" in chosen else chosen)
                start = -1
                i += 1
                continue
        elif depth == 0:
            result.append(ch)
        i += 1
    if depth > 0 and start != -1:
        # unbalanced - keep the rest as it is
        result.append(text[start:])
    return "".join(result)


def _split_options(inner: str) -> list:
    """Splits 'a|b|c' on the top level pipes."""
    parts = []
    depth = 0
    current = []
    for ch in inner:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p] or [inner.strip()]


def group_tag(group_id) -> str:
    """
    A short, stable and VISIBLE tag for a group.

    Every group gets a different tag, so no two groups ever receive the
    exact same message. The owner can also use it to see which group a
    message came from.
    """
    if group_id is None:
        return ""
    h = hashlib.md5(str(group_id).encode("utf-8")).hexdigest()
    return h[:4]


def vary_message(text: str, group_id=None, tag: bool = False, emoji: bool = False) -> str:
    """
    Returns the message cleanly.
    Expands spintax {option1|option2} if present.
    Only appends emoji or group tags if explicitly enabled (defaults to False).
    """
    if not text:
        return text

    out = expand_spintax(text)

    # rotate the trailing emoji only if explicitly enabled
    if emoji:
        chosen = random.choice(TRAILING_EMOJI)
        if chosen:
            out = f"{out.rstrip()} {chosen}"

    # the per group tag only if explicitly enabled
    if tag and group_id is not None:
        code = group_tag(group_id)
        if code:
            sep = random.choice(SEPARATORS)
            out = f"{out.rstrip()}{sep}{code}"

    return out


# ── the send budget ──────────────────────────────────────────────────────
def account_age_days(session_data: dict) -> int:
    """How many days the account has been connected."""
    session_data = session_data or {}
    created = session_data.get("created_at") or session_data.get("added_at")
    if not created:
        stats = session_data.get("stats") or {}
        created = stats.get("since")
    if not created:
        return 30          # an old account without a date
    try:
        return max(0, int((time.time() - float(created)) // 86400))
    except (TypeError, ValueError):
        return 30


def daily_budget(session_data: dict) -> int:
    """The maximum number of messages this account may send today."""
    age = account_age_days(session_data)
    for day, budget in sorted(WARMUP_BUDGET.items()):
        if age <= day:
            return budget
    return FREE_BUDGET


def hourly_budget(session_data: dict) -> int:
    return int((session_data or {}).get("settings", {}).get(
        "auto_reply_global_gap", DEFAULT_HOURLY_BUDGET) or DEFAULT_HOURLY_BUDGET)


def used_today(session_data: dict) -> int:
    stats = (session_data or {}).get("stats") or {}
    return int(stats.get("sends_today", 0) or 0)


def used_this_hour(session_data: dict) -> int:
    stats = (session_data or {}).get("stats") or {}
    return int(stats.get("sends_this_hour", 0) or 0)


def budget_state(session_data: dict) -> dict:
    """Returns the whole budget picture of an account."""
    session_data = session_data or {}
    age = account_age_days(session_data)
    day_max = daily_budget(session_data)
    hour_max = hourly_budget(session_data)
    used_d = used_today(session_data)
    used_h = used_this_hour(session_data)
    return {
        "age_days": age,
        "daily_max": day_max,
        "daily_used": used_d,
        "daily_left": max(0, day_max - used_d),
        "hourly_max": hour_max,
        "hourly_used": used_h,
        "hourly_left": max(0, hour_max - used_h),
        "warming_up": age < 8,
    }


def record_send(session_data: dict) -> dict:
    """
    Counts a sent message against the budget.

    The counters are reset when the day / the hour changes.
    """
    session_data = session_data or {}
    stats = session_data.setdefault("stats", {})
    now = time.time()

    day_key = int(now // 86400)
    hour_key = int(now // 3600)

    if stats.get("send_day") != day_key:
        stats["send_day"] = day_key
        stats["sends_today"] = 0
    if stats.get("send_hour") != hour_key:
        stats["send_hour"] = hour_key
        stats["sends_this_hour"] = 0

    stats["sends_today"] = int(stats.get("sends_today", 0) or 0) + 1
    stats["sends_this_hour"] = int(stats.get("sends_this_hour", 0) or 0) + 1
    return stats


def can_send(session_data: dict) -> tuple:
    """
    Returns (allowed, reason).
    """
    state = budget_state(session_data)
    if state["daily_left"] <= 0:
        return False, (f"the daily budget of {state['daily_max']} messages is used up "
                       f"(the account is {state['age_days']} day(s) old)")
    if state["hourly_left"] <= 0:
        return False, f"the hourly budget of {state['hourly_max']} messages is used up"
    return True, ""


# ── the @SpamBot status ─────────────────────────────────────────────────
def parse_spam_status(text: str) -> dict:
    """
    Parses the answer of @SpamBot.

        {"limited": bool, "severity": "none|temporary|permanent",
         "until": str|None, "raw": str}
    """
    raw = text or ""
    low = raw.lower()
    result = {"limited": False, "severity": "none", "until": None, "raw": raw}

    # The negative phrases MUST be checked first - "You're not limited"
    # contains the word "limited" and would otherwise be read as a limit.
    negative = ("not limited", "no restriction", "no limits", "you're good",
                "you are good", "nothing to worry", "all good", "no limit")
    if any(phrase in low for phrase in negative):
        return result

    if "permanently" in low or "permanent" in low:
        result["limited"] = True
        result["severity"] = "permanent"
    elif "limited" in low or "restriction" in low or "flood" in low:
        result["limited"] = True
        result["severity"] = "temporary"
        m = re.search(r"(until\s+[^\n.]+)", low)
        if m:
            result["until"] = m.group(1).strip()
    return result


async def check_spam_status(client, force: bool = False) -> dict:
    """
    Asks @SpamBot about the current restriction of this account.

    The answer is cached for 6 hours so @SpamBot is not bothered.
    """
    try:
        from villain_ads_bot.data import database
    except Exception:
        database = None

    cached = None
    if database is not None:
        try:
            cached = (database.get_global_settings() or {}).get("spambot_cache", {}).get(str(id(client)))
        except Exception:
            cached = None
    if cached and not force:
        try:
            if time.time() - float(cached.get("ts", 0)) < 21600:
                return cached.get("data", {})
        except Exception:
            pass

    result = {"limited": False, "severity": "none", "until": None, "raw": ""}
    try:
        # NOTE: there is NO "GetBotRequest" in Telethon - calling it always
        # raised "module 'telethon.tl.functions.messages' has no attribute
        # 'GetBotRequest'". The entity is resolved directly instead.
        entity = await client.get_entity(SPAM_BOT)
        # Start the bot if it was never started by this account.
        try:
            from telethon.tl.functions.messages import StartBotRequest
            await client(StartBotRequest(entity, "/start", random.randint(0, 2**31 - 1)))
        except Exception:
            pass
        await asyncio_sleep_safe(2.0)
        # Ask again and read the answer.
        try:
            await client.send_message(entity, "/start")
        except Exception:
            pass
        await asyncio_sleep_safe(3.0)

        answer = ""
        messages = await client.get_messages(entity, limit=5)
        for message in messages:
            text = getattr(message, "text", None) or getattr(message, "message", None) or ""
            if not text:
                continue
            low = text.lower()
            if "spam" in low or "limit" in low or "restriction" in low or "flood" in low:
                answer = text
                break
        if not answer and messages:
            first = messages[0]
            answer = getattr(first, "text", None) or getattr(first, "message", None) or ""
        if not answer:
            result["error"] = "@SpamBot did not answer (the account may never have started it)"
        else:
            result = parse_spam_status(answer)
    except Exception as e:
        logger.warning(f"Could not read the @SpamBot status: {e}")
        result["error"] = str(e)

    if database is not None:
        try:
            settings = database.get_global_settings() or {}
            cache = settings.get("spambot_cache", {}) or {}
            cache[str(id(client))] = {"ts": time.time(), "data": result}
            settings["spambot_cache"] = cache
            database.save_global_settings(settings)
        except Exception:
            pass
    return result


async def asyncio_sleep_safe(seconds: float):
    import asyncio
    await asyncio.sleep(seconds)
