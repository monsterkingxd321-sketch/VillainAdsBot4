"""Offline test for the error reporter (fake bot client, no Telegram)."""
import asyncio
import logging

from villain_ads_bot.core import error_reporter

SENT = []


class FakeClient:
    async def send_message(self, chat_id, text, **kwargs):
        SENT.append((chat_id, text))
        return True


def fake_log_group_id():
    return -100123456


def fake_owner_ids():
    return [777000]


error_reporter.get_log_group_id = fake_log_group_id
error_reporter.get_owner_ids = fake_owner_ids
error_reporter.set_bot_client(FakeClient())
error_reporter.setup_error_reporter()


async def main():
    # 1. Direct report
    error_reporter.report("test:context", ValueError("boom"))
    await error_reporter._flush_once()
    assert len(SENT) == 2, SENT
    assert SENT[0][0] == -100123456, "must go to the log group"
    assert SENT[1][0] == 777000, "must go to the owner"
    assert "test:context" in SENT[0][1]
    assert "boom" in SENT[0][1]
    print("1) report -> log group + owner OK")

    # 2. A logging ERROR record is captured
    SENT.clear()
    logging.getLogger("some.module").error("logging failure %s", "here")
    await error_reporter._flush_once()
    assert len(SENT) == 2, SENT
    assert "logging failure here" in SENT[0][1]
    assert "some.module" in SENT[0][1]
    print("2) logger.error -> log group + owner OK")

    # 3. The end user must never see the raw exception
    from villain_ads_bot.core import utils
    msg = utils.user_safe_error("ctx", RuntimeError("secret internal detail"))
    assert "secret internal detail" not in msg, msg
    assert "reported to the admin team" in msg, msg
    print("3) user_safe_error hides internals OK")

    # 4. Dedupe: the same error twice within 60s is only reported once
    SENT.clear()
    error_reporter._buffer.clear()
    error_reporter.report("dup:ctx", ValueError("same"))
    error_reporter.report("dup:ctx", ValueError("same"))
    await error_reporter._flush_once()
    assert len(SENT) == 2, f"expected 1 report (2 deliveries), got {len(SENT)}"
    print("4) duplicate errors are deduped OK")

    # 5. Hourly cap
    SENT.clear()
    error_reporter._reports_this_hour[0] = error_reporter.MAX_REPORTS_PER_HOUR
    error_reporter.report("cap:ctx", ValueError("x"))
    await error_reporter._flush_once()
    assert len(SENT) == 0, "hourly cap must drop the report"
    error_reporter._reports_this_hour[0] = 0
    print("5) hourly rate limit OK")

    print("\nALL ERROR REPORTER TESTS PASSED")


asyncio.run(main())
