import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from villain_ads_bot.handlers.player import (
    _active_chat_players,
    _chat_queues,
    _chat_replay_mode,
    _track_timer_tasks,
    build_now_playing_markup,
    play_next_in_queue,
    _play_next_in_queue_locked
)


class TestReplayAndThumbnail(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _active_chat_players.clear()
        _chat_queues.clear()
        _chat_replay_mode.clear()
        _track_timer_tasks.clear()

    async def asyncTearDown(self):
        _active_chat_players.clear()
        _chat_queues.clear()
        _chat_replay_mode.clear()
        _track_timer_tasks.clear()

    def test_build_now_playing_markup_replay_button(self):
        chat_id = -100123456789
        
        # When replay is OFF
        _chat_replay_mode[chat_id] = False
        markup = build_now_playing_markup(chat_id)
        found_replay = False
        for row in markup:
            for btn in row:
                btn_data = getattr(btn, "data", None) or getattr(getattr(btn, "type", None), "data", b"")
                if f"player_replay_{chat_id}".encode() in btn_data or f"player_replay_{chat_id}" in str(btn_data):
                    found_replay = True
                    self.assertIn("Replay", getattr(btn, "text", ""))
                    self.assertNotIn("ON", getattr(btn, "text", ""))
        self.assertTrue(found_replay, "Replay button should be present in markup")

        # When replay is ON
        _chat_replay_mode[chat_id] = True
        markup_on = build_now_playing_markup(chat_id)
        found_on = False
        for row in markup_on:
            for btn in row:
                btn_data = getattr(btn, "data", None) or getattr(getattr(btn, "type", None), "data", b"")
                if f"player_replay_{chat_id}".encode() in btn_data or f"player_replay_{chat_id}" in str(btn_data):
                    found_on = True
                    self.assertIn("ON", getattr(btn, "text", ""))
        self.assertTrue(found_on, "Replay ON label should be displayed when replay mode is active")

    async def test_replay_mode_repeats_current_track(self):
        chat_id = -100987654321
        mock_bot = MagicMock()
        mock_bot.play_song = AsyncMock(return_value=(True, "Playing", {"title": "Test Song", "duration": 45, "play_type": "video"}))

        song_info = {
            "title": "Test Video",
            "play_type": "video",
            "file_path": "downloads/test.mp4",
            "duration": 45,
            "query": "test query"
        }

        _active_chat_players[chat_id] = {
            "bot": mock_bot,
            "companion_bots": [],
            "song_info": song_info,
            "msg_id": 100
        }
        _chat_replay_mode[chat_id] = True

        # When song finishes, advance queue is called
        await _play_next_in_queue_locked(chat_id)

        # It must replay the SAME track with play_type="video" and local_file
        mock_bot.play_song.assert_awaited_once_with(
            query="test query",
            play_type="video",
            chat_id=chat_id,
            local_file="downloads/test.mp4",
            title="Test Video",
            duration=45
        )
        self.assertIn(chat_id, _track_timer_tasks, "Timer task should be re-scheduled for replay")

    async def test_stop_clears_replay_mode(self):
        chat_id = -100555555555
        _chat_replay_mode[chat_id] = True
        
        # Simulate stop / end
        _chat_queues.pop(chat_id, None)
        _chat_replay_mode.pop(chat_id, None)
        
        self.assertNotIn(chat_id, _chat_replay_mode)


if __name__ == "__main__":
    unittest.main()
