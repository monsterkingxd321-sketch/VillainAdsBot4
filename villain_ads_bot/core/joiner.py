"""
Bulk group / channel joining with flood-limit control.

When Telegram returns a `FloodWaitError` the remaining groups are NOT
skipped - the joiner waits for the requested time and then continues with
the next group automatically. The user always knows how far the process is
and how much time is still needed.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Default pause between two join requests (safe delay to prevent Telegram flood limits)
DEFAULT_DELAY = 4.0
# Maximum time we are willing to wait for a single flood wait
MAX_FLOOD_WAIT = 900


def _flood_seconds(exc) -> int:
    """Extracts the number of seconds from a flood wait exception."""
    for attr in ("seconds", "timeout"):
        value = getattr(exc, attr, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    return 0


async def join_many(client, links, progress_msg=None, delay: float = DEFAULT_DELAY,
                    progress_callback=None) -> dict:
    """
    Joins every link in `links` sequentially.
    """
    from villain_ads_bot.services.userbot import join_channel_detailed

    total = len(links)
    joined = 0
    failed = 0
    flood_waits = 0
    wait_total = 0
    failure_reasons = []
    started = time.time()

    for index, link in enumerate(links, 1):
        remaining = total - index + 1
        try:
            ok, reason = await join_channel_detailed(client, link)
            if ok:
                joined += 1
            else:
                failed += 1
                failure_reasons.append(f"{link}: {reason}")
        except Exception as exc:
            seconds = _flood_seconds(exc)
            if seconds:
                flood_waits += 1
                seconds = min(seconds, MAX_FLOOD_WAIT)
                wait_total += seconds
                logger.warning(
                    f"Flood wait of {seconds}s while joining group {index}/{total} - waiting."
                )
                for s_wait in range(seconds, 0, -1):
                    if progress_msg is not None and (s_wait % 2 == 0 or s_wait <= 5):
                        try:
                            await progress_msg.edit(
                                f"<blockquote><b>» ⏳ Telegram Flood Limit Hit</b>\n\n"
                                f"• <b>✅ Joined So Far :</b> <b>{joined}</b>\n"
                                f"• <b>⏳ Remaining Groups :</b> <b>{remaining}</b>\n"
                                f"• <b>⏱️ Waiting Timer :</b> <code>{s_wait}s</code>\n\n"
                                f"💡 <i>Process will resume automatically once timer reaches 0.</i></blockquote>",
                                parse_mode="html"
                            )
                        except Exception:
                            pass
                    await asyncio.sleep(1)

                try:
                    ok, retry_reason = await join_channel_detailed(client, link)
                    if ok:
                        joined += 1
                    else:
                        failed += 1
                        failure_reasons.append(f"{link}: {retry_reason}")
                except Exception as retry_exc:
                    logger.warning(f"Could not join {link} after the flood wait: {retry_exc}")
                    failed += 1
                    failure_reasons.append(f"{link}: {retry_exc}")
            else:
                logger.warning(f"Could not join {link}: {exc}")
                failed += 1
                failure_reasons.append(f"{link}: {exc}")

        elapsed = int(time.time() - started)
        if progress_msg is not None:
            try:
                await progress_msg.edit(
                    f"<blockquote><b>» ⏳ Joining Groups... ({index}/{total})</b>\n\n"
                    f"• <b>✅ Joined :</b> <b>{joined}</b>\n"
                    f"• <b>❌ Failed :</b> <b>{failed}</b>\n"
                    f"• <b>⏳ Remaining :</b> <b>{total - index}</b>\n"
                    f"• <b>⏱️ Elapsed :</b> <code>{elapsed}s</code>"
                    + (f"\n• <b>⏸️ Flood Waits :</b> <b>{flood_waits}</b> (<code>{wait_total}s</code> waited)" if flood_waits else "")
                    + "</blockquote>",
                    parse_mode="html"
                )
            except Exception:
                pass

        if progress_callback is not None:
            try:
                await progress_callback(index, total, {
                    "joined": joined, "failed": failed, "remaining": remaining,
                    "flood_waits": flood_waits, "wait_total": wait_total,
                    "elapsed": elapsed,
                })
            except Exception:
                pass

        if index < total:
            wait_delay = int(delay) if delay > 1 else 1
            for s_delay in range(wait_delay, 0, -1):
                if progress_msg is not None:
                    try:
                        await progress_msg.edit(
                            f"<blockquote><b>» ⏳ Joining Groups... ({index}/{total})</b>\n\n"
                            f"• <b>✅ Joined :</b> <b>{joined}</b>\n"
                            f"• <b>❌ Failed :</b> <b>{failed}</b>\n"
                            f"• <b>⏳ Remaining :</b> <b>{total - index}</b>\n"
                            f"• <b>⏱️ Next Join In :</b> <code>{s_delay}s</code> (Anti-Flood Guard)</blockquote>",
                            parse_mode="html"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(1)

    total_time = int(time.time() - started)
    return {
        "total": total,
        "joined": joined,
        "failed": failed,
        "flood_waits": flood_waits,
        "wait_total": wait_total,
        "total_time": total_time,
        "failure_reasons": failure_reasons,
        "failed_reasons": failure_reasons,
    }


def format_result(result: dict, title: str = "Group Joining") -> str:
    """Builds the final summary of a bulk join."""
    total = result.get("total", 0)
    joined = result.get("joined", 0)
    failed = result.get("failed", 0)
    remaining = max(0, total - joined - failed)
    flood = result.get("flood_waits", 0)
    wait_total = result.get("wait_total", 0)
    total_time = result.get("total_time", 0)

    lines = [
        f"✅ <b>Joined :</b> <code>{joined}</code> / <code>{total}</code>",
        f"❌ <b>Failed :</b> <code>{failed}</code>",
        f"⏳ <b>Remaining :</b> <code>{remaining}</code>",
        f"⏱️ <b>Total time :</b> <code>{total_time}s</code>",
    ]
    if flood:
        lines.append(f"⏸️ <b>Flood waits :</b> <code>{flood}</code> (<code>{wait_total}s</code> waited)")

    reasons = result.get("failure_reasons") or result.get("failed_reasons")
    if reasons:
        lines.append("⚠️ <b>Failure Details :</b>")
        for r, cnt in reasons.items():
            lines.append(f"• {r}: <code>{cnt}</code>")

    summary = "\n".join(lines)
    if remaining:
        summary += (
            f"\n\n💡 <i>{remaining} group(s) could not be processed. "
            f"Run the command again for the remaining ones.</i>"
        )
    return f"📚 <b>{title}</b>\n\n{summary}"
