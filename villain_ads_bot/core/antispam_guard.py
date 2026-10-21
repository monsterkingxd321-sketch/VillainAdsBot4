"""
The @SpamBot protection is a WARNING, never a limit.

The owner pays for the slots and expects to broadcast whenever he wants.
The bot must NEVER silently stop a broadcast. The warm up is therefore only
ADVISED: the owner sees "this account is 2 days old, 3 messages a day is
the safe amount" and decides himself.

The whole protection can be switched off in the admin panel.
"""

import time

from villain_ads_bot.core import antispam


def is_enabled() -> bool:
    """Returns True when the anti spam advice is enabled."""
    try:
        from villain_ads_bot.data import database
        settings = database.get_global_settings() or {}
        return bool(settings.get("anti_spam_protection", True))
    except Exception:
        return True


def health_text(session_data: dict) -> str:
    """
    A short health summary of an account (for the dashboard).

    It never blocks anything - it only tells the owner what is safe.
    """
    if not is_enabled():
        return "Anti-spam protection is switched off."

    state = antispam.budget_state(session_data)
    age = state.get("age_days", 0)
    recommended = state.get("daily_max", 0)
    used = state.get("daily_used", 0)

    if age < 8:
        return (f"Account is <b>{age} day(s)</b> old. "
                f"Safe amount today: <code>~{recommended}</code> "
                f"(sent so far: <code>{used}</code>).")
    return (f"Account is <b>{age} day(s)</b> old. "
            f"Safe amount today: <code>~{recommended}</code> "
            f"(sent so far: <code>{used}</code>).")


def advice(session_data: dict) -> str:
    """
    Returns a short advice line when the account is young and a lot was
    already sent today - or an empty string.
    """
    if not is_enabled():
        return ""
    state = antispam.budget_state(session_data)
    if state.get("age_days", 0) >= 8:
        return ""
    if state.get("daily_used", 0) < state.get("daily_max", 0):
        return ""
    return (f"⚠️ This account is only {state.get('age_days', 0)} day(s) old and already "
            f"sent {state.get('daily_used', 0)} messages today. "
            f"Sending much more can get it limited by @SpamBot.")


def record(session_data: dict) -> dict:
    """Counts a sent message (the counters are only informative)."""
    return antispam.record_send(session_data)
