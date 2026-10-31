import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from villain_ads_bot.services.userbot import UserBot
from villain_ads_bot.services import userbot_manager


class TestUserBotLifecycle(unittest.IsolatedAsyncioTestCase):
    def test_userbot_methods_exist(self):
        # Verify that all essential methods are present on UserBot class
        for method_name in [
            "start",
            "stop",
            "reload_settings",
            "leave_voice_chat",
            "join_voice_chat",
            "find_active_group_vcs",
            "join_all_active_group_vcs",
            "broadcast_loop",
            "_register_handlers",
        ]:
            self.assertTrue(
                hasattr(UserBot, method_name),
                f"UserBot is missing required method: {method_name}"
            )

    async def test_start_userbot_single_arg(self):
        with patch.object(UserBot, "start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = True
            with patch("villain_ads_bot.data.database.get_session", return_value={"phone": "+1234567890"}):
                res = await userbot_manager.start_userbot("+1234567890")
                self.assertTrue(res)
                mock_start.assert_awaited_once()

    async def test_start_userbot_two_args(self):
        with patch.object(UserBot, "start", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = True
            with patch("villain_ads_bot.data.database.get_session", return_value={"phone": "+1234567891"}):
                res = await userbot_manager.start_userbot("+1234567891", owner_id=98765)
                self.assertTrue(res)
                mock_start.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
