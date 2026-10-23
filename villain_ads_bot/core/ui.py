"""
Premium UI kit.

Every screen of the bot is built from these helpers so the whole product
looks consistent: a branded header, a clean body, a divider and a subtle
footer. All helpers return Telegram-flavoured HTML.
"""

from villain_ads_bot import config

# ── Design tokens ─────────────────────────────────────────────────────────
BRAND = "𝗫𝗧𝗥 𝗔𝗗 𝗕𝗢𝗧"
DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
THIN = "──────────────────────"

HEADER_EMOJI = "✦"
LOCK = "🔒"
SPARK = "✧"


def header(title: str, subtitle: str = "", emoji: str = "✦") -> str:
    """Premium header block."""
    out = [f"{emoji} <b>{title}</b>"]
    if subtitle:
        out.append(f"{SPARK} <i>{subtitle}</i>")
    return "\n".join(out)


def footer(text: str = "") -> str:
    """Premium footer block."""
    if not text:
        return f"{SPARK} <i>Powered by {BRAND}</i>"
    return f"{SPARK} <i>{text}</i>\n{SPARK} <i>Powered by {BRAND}</i>"


def card(title: str, body: str = "", subtitle: str = "", emoji: str = "✦") -> str:
    """
    Builds a full premium card with separate <blockquote> on each line.
    """
    parts = [header(title, subtitle, emoji)]
    if body:
        parts.append(body)
    raw = "\n".join(parts)
    lines = raw.split("\n")
    return "\n".join(f"<blockquote>{l}</blockquote>" if l.strip() else "" for l in lines)


def card_open(title: str, body: str = "", subtitle: str = "", emoji: str = "✦") -> str:
    """
    Opens a premium card with per-line blockquotes.
    """
    lines = header(title, subtitle, emoji).split("\n")
    out = [f"<blockquote>{l}</blockquote>" for l in lines if l.strip()]
    if body:
        out.extend(f"<blockquote>{l}</blockquote>" if l.strip() else "" for l in body.split("\n"))
    return "\n".join(out)


def card_close(footer_text: str = "") -> str:
    """Closes a premium card with per-line blockquotes."""
    lines = footer(footer_text).split("\n")
    return "\n".join(f"<blockquote>{l}</blockquote>" for l in lines if l.strip())


def section(title: str, body: str = "") -> str:
    """A titled section inside a card."""
    if not body:
        return f"<blockquote><b>{title}</b></blockquote>"
    lines = [f"<blockquote><b>{title}</b></blockquote>"]
    for l in body.split("\n"):
        if l.strip():
            lines.append(f"<blockquote>{l}</blockquote>")
        else:
            lines.append("")
    return "\n".join(lines)


def kv(label: str, value: str, icon: str = "•") -> str:
    """A single key/value line."""
    return f"<blockquote>{icon} <b>{label} :</b> <code>{value}</code></blockquote>"


def bullet(text: str, icon: str = "•") -> str:
    return f"<blockquote>{icon} {text}</blockquote>"


def status(enabled: bool, on_text: str = "ON", off_text: str = "OFF") -> str:
    """A coloured ON/OFF pill."""
    return f"🟢 <b>{on_text}</b>" if enabled else f"🔴 <b>{off_text}</b>"


def locked(text: str = "This action is reserved for the owner.") -> str:
    """A premium 'locked' notice with per-line blockquotes."""
    return (
        f"<blockquote>{LOCK} <b>Owner Only</b></blockquote>\n\n"
        f"<blockquote>{SPARK} <i>{text}</i></blockquote>"
    )


def money(amount: float) -> str:
    return f"₹{float(amount):,.2f}"


def progress_bar(current: int, total: int, width: int = 12) -> str:
    """A small text progress bar."""
    total = max(1, int(total))
    current = max(0, min(int(current), total))
    filled = int(width * current / total)
    return "▰" * filled + "▱" * (width - filled)


def page_title(title: str) -> str:
    """The standard page heading used across the dashboards."""
    return f"{HEADER_EMOJI} <b>{title}</b>"


def bot_signature() -> str:
    return f"{SPARK} <i>Powered by {BRAND}</i>"


def help_hint(text: str) -> str:
    return f"{SPARK} <i>{text}</i>"


# ── Validation ────────────────────────────────────────────────────────────
ALLOWED_TAGS = {
    "b", "i", "u", "s", "code", "pre", "a", "blockquote", "spoiler",
    "tg-spoiler", "br", "h1", "h2", "h3", "h4",
}


def validate_html(text: str) -> tuple:
    """
    Checks that a Telegram-HTML string has balanced tags.

    Returns (is_valid: bool, message: str)
    """
    import re

    if not text:
        return True, "empty"

    stack = []
    for match in re.finditer(r"<(/?)([a-zA-Z0-9\-]+)[^>]*?(/?)>", text):
        closing, tag, self_closing = match.group(1), match.group(2).lower(), match.group(3)
        if tag not in ALLOWED_TAGS:
            return False, f"unknown tag <{tag}>"
        if self_closing:
            continue
        if closing:
            if not stack:
                return False, f"closing </{tag}> without an opening tag"
            if stack[-1] != tag:
                return False, f"</{tag}> does not match <{stack[-1]}>"
            stack.pop()
        else:
            stack.append(tag)

    if stack:
        return False, f"unclosed tag(s): {', '.join(stack)}"
    return True, "ok"
