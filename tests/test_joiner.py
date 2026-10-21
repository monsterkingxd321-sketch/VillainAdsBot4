"""Offline test for the flood-limit aware bulk joiner."""
import asyncio
import sys

from villain_ads_bot.core import joiner

# ── Fake Telethon client that raises a FloodWait once ───────────────────
class FloodWaitError(Exception):
    def __init__(self, seconds):
        super().__init__(f"Flood wait: {seconds}s")
        self.seconds = seconds


STATE = {"joined": [], "calls": 0, "flooded": False}


class FakeClient:
    """Fake client - the join function is monkeypatched below."""


async def fake_join(client, link):
    STATE["calls"] += 1
    if link == "flood" and not STATE["flooded"]:
        STATE["flooded"] = True
        raise FloodWaitError(3)
    STATE["joined"].append(link)
    return True


import villain_ads_bot.services.userbot as ub

ub.join_channel_single = fake_join


async def main():
    # A link that raises a flood wait must NOT abort the round.
    result = await joiner.join_many(FakeClient(), ["a", "flood", "b"], delay=0.01)
    print("result:", result)
    assert result["total"] == 3
    assert result["joined"] == 3, result
    assert result["flood_waits"] == 1, result
    assert result["wait_total"] >= 3, result
    assert STATE["joined"] == ["a", "flood", "b"], STATE["joined"]

    # The summary must mention the flood wait and the remaining groups.
    summary = joiner.format_result(result, "Test")
    assert "Flood waits" in summary, summary
    print("\n" + summary)

    print("\nALL JOINER TESTS PASSED")


asyncio.run(main())
