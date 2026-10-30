import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import types


class TestAllVcJoin(unittest.IsolatedAsyncioTestCase):
    async def test_find_active_group_vcs_fast_path(self):
        from villain_ads_bot.services.userbot import UserBot

        bot = UserBot(session_id="test_sess")
        bot.phone = "+1234567890"
        bot.is_running = True
        bot.client = MagicMock()
        bot.client.is_connected = MagicMock(return_value=True)

        # Dialog 1: Active supergroup VC (call_active=True)
        ch1 = types.Channel(id=101, title="Active Group 1", photo=None, date=None, call_active=True)
        d1 = MagicMock()
        d1.id = -100101
        d1.title = "Active Group 1"
        d1.is_group = True
        d1.is_channel = True
        d1.entity = ch1

        # Dialog 2: Inactive supergroup (call_active=False)
        ch2 = types.Channel(id=102, title="Inactive Group 2", photo=None, date=None, call_active=False)
        d2 = MagicMock()
        d2.id = -100102
        d2.title = "Inactive Group 2"
        d2.is_group = True
        d2.is_channel = True
        d2.entity = ch2

        # Dialog 3: Active supergroup with call_not_empty=True
        ch3 = types.Channel(id=103, title="Active Group 3", photo=None, date=None, call_active=False, call_not_empty=True)
        d3 = MagicMock()
        d3.id = -100103
        d3.title = "Active Group 3"
        d3.is_group = True
        d3.is_channel = True
        d3.entity = ch3

        bot.client.get_dialogs = AsyncMock(return_value=[d1, d2, d3])

        active_vcs = await bot.find_active_group_vcs()
        active_ids = [cid for cid, _, _ in active_vcs]

        self.assertIn(-100101, active_ids)
        self.assertIn(-100103, active_ids)
        self.assertNotIn(-100102, active_ids)
        self.assertEqual(len(active_vcs), 2)

    async def test_join_all_active_group_vcs(self):
        from villain_ads_bot.services.userbot import UserBot

        bot = UserBot(session_id="test_sess2")
        bot.phone = "+1234567891"
        bot.is_running = True
        bot.client = MagicMock()
        bot.client.is_connected = MagicMock(return_value=True)

        ch1 = types.Channel(id=201, title="Active Group A", photo=None, date=None, call_active=True)
        d1 = MagicMock(id=-100201, title="Active Group A", is_group=True, entity=ch1)
        ch2 = types.Channel(id=202, title="Active Group B", photo=None, date=None, call_active=True)
        d2 = MagicMock(id=-100202, title="Active Group B", is_group=True, entity=ch2)

        bot.client.get_dialogs = AsyncMock(return_value=[d1, d2])
        bot.join_voice_chat = AsyncMock(return_value=(True, "Connected"))

        joined, total = await bot.join_all_active_group_vcs()
        self.assertEqual(total, 2)
        self.assertEqual(joined, 2)
        self.assertEqual(bot.join_voice_chat.await_count, 2)

    async def test_join_all_userbots_to_active_vcs(self):
        from villain_ads_bot.services.userbot import join_all_userbots_to_active_vcs

        bot1 = MagicMock()
        bot1.is_running = True
        bot1.join_all_active_group_vcs = AsyncMock(return_value=(2, 2))

        bot2 = MagicMock()
        bot2.is_running = True
        bot2.join_all_active_group_vcs = AsyncMock(return_value=(3, 3))

        with patch("villain_ads_bot.services.userbot_manager.get_running_userbots_for_owner", return_value=[bot1, bot2]):
            total_joined, total_found, bot_count = await join_all_userbots_to_active_vcs(owner_id=12345)
            self.assertEqual(bot_count, 2)
            self.assertEqual(total_joined, 5)
            self.assertEqual(total_found, 5)


if __name__ == "__main__":
    unittest.main()
