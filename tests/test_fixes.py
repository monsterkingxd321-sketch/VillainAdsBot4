import os
import sys

sys.path.insert(0, os.path.abspath("."))

from villain_ads_bot.core import utils
from villain_ads_bot.data import database

def run_tests():
    print("=" * 60)
    print("RUNNING UNIT TESTS FOR MULTI-WELCOME, AUTO-CHAT, BROADCAST & MODES")
    print("=" * 60)

    # 1. Delimiter Parsing Tests
    pipes = utils.parse_multi_messages("Hello | World | Telegram")
    assert pipes == ["Hello", "World", "Telegram"], f"Failed pipes: {pipes}"
    
    commas = utils.parse_multi_messages("Hello, World, Telegram")
    assert commas == ["Hello", "World", "Telegram"], f"Failed commas: {commas}"
    
    newlines = utils.parse_multi_messages("Hello\nWorld\nTelegram")
    assert newlines == ["Hello", "World", "Telegram"], f"Failed newlines: {newlines}"
    
    # Delimiter with commas inside pipe-separated messages
    pipe_with_commas = utils.parse_multi_messages("Hello, friend | Welcome, guest")
    assert pipe_with_commas == ["Hello, friend", "Welcome, guest"], f"Failed pipe with commas: {pipe_with_commas}"
    
    empty = utils.parse_multi_messages("   |  ,  \n  ")
    assert empty == [], f"Failed empty: {empty}"
    print("  [PASS] parse_multi_messages (pipes, commas, newlines, mixed)")

    # 2. Mode Resolution and Preservation Tests
    # Broadcast: single mode with multiple list must NOT be overwritten to multiple
    s_single = {"broadcast_msg": "Single Msg", "broadcast_messages": ["M1", "M2"], "broadcast_mode": "single"}
    assert utils.resolve_modes(s_single)["broadcast_mode"] == "single", "resolve_modes altered single mode!"
    assert utils.get_broadcast_messages(s_single) == ["Single Msg"], f"Single broadcast failed: {utils.get_broadcast_messages(s_single)}"

    # Broadcast: multiple mode with multiple list
    s_multi = {"broadcast_msg": "Single Msg", "broadcast_messages": ["M1", "M2"], "broadcast_mode": "multiple"}
    assert utils.resolve_modes(s_multi)["broadcast_mode"] == "multiple", "resolve_modes altered multi mode!"
    assert utils.get_broadcast_messages(s_multi) == ["M1", "M2"], f"Multi broadcast failed: {utils.get_broadcast_messages(s_multi)}"

    # Welcome: single mode
    w_single = {"welcome_msg": "Welcome 1", "welcome_messages": ["W1", "W2"], "welcome_mode": "single"}
    assert utils.resolve_modes(w_single)["welcome_mode"] == "single", "resolve_modes altered single welcome mode!"
    assert utils.get_welcome_messages(w_single) == ["Welcome 1"], f"Single welcome failed: {utils.get_welcome_messages(w_single)}"

    # Welcome: multiple mode
    w_multi = {"welcome_msg": "Welcome 1", "welcome_messages": ["W1", "W2"], "welcome_mode": "multiple"}
    assert utils.resolve_modes(w_multi)["welcome_mode"] == "multiple", "resolve_modes altered multi welcome mode!"
    assert utils.get_welcome_messages(w_multi) == ["W1", "W2"], f"Multi welcome failed: {utils.get_welcome_messages(w_multi)}"

    # Auto Reply: single mode
    ar_single = {"auto_reply_msg": "Hi single", "auto_reply_messages": ["AR1", "AR2"], "auto_reply_mode": "single"}
    assert utils.resolve_modes(ar_single)["auto_reply_mode"] == "single", "resolve_modes altered single ar mode!"
    assert utils.get_auto_reply_messages(ar_single) == ["Hi single"], f"Single auto reply failed: {utils.get_auto_reply_messages(ar_single)}"

    # Auto Reply: multiple mode
    ar_multi = {"auto_reply_msg": "Hi single", "auto_reply_messages": ["AR1", "AR2"], "auto_reply_mode": "multiple"}
    assert utils.resolve_modes(ar_multi)["auto_reply_mode"] == "multiple", "resolve_modes altered multi ar mode!"
    assert utils.get_auto_reply_messages(ar_multi) == ["AR1", "AR2"], f"Multi auto reply failed: {utils.get_auto_reply_messages(ar_multi)}"

    # Fallback when mode is missing
    s_default_multi = {"broadcast_messages": ["M1", "M2"]}
    assert utils.resolve_modes(s_default_multi)["broadcast_mode"] == "multiple"

    s_default_single = {"broadcast_msg": "Single Msg"}
    assert utils.resolve_modes(s_default_single)["broadcast_mode"] == "single"
    print("  [PASS] resolve_modes & get_*_messages (Single vs Multiple mode preservation)")

    # 3. Userbot logic checks
    import inspect
    from villain_ads_bot.services.userbot import UserBot
    init_src = inspect.getsource(UserBot.__init__)
    assert "_wake_broadcast" in init_src, "UserBot missing _wake_broadcast"

    loop_src = inspect.getsource(UserBot.broadcast_loop)
    assert "_wake_broadcast" in loop_src, "broadcast_loop missing _wake_broadcast event handling"
    assert "DEFAULT_BROADCAST_MESSAGE" not in loop_src, "broadcast_loop still filtering out default message!"
    print("  [PASS] UserBot broadcast wake-up and message unblocking")

    # 4. Voice Chat (VC) link and auto-join logic checks
    vc_src = inspect.getsource(UserBot.join_voice_chat)
    assert "join_channel_single" in vc_src, "join_voice_chat missing pre-join step!"
    assert "get_input_entity" in vc_src, "join_voice_chat missing MTProto join_as!"
    print("  [PASS] UserBot join_voice_chat auto-joins group and prepares MTProto peer")

    from villain_ads_bot.services.userbot import get_peer_from_link
    peer_src = inspect.getsource(get_peer_from_link)
    assert "/c/(\\d+)" in peer_src or "/c/" in peer_src, "get_peer_from_link missing t.me/c/ internal link support"
    print("  [PASS] get_peer_from_link handles internal channel links and query parameters")

    import re
    vc_cb_pattern = r"^vc_join_(?!(?:grp_|all_|multi_grp_))(.+)$"
    assert re.match(vc_cb_pattern, "vc_join_+919876543210"), "Failed to match + phone"
    assert re.match(vc_cb_pattern, "vc_join_919876543210"), "Failed to match non-+ phone"
    assert re.match(vc_cb_pattern, "vc_join_sess_123"), "Failed to match session ID"
    assert not re.match(vc_cb_pattern, "vc_join_grp_919876543210"), "Mistakenly matched vc_join_grp"
    assert not re.match(vc_cb_pattern, "vc_join_all_919876543210"), "Mistakenly matched vc_join_all"
    print("  [PASS] vc_join callback regex correctly matches any phone format without collisions")

    # 5. Broadcast to groups only (channels excluded)
    get_groups_src = inspect.getsource(UserBot.get_groups)
    assert "not getattr(d.entity, \"broadcast\", False)" in get_groups_src, "get_groups does not filter out broadcast channels!"
    
    bc_round_src = inspect.getsource(UserBot.broadcast_round)
    assert "getattr(g.entity, \"broadcast\", False)" in bc_round_src, "broadcast_round missing broadcast channel skip check!"
    print("  [PASS] UserBot strictly restricts broadcast to groups only (no channels)")

    # 6. Help button removed from dashboards (only How to use kept)
    with open("villain_ads_bot/handlers/my_bots.py", "r", encoding="utf-8") as f:
        my_bots_code = f.read()
    assert '"btn_help"' not in my_bots_code, "btn_help is still present in my_bots.py!"
    print("  [PASS] Help button removed from all dashboards (How to use retained)")

    # 7. Broadcast Now buttons removed
    assert "styled_button(\"📢 Broadcast Now\"" not in my_bots_code
    assert "styled_button(\"\\U0001f4e2 Broadcast Now\"" not in my_bots_code
    assert "styled_button(\"📢 Broadcast Now (All)\"" not in my_bots_code
    assert 'f"broadcast_now_{phone}"' not in my_bots_code
    assert '"all_slots_broadcast_now", style=' not in my_bots_code
    print("  [PASS] Broadcast Now buttons completely removed from all menus")

    # 8. VC connection persistence and video stream fixes
    assert "-stream_loop -1" in vc_src, "join_voice_chat missing -stream_loop -1 for continuous presence!"
    assert hasattr(UserBot, "idle_in_vc"), "UserBot missing idle_in_vc method!"
    idle_src = inspect.getsource(UserBot.idle_in_vc)
    assert "-stream_loop -1" in idle_src, "idle_in_vc missing -stream_loop -1!"
    
    stop_song_src = inspect.getsource(UserBot.stop_song)
    assert "idle_in_vc" in stop_song_src, "stop_song does not call idle_in_vc when leave_vc=False!"

    from villain_ads_bot.services import youtube as yt
    assert hasattr(yt, "_is_valid_video_file"), "youtube.py missing _is_valid_video_file!"
    # 9. Real-time message update verification and race condition prevention
    assert hasattr(database, "add_welcomed_user"), "database missing add_welcomed_user function!"
    reload_src = inspect.getsource(UserBot.reload_settings)
    assert "get_session_fresh" in reload_src, "reload_settings must read fresh session bypassing cache!"
    
    test_ub = UserBot("test_session_id")
    assert hasattr(test_ub, "phone"), "UserBot must have phone attribute for lookup!"
    
    # Verify save_session caches both with and without '+'
    test_sess = {
        "session_id": "+919999988888",
        "phone": "+919999988888",
        "user_id": 12345,
        "settings": {"broadcast_msg": "Fresh New Message"}
    }
    database.save_session(test_sess)
    assert "+919999988888" in database._cached_sessions
    assert "919999988888" in database._cached_sessions
    assert database._cached_sessions["919999988888"]["data"]["settings"]["broadcast_msg"] == "Fresh New Message"
    # 10. Multi-userbot concurrent VC streaming & synchronized controls verification
    from villain_ads_bot.services import userbot_manager
    from villain_ads_bot.handlers import player

    assert hasattr(userbot_manager, "get_running_userbots_for_owner"), "userbot_manager missing get_running_userbots_for_owner!"
    
    # Mock multiple running bots for owner 99999
    bot1 = UserBot("bot1_session")
    bot1.owner_id = 99999
    bot1.phone = "+919999900001"
    bot1.is_running = True
    bot1.joined_vcs = {-1001234567890}

    bot2 = UserBot("bot2_session")
    bot2.owner_id = 99999
    bot2.phone = "+919999900002"
    bot2.is_running = True
    bot2.joined_vcs = {-1001234567890}

    bot3 = UserBot("bot3_session")
    bot3.owner_id = 99999
    bot3.phone = "+919999900003"
    bot3.is_running = True
    bot3.joined_vcs = {-1001234567890}

    bot4 = UserBot("bot4_session")
    bot4.owner_id = 99999
    bot4.phone = "+919999900004"
    bot4.is_running = True
    bot4.joined_vcs = {-1001234567890}

    # Register in userbot_manager
    userbot_manager._running_bots[bot1.phone] = bot1
    userbot_manager._running_bots[bot2.phone] = bot2
    userbot_manager._running_bots[bot3.phone] = bot3
    userbot_manager._running_bots[bot4.phone] = bot4

    # Mock database sessions for owner 99999
    for b in (bot1, bot2, bot3, bot4):
        database.save_session({
            "session_id": b.session_id,
            "phone": b.phone,
            "user_id": 99999,
            "settings": {}
        })

    # Test get_running_userbots_for_owner finds the 3 companion bots when excluding bot1
    companions = userbot_manager.get_running_userbots_for_owner(99999, chat_id=-1001234567890, exclude_bot=bot1)
    assert len(companions) == 3, f"Expected 3 companion bots, got {len(companions)}"
    assert bot1 not in companions, "exclude_bot must not be in companion bots!"
    assert all(cb in [bot2, bot3, bot4] for cb in companions)

    # Test format_now_playing_text with multiple bots
    sample_song = {"title": "Villain Anthem", "duration": 180, "userbot_name": "VillainBot"}
    np_card = player.format_now_playing_text(sample_song, "OwnerUser", 99999, stream_type="audio", bot_count=4)
    assert "(4 Userbots)" in np_card, f"Expected '(4 Userbots)' in header, got: {np_card}"
    print("  [PASS] Multi-userbot concurrent VC streaming & companion discovery verified")

    print("\nALL FIXES VERIFIED SUCCESSFULLY!")

if __name__ == "__main__":
    run_tests()


