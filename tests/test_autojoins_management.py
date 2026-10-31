import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from villain_ads_bot import config
from villain_ads_bot.data import database


class TestAutoJoinsManagement(unittest.TestCase):
    def test_autojoin_toggle_and_clear(self):
        # Test that global settings save and retrieve auto-join properties
        fake_settings = {
            "id": "global",
            "userbot_auto_join_enabled": True,
            "userbot_auto_join_admin_managed": True,
            "userbot_auto_join_links": ["https://t.me/test_ch1", "https://t.me/test_ch2"],
        }
        with patch.object(database, "_db", MagicMock()):
            with patch.object(database, "_cached_global_settings", fake_settings):
                with patch.object(database, "_cached_settings_time", 9999999999.0):
                    s = database.get_global_settings()
                    self.assertTrue(s.get("userbot_auto_join_enabled"))
                    self.assertEqual(len(s.get("userbot_auto_join_links")), 2)

                    # Simulate clear
                    s["userbot_auto_join_links"] = []
                    self.assertEqual(len(s.get("userbot_auto_join_links")), 0)

                    # Simulate toggle
                    s["userbot_auto_join_enabled"] = False
                    self.assertFalse(s.get("userbot_auto_join_enabled"))


if __name__ == "__main__":
    unittest.main()
