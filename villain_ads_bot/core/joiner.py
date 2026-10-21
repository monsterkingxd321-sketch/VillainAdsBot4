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

# Default pause between two join requests (Telegram is very strict here)
DEFAULT_DELAY = 2.0
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

    Parameters
    ----------
    client          : the userbot client
    links           : list of invite links / usernames
    progress_msg    : optional Telethon message that is updated live
    delay           : pause between two join requests
    progress_callback: optional coroutine ``callback(done, total, state)``

    Returns a dict with the counters and the total time that was needed.
    """
    from villain_ads_bot.services.userbot import join_channel_single

    total = len(links)
    joined = 0
    failed = 0
    flood_waits = 0
    wait_total = 0
    started = time.time()

    for index, link in enumerate(links, 1):
        remaining = total - index + 1
        try:
            ok = await join_channel_single(client, link)
            if ok:
                joined += 1
            else:
                failed += 1
        except Exception as exc:
            seconds = _flood_seconds(exc)
            if seconds:
                # ── Flood limit: wait it out and continue automatically ──
                flood_waits += 1
                seconds = min(seconds, MAX_FLOOD_WAIT)
                wait_total += seconds
                logger.warning(
                    f"Flood wait of {seconds}s while joining group {index}/{total} - waiting."
                )
                if progress_msg is not None:
                    try:
                        await progress_msg.edit(
                            f"⏳ <b>Flood Limit Hit</b>\n\n"
                            f"✅ <b>Joined :</b> {joined}\n"
                            f"⏳ <b>Remaining :</b> {remaining}\n"
                            f"⏱️ <b>Waiting :</b> {seconds}s\n\n"
                            f"<i>The remaining groups will be joined automatically afterwards.</i>",
                            parse_mode="html"
                        )
                    except Exception:
                        pass
                try:
                    await asyncio.sleep(seconds)
                except asyncio.CancelledError:
                    break
                # Retry the group that hit the flood limit.
                try:
                    ok = await join_channel_single(client, link)
                    if ok:
                        joined += 1
                    else:
                        failed += 1
                except Exception as retry_exc:
                    logger.warning(f"Could not join {link} after the flood wait: {retry_exc}")
                    failed += 1
            else:
                logger.warning(f"Could not join {link}: {exc}")
                failed += 1

        elapsed = int(time.time() - started)
        if progress_msg is not None:
            try:
                await progress_msg.edit(
                    f"⏳ <b>Joining Groups...</b> <code>({index}/{total})</code>\n\n"
                    f"✅ <b>Joined :</b> {joined}\n"
                    f"❌ <b>Failed :</b> {failed}\n"
                    f"⏳ <b>Remaining :</b> {remaining}\n"
                    f"⏱️ <b>Elapsed :</b> {elapsed}s"
                    + (f"\n⏸️ <b>Flood waits :</b> {flood_waits} ({wait_total}s waited)"
                       if flood_waits else ""),
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
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break

    total_time = int(time.time() - started)
    return {
        "total": total,
        "joined": joined,
        "failed": failed,
        "flood_waits": flood_waits,
        "wait_total": wait_total,
        "total_time": total_time,
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

    summary = "\n".join(lines)
    if remaining:
        summary += (
            f"\n\n💡 <i>{remaining} group(s) could not be processed. "
            f"Run the command again for the remaining ones.</i>"
        )
    return f"📚 <b>{title}</b>\n\n{summary}"
