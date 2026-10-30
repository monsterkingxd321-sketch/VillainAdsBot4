import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime
from telethon.tl.types import (
    Message,
    MessageMediaDocument,
    Document,
    DocumentAttributeAudio,
    DocumentAttributeVideo,
    DocumentAttributeFilename
)
from villain_ads_bot.services.userbot import extract_media_info as ub_extract_media_info
from villain_ads_bot.handlers.my_bots import extract_media_info as mb_extract_media_info


def test_extract_media_info():
    now = datetime.datetime.now()

    # 1. Voice Note Recording
    voice_doc = Document(
        id=101,
        access_hash=101,
        file_reference=b'',
        date=now,
        mime_type='audio/ogg',
        size=5000,
        dc_id=1,
        attributes=[DocumentAttributeAudio(duration=25, voice=True)]
    )
    msg_voice = Message(id=1, peer_id=None, date=now, message='', media=MessageMediaDocument(document=voice_doc))
    
    media, title, duration = ub_extract_media_info(msg_voice)
    assert media is not None, "Voice note media was not extracted!"
    assert "Voice" in title, f"Expected Voice in title, got {title}"
    assert duration == 25, f"Expected duration 25, got {duration}"

    # 2. Video File
    video_doc = Document(
        id=102,
        access_hash=102,
        file_reference=b'',
        date=now,
        mime_type='video/mp4',
        size=50000,
        dc_id=1,
        attributes=[
            DocumentAttributeVideo(duration=80.5, w=1280, h=720),
            DocumentAttributeFilename(file_name='funny_video.mp4')
        ]
    )
    msg_video = Message(id=2, peer_id=None, date=now, message='', media=MessageMediaDocument(document=video_doc))

    media, title, duration = ub_extract_media_info(msg_video)
    assert media is not None, "Video media was not extracted!"
    assert title == "funny_video.mp4", f"Expected filename in title, got {title}"
    assert duration == 80, f"Expected integer duration 80, got {duration}"

    # 3. Video Note (Round Message Recording)
    vnote_doc = Document(
        id=103,
        access_hash=103,
        file_reference=b'',
        date=now,
        mime_type='video/mp4',
        size=15000,
        dc_id=1,
        attributes=[DocumentAttributeVideo(duration=15, w=360, h=360, round_message=True)]
    )
    msg_vnote = Message(id=3, peer_id=None, date=now, message='', media=MessageMediaDocument(document=vnote_doc))

    media, title, duration = ub_extract_media_info(msg_vnote)
    assert media is not None, "Video note media was not extracted!"
    assert "Video" in title, f"Expected Video in title, got {title}"
    assert duration == 15, f"Expected duration 15, got {duration}"

    # 4. Audio Song
    audio_doc = Document(
        id=104,
        access_hash=104,
        file_reference=b'',
        date=now,
        mime_type='audio/mpeg',
        size=4000000,
        dc_id=1,
        attributes=[DocumentAttributeAudio(duration=210, performer='Alan Walker', title='Faded')]
    )
    msg_audio = Message(id=4, peer_id=None, date=now, message='', media=MessageMediaDocument(document=audio_doc))

    media, title, duration = ub_extract_media_info(msg_audio)
    assert media is not None, "Audio media was not extracted!"
    assert "Alan Walker" in title and "Faded" in title, f"Expected song title, got {title}"
    assert duration == 210, f"Expected duration 210, got {duration}"

    # 5. Document with media extension (e.g. MKV)
    mkv_doc = Document(
        id=105,
        access_hash=105,
        file_reference=b'',
        date=now,
        mime_type='application/octet-stream',
        size=10000000,
        dc_id=1,
        attributes=[
            DocumentAttributeFilename(file_name='movie.mkv'),
            DocumentAttributeVideo(duration=120, w=1920, h=1080)
        ]
    )
    msg_mkv = Message(id=5, peer_id=None, date=now, message='', media=MessageMediaDocument(document=mkv_doc))

    media, title, duration = ub_extract_media_info(msg_mkv)
    assert media is not None, "MKV document was not extracted!"
    assert title == "movie.mkv", f"Expected movie.mkv, got {title}"
    assert duration == 120, f"Expected duration 120, got {duration}"

    print("ALL MEDIA EXTRACTION TESTS PASSED [OK]")


def test_code_checks():
    # Verify player.py fetches reply before deleting event
    with open("villain_ads_bot/handlers/player.py", "r", encoding="utf-8") as f:
        pl_src = f.read()

    # event.get_reply_message() should be called before await event.delete()
    get_reply_pos = pl_src.find("reply_msg = await event.get_reply_message()")
    delete_event_pos = pl_src.find("await event.delete()")
    assert get_reply_pos != -1, "reply_msg = await event.get_reply_message() missing in player.py"
    assert delete_event_pos != -1, "await event.delete() missing in player.py"
    assert get_reply_pos < delete_event_pos, "event.get_reply_message() MUST come before event.delete() in player.py!"

    # Verify private chat routing
    assert "if event.is_private:" in pl_src, "Private chat routing missing in player.py!"
    assert "No Active Voice Chat Found" in pl_src, "Friendly no active VC message missing in player.py!"

    # Verify userbot.py private chat routing and video_stopped=True
    with open("villain_ads_bot/services/userbot.py", "r", encoding="utf-8") as f:
        ub_src = f.read()

    assert "if event.is_private:" in ub_src, "Private chat routing missing in userbot.py!"
    assert "video_stopped=(False if is_video_streaming else True)" in ub_src, "video_stopped must be True when not streaming video!"

    print("ALL CODE STRUCTURE TESTS PASSED [OK]")


if __name__ == "__main__":
    test_extract_media_info()
    test_code_checks()
    print("ALL TESTS IN test_reply_playback.py PASSED!")

