import os
import asyncio
import logging
import time
import urllib.request
import urllib.error
import json
import random
import sys
from typing import Optional, Set

# Global semaphore to prevent Railway OOM crashes when multiple bots refresh groups at the exact same time
_dialog_fetch_semaphore = asyncio.Semaphore(2)

# Auto-detect and append NodeJS to PATH on Windows if missing
if sys.platform == "win32":
    paths_to_add = [
        r"C:\Program Files\nodejs",
        r"C:\Program Files (x86)\nodejs",
        os.path.expandvars(r"%APPDATA%\npm")
    ]
    current_path = os.environ.get("PATH", "")
    for p in paths_to_add:
        if os.path.exists(p) and p not in current_path:
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]

from telethon import TelegramClient, events, functions, types
from telethon.errors import (
    PeerFloodError,
    FloodWaitError,
    UserBannedInChannelError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    SlowModeWaitError,
    ChannelInvalidError,
    ChatIdInvalidError,
    UserNotParticipantError,
    UserDeactivatedError,
    AuthKeyUnregisteredError
)
try:
    from telethon.errors import SessionRevokedError
except ImportError:
    SessionRevokedError = None
try:
    from telethon.errors import SessionExpiredError
except ImportError:
    SessionExpiredError = None
try:
    from telethon.errors import MessageEntityParsingError
except ImportError:
    class MessageEntityParsingError(Exception):
        """Fallback when the telethon version does not know this error."""
        pass
from villain_ads_bot import config
from villain_ads_bot.core import antispam
from villain_ads_bot.core import antispam_guard
from villain_ads_bot.core import session_lock
from villain_ads_bot.data import database
from villain_ads_bot.core import utils

logger = logging.getLogger(__name__)

# --- Pytgcalls Telethon Update AttributeError Monkey-Patch ---
try:
    from pytgcalls.mtproto.telethon_client import TelethonClient
    from telethon.events import Raw
    
    original_init = TelethonClient.__init__
    
    def patched_init(self, cache_duration, client):
        original_add = client.add_event_handler
        
        def patched_add(callback, event=None):
            if isinstance(event, Raw) or (event and event.__class__.__name__ == 'Raw'):
                original_callback = callback
                async def wrapped_callback(evt):
                    try:
                        await original_callback(evt)
                    except (AttributeError, ValueError) as err:
                        err_str = str(err)
                        if "UpdateGroupCall" in err_str or "chat_id" in err_str:
                            pass
                        else:
                            raise err
                    except Exception as e:
                        err_str = str(e)
                        if "UpdateGroupCall" in err_str or "chat_id" in err_str:
                            pass
                        else:
                            raise e
                callback = wrapped_callback
            return original_add(callback, event)
            
        client.add_event_handler = patched_add
        try:
            original_init(self, cache_duration, client)
        finally:
            client.add_event_handler = original_add
            
    TelethonClient.__init__ = patched_init
    logger.info("Successfully applied PyTgCalls TelethonClient monkey-patch.")
except Exception as patch_err:
    logger.error(f"Failed to apply PyTgCalls monkey-patch: {patch_err}")

import asyncio
import os
import re
from typing import Union
import yt_dlp
try:
    from pyrogram.enums import MessageEntityType
    from pyrogram.types import Message
except ImportError:
    MessageEntityType = None
    Message = None
try:
    from youtubesearchpython import VideosSearch, Playlist
except ImportError:  # pragma: no cover - optional legacy search backend
    VideosSearch = None
    Playlist = None
import aiohttp
try:
    from pytgcalls import PyTgCalls
    from pytgcalls.types import MediaStream, VideoQuality
    PYTGCALLS_AVAILABLE = True
except Exception as e:
    PyTgCalls = None
    MediaStream = None
    VideoQuality = None
    PYTGCALLS_AVAILABLE = False
    logger.warning(f"PyTgCalls is not available: {e}")

# The placeholder that ships with a new session. It is NOT a real message.
DEFAULT_BROADCAST_MESSAGE = "Hello! This is an automated broadcast message."

from villain_ads_bot.services.youtube import (
    YouTube,
    YouTubeAPI,
    download_song,
    download_video,
    time_to_seconds,
    DOWNLOAD_DIR,
    API_URL,
    API_KEY,
)


# YouTubeAPI and YouTube instance are imported from youtube.py



async def download_media(query: str, download_type: str = "audio") -> tuple:
    """
    Downloads audio/video for voice call using YouTube API search & Meow/Yuki API stream.
    Returns (file_path: str, title: str, duration: int, thumbnail: str)
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    try:
        title = "YouTube Video"
        vidid = None
        duration_sec = 0
        thumb = None
        query_str = (query or "").strip()
        
        # 0. Check if query is a direct YouTube URL or 11-char Video ID
        if "youtube.com" in query_str or "youtu.be" in query_str or re.match(r"^[a-zA-Z0-9_-]{11}$", query_str):
            url_match = re.search(r"(?:v=|\/|vi\/|youtu\.be\/|\/v\/|\/embed\/|\/shorts\/|^)([a-zA-Z0-9_-]{11})", query_str)
            if url_match:
                vidid = url_match.group(1)

        # Fetch metadata if vidid was extracted from URL directly
        if vidid:
            try:
                track_details, _ = await YouTube.track(f"https://www.youtube.com/watch?v={vidid}")
                if track_details:
                    title = track_details.get("title") or title
                    thumb = track_details.get("thumb")
                    dur_min = track_details.get("duration_min")
                    duration_sec = int(time_to_seconds(dur_min)) if dur_min else 0
            except Exception:
                pass

        # 1. Search YouTube using py_yt if vidid not yet resolved
        if not vidid:
            try:
                track_details, vidid = await YouTube.track(query_str)
                if vidid:
                    title = track_details.get("title") or title
                    thumb = track_details.get("thumb")
                    dur_min = track_details.get("duration_min")
                    duration_sec = int(time_to_seconds(dur_min)) if dur_min else 0
            except Exception as yt_err:
                logger.warning(f"YouTube.track extraction error: {yt_err}")

        # Fallback search with yt_dlp if py_yt didn't find video id
        if not vidid:
            def _search_sync():
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': 'in_playlist',
                    'skip_download': True,
                    'geo_bypass': True,
                    'nocheckcertificate': True,
                    'extractor_args': {'youtube': {'player_client': ['mweb', 'tv_embedded', 'android']}},
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    search_query = query_str if query_str.startswith("http") else f"ytsearch1:{query_str}"
                    try:
                        res = ydl.extract_info(search_query, download=False)
                        if not res:
                            return None
                        if 'entries' in res:
                            entries = res.get('entries') or []
                            if not entries or not entries[0]:
                                return None
                            return entries[0]
                        return res
                    except Exception as e:
                        logger.error(f"yt-dlp search extraction failed: {e}")
                        return None

            loop = asyncio.get_running_loop()
            entry = await loop.run_in_executor(None, _search_sync)
            if entry:
                title = entry.get("title") or title
                vidid = entry.get("id")
                duration_sec = int(entry.get("duration") or 0)
                thumb = entry.get("thumbnail") or f"https://img.youtube.com/vi/{vidid}/0.jpg"

        if not vidid:
            return None, None, None, None

        thumb_url = thumb or f"https://img.youtube.com/vi/{vidid}/0.jpg"
        youtube_url = f"https://www.youtube.com/watch?v={vidid}"

        # Ensure thumbnail is downloaded locally
        local_thumb_path = os.path.join(DOWNLOAD_DIR, f"{vidid}_thumb.jpg")
        if not os.path.exists(local_thumb_path) or os.path.getsize(local_thumb_path) < 1000:
            thumb_candidates = [
                thumb_url,
                f"https://img.youtube.com/vi/{vidid}/hqdefault.jpg",
                f"https://img.youtube.com/vi/{vidid}/0.jpg"
            ]
            for t_url in thumb_candidates:
                if not t_url:
                    continue
                try:
                    import urllib.request
                    urllib.request.urlretrieve(t_url, local_thumb_path)
                    if os.path.exists(local_thumb_path) and os.path.getsize(local_thumb_path) > 1000:
                        break
                except Exception:
                    pass
        
        final_thumb = local_thumb_path if (os.path.exists(local_thumb_path) and os.path.getsize(local_thumb_path) > 1000) else thumb_url

        def _has_valid_video_stream(fpath: str) -> bool:
            try:
                import subprocess
                cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type,width,height", "-of", "csv=s=x:p=0", fpath]
                res = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5).decode().strip()
                return ("video" in res or "x" in res) and len(res) > 0
            except Exception:
                return True

        # 2. Check if file already exists in downloads
        prefix = "audio" if download_type == "audio" else "video"
        ext = "mp3" if download_type == "audio" else "mp4"
        cached_file = os.path.join(DOWNLOAD_DIR, f"{vidid}_{prefix}.{ext}")
        if os.path.exists(cached_file) and os.path.getsize(cached_file) > 10000:
            if download_type != "video" or _has_valid_video_stream(cached_file):
                logger.info(f"Using cached file: {cached_file}")
                return cached_file, title, duration_sec, final_thumb

        # The youtube module caches as "<video_id>.mp3" / "<video_id>.mp4"
        plain_cached = os.path.join(DOWNLOAD_DIR, f"{vidid}.{ext}")
        if os.path.exists(plain_cached) and os.path.getsize(plain_cached) > 10000:
            if download_type != "video" or _has_valid_video_stream(plain_cached):
                logger.info(f"Using cached file: {plain_cached}")
                return plain_cached, title, duration_sec, final_thumb

        # Also check any file starting with vidid_prefix
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(f"{vidid}_{prefix}") and not fname.endswith(".jpg"):
                full_p = os.path.join(DOWNLOAD_DIR, fname)
                if os.path.getsize(full_p) > 10000:
                    if download_type != "video" or _has_valid_video_stream(full_p):
                        logger.info(f"Using cached file matching prefix: {full_p}")
                        return full_p, title, duration_sec, final_thumb

        # 3. Primary Fast Direct Stream via Yuki API (with gateway failover)
        logger.info(f"Downloading {download_type} directly via Yuki API for: {title} ({vidid})...")
        downloaded_file = None
        try:
            from villain_ads_bot.core import music as _music
            downloaded_file = await _music.download_with_failover(download_type, youtube_url)
        except Exception as api_err:
            logger.warning(f"Yuki API primary download error: {api_err}")

        # Fallback Strategy: Fast local download via yt-dlp if API stream was unavailable
        if not downloaded_file or not os.path.exists(downloaded_file) or os.path.getsize(downloaded_file) < 5000:
            logger.info(f"Yuki API stream missed, falling back to yt-dlp engine for: {title} ({vidid})...")
            def _dl_sync():
                # Strategy 1: mweb + tv_embedded + android
                try:
                    if download_type == "audio":
                        ydl_opts = {
                            'format': 'bestaudio/best',
                            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{vidid}_audio.%(ext)s"),
                            'quiet': True,
                            'no_warnings': True,
                            'nocheckcertificate': True,
                            'geo_bypass': True,
                            'extractor_args': {'youtube': {'player_client': ['mweb', 'tv_embedded', 'android']}},
                        }
                    else:
                        ydl_opts = {
                            'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
                            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{vidid}_video.%(ext)s"),
                            'merge_output_format': 'mp4',
                            'quiet': True,
                            'no_warnings': True,
                            'nocheckcertificate': True,
                            'geo_bypass': True,
                            'extractor_args': {'youtube': {'player_client': ['mweb', 'tv_embedded', 'android']}},
                        }
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([youtube_url])
                            
                    for fname in os.listdir(DOWNLOAD_DIR):
                        if fname.startswith(f"{vidid}_{prefix}") and not fname.endswith(".jpg"):
                            full_p = os.path.join(DOWNLOAD_DIR, fname)
                            if os.path.getsize(full_p) > 5000:
                                return full_p
                except Exception as dl_ex:
                    logger.error(f"yt-dlp strategy 1 exception: {dl_ex}")
                
                # Strategy 2: android_creator + mweb + android fallback format ba/b
                try:
                    fb_opts = {
                        'format': 'ba/b' if download_type == "audio" else 'b[height<=720]/b',
                        'outtmpl': os.path.join(DOWNLOAD_DIR, f"{vidid}_{prefix}.%(ext)s"),
                        'quiet': True,
                        'no_warnings': True,
                        'nocheckcertificate': True,
                        'geo_bypass': True,
                        'extractor_args': {'youtube': {'player_client': ['android_creator', 'mweb', 'android']}},
                    }
                    with yt_dlp.YoutubeDL(fb_opts) as ydl:
                        ydl.download([youtube_url])
                    for fname in os.listdir(DOWNLOAD_DIR):
                        if fname.startswith(f"{vidid}_{prefix}") and not fname.endswith(".jpg"):
                            full_p = os.path.join(DOWNLOAD_DIR, fname)
                            if os.path.getsize(full_p) > 5000:
                                return full_p
                except Exception as fb_err:
                    logger.error(f"yt-dlp strategy 2 exception: {fb_err}")

                # Strategy 3: Standard yt-dlp without player_client restrictions
                try:
                    std_opts = {
                        'format': 'bestaudio/best' if download_type == "audio" else 'bestvideo[height<=720]+bestaudio/best',
                        'outtmpl': os.path.join(DOWNLOAD_DIR, f"{vidid}_{prefix}.%(ext)s"),
                        'quiet': True,
                        'no_warnings': True,
                        'nocheckcertificate': True,
                        'geo_bypass': True,
                    }
                    with yt_dlp.YoutubeDL(std_opts) as ydl:
                        ydl.download([youtube_url])
                    for fname in os.listdir(DOWNLOAD_DIR):
                        if fname.startswith(f"{vidid}_{prefix}") and not fname.endswith(".jpg"):
                            full_p = os.path.join(DOWNLOAD_DIR, fname)
                            if os.path.getsize(full_p) > 5000:
                                return full_p
                except Exception as std_err:
                    logger.error(f"yt-dlp strategy 3 exception: {std_err}")

                return None

            loop = asyncio.get_running_loop()
            downloaded_file = await loop.run_in_executor(None, _dl_sync)

        if downloaded_file and os.path.exists(downloaded_file) and os.path.getsize(downloaded_file) > 5000:
            logger.info(f"Successfully obtained {download_type} file: {downloaded_file}")
            return downloaded_file, title, duration_sec, final_thumb
        else:
            logger.error(f"All download methods failed for query: {query_str} (vidid: {vidid}).")
            return None, None, None, None
    except Exception as e:
        logger.error(f"Download media exception: {e}")
        return None, None, None, None
    except Exception as e:
        logger.error(f"Download media exception: {e}")
        return None, None, None, None


async def call_gpt_api(api_key: str, user_message: str) -> str:
    """
    Calls OpenAI GPT-3.5 API using standard urllib to prevent external library issues.
    """
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    data = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are a helpful automated assistant."},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": 150
    }
    
    def _send():
        req = urllib.request.Request(
            url, 
            data=json.dumps(data).encode("utf-8"), 
            headers=headers, 
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                res = json.loads(response.read().decode("utf-8"))
                return res["choices"][0]["message"]["content"].strip()
        except Exception as err:
            logger.error(f"GPT API request error: {err}")
            return "⚠️ GPT Assistant temporarily unavailable."

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send)

async def join_vc(client: TelegramClient, peer_id: int) -> bool:
    """
    Attempts to join the active voice call of a group/channel using native MTProto JoinGroupCallRequest.
    """
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetFullChatRequest
        from telethon.tl.functions.phone import JoinGroupCallRequest
        from telethon.tl.types import InputGroupCall, DataJSON, Channel, GroupCallDiscarded
        import random
        
        entity = await client.get_entity(peer_id)
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
        else:
            full = await client(GetFullChatRequest(entity.id))
            
        group_call = full.full_chat.call
        if group_call and not isinstance(group_call, GroupCallDiscarded):
            for attempt in range(5):
                random_ssrc = random.randint(100000, 999999999)
                try:
                    params_json = f'{{"transport":{{"webrtc":true}},"muted":true,"video_stopped":true,"ssrc":{random_ssrc}}}'
                    await client(JoinGroupCallRequest(
                        call=InputGroupCall(
                            id=group_call.id,
                            access_hash=group_call.access_hash
                        ),
                        join_as=await client.get_input_entity('me'),
                        params=DataJSON(data=params_json),
                        muted=True
                    ))
                    logger.info(f"Successfully joined VC for peer {peer_id} with SSRC {random_ssrc}")
                    return True
                except Exception as j_err:
                    err_s = str(j_err).lower()
                    if "ssrc" in err_s or "duplicate" in err_s:
                        await asyncio.sleep(0.5)
                        continue
                    elif "already" in err_s or "participant" in err_s:
                        return True
                    else:
                        logger.warning(f"Native join VC error on attempt {attempt}: {j_err}")
                        break
        else:
            logger.debug(f"No active VC found for peer {peer_id}")
    except Exception as e:
        logger.warning(f"Could not join VC for peer {peer_id}: {e}")
    return False


async def get_peer_from_link(client: TelegramClient, link: str):
    """
    Resolves a group/channel link or username to a chat entity,
    joining the channel/group if the userbot is not already a member.
    Robustly supports private invite links (+hash, joinchat/hash), usernames, and numeric IDs.
    """
    if not link:
        return None
        
    link_clean = str(link).strip().strip('\'"`()[]{}<> \t\n\r')
    if not link_clean:
        return None
        
    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.types import ChatInviteAlready, ChatInvite, Channel, Chat
    from telethon.errors import (
        UserAlreadyParticipantError,
        InviteRequestSentError,
        InviteHashExpiredError,
        InviteHashInvalidError
    )
    
    # 1. Check if link is a numeric ID (e.g. -100123456789 or 123456789)
    if link_clean.lstrip("-").isdigit():
        try:
            c_id = int(link_clean)
            try:
                return await client.get_entity(c_id)
            except Exception:
                async for dialog in client.iter_dialogs(limit=500):
                    if dialog.id == c_id:
                        return dialog.entity
        except Exception:
            pass

    # 2. Extract private invite hash if link is an invite link
    # Matches t.me/+hash, t.me/joinchat/hash, telegram.me/+hash, etc.
    hash_match = re.search(r'(?:t\.me|telegram\.(?:me|dog|org))/(?:joinchat/|\+)([a-zA-Z0-9_-]+)', link_clean, re.IGNORECASE)
    hash_val = None
    if hash_match:
        hash_val = hash_match.group(1).strip()
    elif link_clean.startswith("+"):
        hash_val = link_clean[1:].split('/')[0].split('?')[0].strip()
    elif "joinchat/" in link_clean:
        hash_val = link_clean.split("joinchat/")[-1].split('/')[0].split('?')[0].strip()
    elif "+" in link_clean and "t.me" in link_clean:
        hash_val = link_clean.split("+")[-1].split('/')[0].split('?')[0].strip()

    if hash_val:
        invite_info = None
        # First, check the invite
        try:
            invite_info = await client(CheckChatInviteRequest(hash_val))
            if isinstance(invite_info, ChatInviteAlready):
                return invite_info.chat
        except UserAlreadyParticipantError:
            pass
        except Exception as e_chk:
            logger.warning(f"CheckChatInviteRequest error for hash {hash_val}: {e_chk}")

        # If not already returned, try importing/joining the invite
        try:
            updates = await client(ImportChatInviteRequest(hash_val))
            if updates and hasattr(updates, 'chats') and updates.chats:
                return updates.chats[0]
        except UserAlreadyParticipantError:
            logger.info(f"User is already a participant of {hash_val}")
        except InviteRequestSentError:
            logger.info(f"Join request sent for {hash_val}")
        except Exception as e_imp:
            logger.warning(f"ImportChatInviteRequest error for hash {hash_val}: {e_imp}")

        # Re-check if invite now returns ChatInviteAlready
        try:
            recheck = await client(CheckChatInviteRequest(hash_val))
            if isinstance(recheck, ChatInviteAlready):
                return recheck.chat
        except Exception:
            pass

        # If we have title from invite_info, search dialogs
        target_title = getattr(invite_info, "title", None) if invite_info else None
        if target_title:
            target_norm = target_title.strip().lower()
            async for dialog in client.iter_dialogs(limit=500):
                d_name = (dialog.name or getattr(dialog.entity, "title", "") or "").strip().lower()
                if d_name and (d_name == target_norm or target_norm in d_name):
                    return dialog.entity

        # Try client.get_entity as fallback
        try:
            return await client.get_entity(link_clean)
        except Exception:
            pass

        return None

    # 3. Public username or link (e.g. @groupname or https://t.me/groupname)
    username_match = re.search(r'(?:t\.me|telegram\.(?:me|dog|org))/([a-zA-Z0-9_]+)', link_clean, re.IGNORECASE)
    if username_match:
        username = username_match.group(1).strip()
    else:
        username = link_clean.replace('@', '').strip()

    try:
        # Join public channel if not member
        try:
            await client(JoinChannelRequest(username))
        except Exception:
            pass
        return await client.get_entity(username)
    except Exception as e:
        logger.warning(f"Failed to get entity for public target {username}: {e}")

    return None

async def join_vc_by_link(client: TelegramClient, link: str) -> tuple:
    """
    Joins a channel/group from a link and joins its active voice chat.
    Returns (success: bool, message: str)
    """
    try:
        entity = await get_peer_from_link(client, link)
        if not entity:
            return False, "Could not resolve or join the group/channel."
            
        # Try to join VC
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetFullChatRequest
        from telethon.tl.functions.phone import JoinGroupCallRequest
        from telethon.tl.types import InputGroupCall, DataJSON, Channel, GroupCallDiscarded
        
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
        else:
            full = await client(GetFullChatRequest(entity.id))
            
        group_call = full.full_chat.call
        if group_call and not isinstance(group_call, GroupCallDiscarded):
            import random
            max_retries = 5
            last_err = None
            for attempt in range(max_retries):
                random_ssrc = random.randint(100000, 999999999)
                try:
                    params_json = f'{{"transport":{{"webrtc":true}},"muted":true,"video_stopped":true,"ssrc":{random_ssrc}}}'
                    await client(JoinGroupCallRequest(
                        call=InputGroupCall(
                            id=group_call.id,
                            access_hash=group_call.access_hash
                        ),
                        join_as=await client.get_input_entity('me'),
                        params=DataJSON(data=params_json),
                        muted=True
                    ))
                    logger.info(f"Successfully joined VC for peer {entity.id} with SSRC {random_ssrc}")
                    chat_title = getattr(entity, 'title', 'Group')
                    return True, f"Successfully joined VC of {chat_title}!", {"group_call": group_call, "ssrc": random_ssrc, "chat_id": entity.id}
                except Exception as join_err:
                    err_str = str(join_err).lower()
                    last_err = join_err
                    if "ssrc" in err_str or "duplicate" in err_str:
                        logger.warning(f"SSRC collision on attempt {attempt + 1}, retrying with new SSRC... Error: {join_err}")
                        await asyncio.sleep(0.5)
                        continue
                    else:
                        break
            raise last_err if last_err else Exception("Failed to join call")
        else:
            chat_title = getattr(entity, 'title', 'Group')
            return False, f"No active voice chat (VC) found in {chat_title}.", None
    except Exception as e:
        logger.warning(f"Error joining VC by link {link}: {e}")
        return False, f"Error: {e}", None


async def generate_silence() -> str:
    """
    Generates a pure-Python silent audio WAV file in the downloads folder.
    Requires zero external FFmpeg commands and never fails.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, "silence.wav")
    if os.path.exists(file_path) and os.path.getsize(file_path) > 1000:
        return file_path
    try:
        import wave
        with wave.open(file_path, "wb") as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(48000)
            f.writeframes(b'\x00' * (48000 * 2 * 3))
        logger.info(f"Generated silence.wav successfully at {file_path}")
    except Exception as e:
        logger.error(f"Failed to generate silence.wav: {e}")
    return file_path


def extract_media_info(msg):
    """
    Extracts file path, title, and duration from a message containing audio/voice/video.
    """
    media = getattr(msg, "audio", None) or getattr(msg, "voice", None) or getattr(msg, "video", None) or getattr(msg, "gif", None)
    if not media and getattr(msg, "document", None):
        mime = getattr(msg.document, "mime_type", "")
        if mime.startswith("audio/") or mime.startswith("video/"):
            media = msg.document
            
    if not media:
        return None, None, 30
        
    title = "Uploaded Media"
    duration = 30
    
    # Extract title
    if getattr(msg, "audio", None):
        title = getattr(msg.audio, "title", None) or getattr(msg.audio, "file_name", None) or "Audio File"
    elif getattr(msg, "voice", None):
        title = "Voice Note"
    elif getattr(msg, "video", None):
        title = getattr(msg.video, "file_name", None) or "Video File"
    elif getattr(msg, "document", None):
        title = getattr(msg.document, "file_name", None) or "Document Media"
        
    # Extract duration
    for attr in getattr(media, "attributes", []):
        if hasattr(attr, "duration"):
            duration = attr.duration
            break
            
    return media, title, duration


async def get_group_call_info(client: TelegramClient, link_or_id: str):

    """
    Resolves link/ID/username to the Telegram entity and its active call details.
    """
    entity = await get_peer_from_link(client, link_or_id)
    if not entity:
        return None, None
        
    from telethon.tl.functions.channels import GetFullChannelRequest
    from telethon.tl.functions.messages import GetFullChatRequest
    from telethon.tl.types import Channel, GroupCallDiscarded
    
    try:
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
        else:
            full = await client(GetFullChatRequest(entity.id))
            
        group_call = full.full_chat.call
        if group_call and not isinstance(group_call, GroupCallDiscarded):
            return entity, group_call
    except Exception as e:
        logger.error(f"Error getting group call info for {link_or_id}: {e}")
    return entity, None



def _flood_wait_seconds(exc) -> int:
    """
    Returns the number of seconds of a flood wait exception (0 if it is not
    a flood wait). Used to let the caller wait instead of silently failing.
    """
    for attr in ("seconds", "timeout"):
        value = getattr(exc, attr, None)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    text = str(exc).lower()
    if "floodwait" in text or "flood wait" in text or "wait of" in text:
        import re
        match = re.search(r"wait of (\d+) second", text)
        if match:
            return int(match.group(1))
    return 0


async def join_channel_single(client: TelegramClient, ch: str) -> bool:
    """
    Attempts to join a single channel or group by invite link or username.
    Returns True if successfully joined, already in it, or join request was sent.

    A flood limit is NOT swallowed here - it is re-raised so the caller can
    wait for the required time and continue automatically.
    """
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest
    import re
    
    if not ch:
        return False
        
    # Strip HTML tags, quotes, angles and leading/trailing whitespace
    ch_clean = re.sub(r'<[^>]+>', '', str(ch)).strip()
    ch_clean = ch_clean.strip('\'"`()[]{}<> \t\n\r')
    if not ch_clean:
        return False
        
    try:
        # Match joinchat or + hash links for t.me, telegram.me, telegram.dog, telegram.org
        join_hash_match = re.search(r'(?:t\.me|telegram\.(?:me|dog|org))/(?:joinchat/|\+)([a-zA-Z0-9_-]+)', ch_clean, re.IGNORECASE)
        
        if join_hash_match:
            hash_val = join_hash_match.group(1).strip()
            try:
                await client(ImportChatInviteRequest(hash_val))
            except Exception as e_invite:
                err_inv = str(e_invite).lower()
                if "already" in err_inv or "user_already_participant" in err_inv:
                    logger.info(f"Already participant of invite hash: {hash_val}")
                    return True
                if "request" in err_inv or "invite_request_sent" in err_inv or "inviterequestsent" in err_inv:
                    logger.info(f"Join request sent successfully for hash: {hash_val}")
                    return True
                raise e_invite
        else:
            # Extract username from public invite link if present
            username_match = re.search(r'(?:t\.me|telegram\.(?:me|dog|org))/([a-zA-Z0-9_]+)', ch_clean, re.IGNORECASE)
            if username_match:
                username = username_match.group(1).strip()
            else:
                username = ch_clean.replace('@', '').strip()
                
            try:
                await client(JoinChannelRequest(username))
            except Exception as e_join:
                err_j = str(e_join).lower()
                if "already" in err_j or "user_already_participant" in err_j:
                    logger.info(f"Already participant of channel: {username}")
                    return True
                if "request" in err_j or "invite_request_sent" in err_j or "inviterequestsent" in err_j:
                    logger.info(f"Join request sent successfully for: {username}")
                    return True
                raise e_join
            
        logger.info(f"Successfully joined channel/group: {ch_clean}")
        return True
    except Exception as e:
        # ── Flood limit: let the caller handle the wait ──
        if _flood_wait_seconds(e):
            raise
        err_msg = str(e).lower()
        if "already" in err_msg or "already participant" in err_msg or "user_already_participant" in err_msg:
            logger.debug(f"Already participant of: {ch_clean}")
            return True
        if "request" in err_msg or "invite_request_sent" in err_msg or "inviterequestsent" in err_msg:
            logger.info(f"Join request sent for: {ch_clean}")
            return True
        logger.warning(f"Failed to join channel {ch_clean}: {e}")
        return False


async def leave_chat_single(client: TelegramClient, ch: str) -> bool:
    """
    Attempts to leave a channel or group by link, username, or ID.
    """
    from telethon.tl.functions.channels import LeaveChannelRequest
    from telethon.tl.functions.messages import DeleteChatUserRequest
    import re
    if not ch:
        return False
    ch_clean = re.sub(r'<[^>]+>', '', str(ch)).strip().strip('\'"`()[]{}<> \t\n\r')
    if not ch_clean:
        return False
    try:
        entity = await get_peer_from_link(client, ch_clean)
        if not entity:
            return False
            
        from telethon.tl.types import Channel
        if isinstance(entity, Channel):
            await client(LeaveChannelRequest(entity))
        else:
            await client(DeleteChatUserRequest(
                chat_id=entity.id,
                user_id=await client.get_input_entity('me')
            ))
        logger.info(f"Successfully left channel/group: {ch_clean}")
        return True
    except Exception as e:
        logger.warning(f"Failed to leave channel/group {ch_clean}: {e}")
        return False


async def force_join_channels(client: TelegramClient, channels: list, session_id: str = None):
    """
    Forcibly joins the userbot to a list of channels or invite links.
    Uses asyncio.gather with a semaphore for fast parallel joining.
    """
    # Load already joined list if session_id is provided
    sess_data = None
    already_joined = []
    if session_id:
        sess_data = database.get_session(session_id)
        if sess_data:
            already_joined = sess_data.get("joined_channels", [])

    # Filter out channels that are already joined or empty
    pending = []
    for ch in channels:
        ch_clean = ch.strip()
        if not ch_clean:
            continue
        if ch_clean in already_joined:
            logger.info(f"Skipping auto-join for already joined channel: {ch_clean}")
            continue
        pending.append(ch_clean)

    if not pending:
        return

    # Join pending channels sequentially with safe delays between joins (bypasses anti-spam)
    joined_now = []
    for ch_clean in pending:
        try:
            success = await join_channel_single(client, ch_clean)
        except Exception as join_exc:
            # A flood limit must not abort the whole auto-join list.
            seconds = _flood_wait_seconds(join_exc)
            if not seconds:
                logger.warning(f"Auto-join failed for {ch_clean}: {join_exc}")
                continue
            seconds = min(seconds, 900)
            logger.warning(f"Flood wait of {seconds}s during auto-join - waiting.")
            await asyncio.sleep(seconds)
            try:
                success = await join_channel_single(client, ch_clean)
            except Exception as retry_exc:
                logger.warning(f"Auto-join failed for {ch_clean}: {retry_exc}")
                success = False
        if success:
            joined_now.append(ch_clean)
        # Sleep 3-5 seconds between channel joins to prevent anti-spam triggers
        await asyncio.sleep(random.uniform(3.0, 5.0))

    # Save newly joined channels to MongoDB
    if sess_data and joined_now:
        sess_data = database.get_session(session_id)
        if sess_data:
            current_joined = sess_data.setdefault("joined_channels", [])
            for c in joined_now:
                if c not in current_joined:
                    current_joined.append(c)
            database.save_session(sess_data)

BRAND_NAME_MAX = 64
BRAND_BIO_MAX = 70


def _is_cloned_session(session_data: dict) -> bool:
    """Returns True when the session currently carries a cloned profile."""
    if not session_data:
        return False
    return bool(
        session_data.get("is_cloned")
        or session_data.get("cloned_name")
        or session_data.get("cloned_bio")
        or "original_first_name" in session_data
    )


def _strip_trailing_suffix(value: str, suffix: str) -> str:
    """Removes a trailing branding suffix from a value (if present)."""
    if not value:
        return ""
    if not suffix:
        return value.rstrip()
    v = value.rstrip()
    s = suffix.rstrip()
    if s and v.endswith(s):
        return v[: -len(s)].rstrip()
    return v


def _normalize_suffix(suffix: str) -> str:
    """Normalises a branding suffix (see utils.normalize_brand_suffix)."""
    return utils.normalize_brand_suffix(suffix)


def _fit_with_suffix(base: str, suffix: str, limit: int) -> str:
    """
    Joins base + suffix while making sure the SUFFIX is never truncated away.
    (Telegram allows 64 chars for first_name and 70 for the bio.)
    """
    base = (base or "").rstrip()
    suffix = suffix or ""
    if not suffix:
        return base[:limit]
    room = max(1, limit - len(suffix))
    return (base[:room].rstrip() + suffix)[:limit]


async def _revert_branding(client: TelegramClient, session_data: dict):
    """
    Removes a previously applied branding suffix by restoring the stored
    "pure" profile values. Only ever runs when we know the original values.
    """
    if not session_data:
        return False
    base_name = session_data.get("branding_base_name")
    base_bio = session_data.get("branding_base_bio")
    if base_name is None and base_bio is None:
        return False

    from telethon.tl.functions.account import UpdateProfileRequest
    from telethon.tl.functions.users import GetFullUserRequest

    try:
        full_user = await client(GetFullUserRequest('me'))
        user_me = full_user.users[0]
        current_name = (user_me.first_name or "").strip()
        current_bio = (full_user.full_user.about or "").strip()

        update_kwargs = {}
        if base_name is not None and base_name != current_name:
            update_kwargs["first_name"] = base_name[:BRAND_NAME_MAX]
        if base_bio is not None and base_bio != current_bio:
            update_kwargs["about"] = base_bio[:BRAND_BIO_MAX]

        if update_kwargs:
            await client(UpdateProfileRequest(**update_kwargs))
            logger.info(f"Branding reverted for {session_data.get('phone')}.")

        session_data["branding_applied_name_suffix"] = ""
        session_data["branding_applied_bio_suffix"] = ""
        database.save_session(session_data)
        return True
    except Exception as e:
        logger.warning(f"Could not revert branding: {e}")
        return False


async def apply_branding(client: TelegramClient, branding_username: str, session_data: dict):
    """
    Applies (or refreshes) the branding suffix on the userbot profile name/bio.

    Key guarantees:
      * The branding is applied to the LIVE Telegram profile immediately.
      * The "pure" (un-branded) name/bio is remembered so the suffix can be
        refreshed or removed later without ever stacking multiple suffixes.
      * Cloned profiles are never touched.
      * Bio-only branding works (the name is left untouched).
    """
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.tl.functions.account import UpdateProfileRequest

    try:
        global_settings = database.get_global_settings()
        branding_lock = global_settings.get("branding_lock", True)

        # 1. PROTECT CLONED AND CUSTOM PROFILES:
        #    When the branding lock is enabled (default) the branding is
        #    applied ON TOP of a cloned profile so a user can never remove it.
        if _is_cloned_session(session_data) and not branding_lock:
            logger.info(f"apply_branding: session {session_data.get('phone')} has an active cloned profile, skipping branding.")
            return

        brand_name_enabled = global_settings.get("branding_name_enabled", True)
        brand_bio_enabled = global_settings.get("branding_bio_enabled", True)

        brand_name_text = _normalize_suffix(global_settings.get("branding_name_text") or "")
        brand_bio_text = _normalize_suffix(global_settings.get("branding_bio_text") or "")
        brand_username = (branding_username or "").strip()

        fallback_suffix = f" via @{brand_username}" if brand_username else ""
        name_suffix = brand_name_text if brand_name_text else fallback_suffix
        bio_suffix = brand_bio_text if brand_bio_text else fallback_suffix

        has_name_branding = bool(brand_name_enabled and name_suffix)
        has_bio_branding = bool(brand_bio_enabled and bio_suffix)

        # 2. Nothing configured any more -> revert to the pure profile (if known).
        if not has_name_branding and not has_bio_branding:
            await _revert_branding(client, session_data)
            return

        full_user = await client(GetFullUserRequest('me'))
        user_me = full_user.users[0]
        full_profile = full_user.full_user

        current_tg_name = (user_me.first_name or "").strip()
        current_tg_bio = (full_profile.about or "").strip()

        prev_name_suffix = _normalize_suffix(session_data.get("branding_applied_name_suffix") or "")
        prev_bio_suffix = _normalize_suffix(session_data.get("branding_applied_bio_suffix") or "")
        stored_base_name = session_data.get("branding_base_name")
        stored_base_bio = session_data.get("branding_base_bio")

        # 3. Work out the "pure" base values (profile WITHOUT any branding).
        if stored_base_name and not _is_cloned_session(session_data):
            expected = stored_base_name + prev_name_suffix if prev_name_suffix else stored_base_name
            if current_tg_name == expected:
                base_name = stored_base_name
            else:
                # The user changed the name on Telegram -> use the new name as base.
                base_name = _strip_trailing_suffix(current_tg_name, prev_name_suffix)
                base_name = _strip_trailing_suffix(base_name, name_suffix)
        else:
            # A cloned (or never branded) profile: the live name is the base.
            base_name = _strip_trailing_suffix(current_tg_name, prev_name_suffix)
            base_name = _strip_trailing_suffix(base_name, name_suffix)

        if stored_base_bio is not None and not _is_cloned_session(session_data):
            expected_bio = (stored_base_bio or "") + prev_bio_suffix if prev_bio_suffix else (stored_base_bio or "")
            if current_tg_bio == expected_bio:
                base_bio = stored_base_bio or ""
            else:
                base_bio = _strip_trailing_suffix(current_tg_bio, prev_bio_suffix)
                base_bio = _strip_trailing_suffix(base_bio, bio_suffix)
        else:
            base_bio = _strip_trailing_suffix(current_tg_bio, prev_bio_suffix)
            base_bio = _strip_trailing_suffix(base_bio, bio_suffix)

        base_name = (base_name or "").strip()
        base_bio = (base_bio or "").strip()

        # 4. Build the final values (suffix always kept intact).
        new_first_name = _fit_with_suffix(base_name, name_suffix if has_name_branding else "", BRAND_NAME_MAX)
        
        # When admin sets a custom bio, set that EXACT bio completely across userbots (do not cut old bio)
        if has_bio_branding:
            if brand_bio_text:
                new_bio = brand_bio_text[:BRAND_BIO_MAX]
            else:
                new_bio = _fit_with_suffix(base_bio, bio_suffix, BRAND_BIO_MAX)
        else:
            new_bio = base_bio[:BRAND_BIO_MAX]

        update_kwargs = {}
        if new_first_name and new_first_name != current_tg_name:
            update_kwargs["first_name"] = new_first_name
        if new_bio != current_tg_bio:
            update_kwargs["about"] = new_bio

        if update_kwargs:
            try:
                await client(UpdateProfileRequest(**update_kwargs))
                logger.info(f"Branding applied for {user_me.id}: {list(update_kwargs.keys())}")
            except Exception as up_err:
                err_name = type(up_err).__name__
                # A FROZEN account can not be written to at all. Mark it so the
                # dashboard shows the truth instead of retrying for ever.
                if "Frozen" in err_name:
                    logger.warning(
                        f"Account {session_data.get('phone')} is FROZEN by Telegram - "
                        f"the branding was skipped."
                    )
                    try:
                        _sid = session_data.get("session_id") or session_data.get("phone")
                        _sess = database.get_session(_sid) if _sid else None
                        if _sess:
                            _sess["status"] = "unauthorized"
                            _sess["last_error"] = (
                                "Account frozen by Telegram (@SpamBot restriction). "
                                "It can not be used any more."
                            )
                            database.save_session(_sess)
                    except Exception:
                        pass
                    return
                logger.warning(f"Could not update branding on Telegram for {user_me.id}: {up_err}")
                utils.report_error(f"apply_branding.update:{session_data.get('phone')}", up_err)

        # 5. Persist the pure values + the applied suffixes so a later change
        #    can refresh / revert the branding without stacking suffixes.
        session_data["branding_base_name"] = base_name
        session_data["branding_base_bio"] = base_bio
        session_data["branding_applied_name_suffix"] = name_suffix if has_name_branding else ""
        session_data["branding_applied_bio_suffix"] = bio_suffix if has_bio_branding else ""
        # Legacy keys kept in sync for the restore flow.
        session_data["original_name"] = base_name
        session_data["original_bio"] = base_bio
        try:
            database.save_session(session_data)
        except Exception as save_err:
            logger.debug(f"Could not persist branding state: {save_err}")
    except Exception as e:
        logger.warning(f"Failed to apply branding: {e}")
        utils.report_error(f"apply_branding:{session_data.get('phone') if session_data else 'unknown'}", e)


async def restore_branding(client: TelegramClient, session_data: dict):
    """
    Restores the userbot profile's original (un-branded) name and bio.
    """
    if _is_cloned_session(session_data):
        logger.info(f"restore_branding: session {session_data.get('phone')} has an active cloned profile, skipping restore.")
        return

    from telethon.tl.functions.account import UpdateProfileRequest
    try:
        orig_name = session_data.get("branding_base_name")
        if orig_name is None:
            orig_name = session_data.get("original_name", "")
        orig_bio = session_data.get("branding_base_bio")
        if orig_bio is None:
            orig_bio = session_data.get("original_bio", "")
        if orig_name or orig_bio:
            await client(UpdateProfileRequest(
                first_name=(orig_name or "User")[:BRAND_NAME_MAX],
                about=(orig_bio or "")[:BRAND_BIO_MAX]
            ))
            session_data["branding_applied_name_suffix"] = ""
            session_data["branding_applied_bio_suffix"] = ""
            database.save_session(session_data)
            logger.info("Branding restored successfully.")
    except Exception as e:
        logger.error(f"Failed to restore branding: {e}")
        utils.report_error("restore_branding", e)


class UserBot:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.client: Optional[TelegramClient] = None
        self.me_id: Optional[int] = None
        self._name = ""
        self._username = ""
        self.is_running = False
        self.broadcast_task: Optional[asyncio.Task] = None
        self.watchdog_task: Optional[asyncio.Task] = None
        self.joined_vcs: Set[int] = set()
        self.tag_cooldown = {}
        # Timestamp of the last auto reply of THIS account (global limiter).
        self._last_reply_ts = 0.0
        self.vc_keepalive_task: Optional[asyncio.Task] = None
        self.current_vc_chat_id: Optional[int] = None
        self.current_vc_link: Optional[str] = None
        self.pytgcalls_client: Optional[object] = None
        self.is_muted = True
        self.bg_tasks: Set[asyncio.Task] = set()
        
        # Caching attributes to resolve performance bottlenecks
        self.settings = {}
        self.groups_cache = None
        self.groups_cache_time = 0.0
        # Only ONE broadcast round may run per account. Without this the
        # background loop and the manual "Broadcast Now" posted every message
        # twice and doubled the flood pressure.
        self._broadcast_lock = asyncio.Lock()

    @property
    def name(self) -> str:
        if self._name:
            return self._name
        sess = database.get_session(self.session_id)
        if sess and sess.get("name"):
            self._name = sess.get("name")
            return self._name
        return "UserBot"

    @name.setter
    def name(self, val: str):
        self._name = val

    @property
    def username(self) -> str:
        if self._username:
            return self._username
        sess = database.get_session(self.session_id)
        if sess and sess.get("username"):
            self._username = sess.get("username")
            return self._username
        return ""

    @username.setter
    def username(self, val: str):
        self._username = val

    def reload_settings(self):
        """
        Reloads userbot settings from MongoDB into memory and restarts the broadcast task.
        """
        sess_data = database.get_session(self.session_id)
        if sess_data:
            self.settings = sess_data.get("settings", {})
            logger.info(f"Reloaded in-memory settings for userbot {self.session_id}")
            
            # The broadcast loop re-reads the settings on every round, so it
            # does NOT have to be restarted. Restarting it cancelled a round
            # that was in flight and immediately started a new one -> every
            # group received the message TWICE.
            if self.is_running and self.broadcast_task is None:
                self.broadcast_task = asyncio.create_task(self.broadcast_loop())
                logger.info(f"Started the broadcast loop for userbot {self.session_id}.")

    async def _mark_unauthorized_and_cleanup(self, reason: str = "❌ Account session expired or requires re-login."):
        """
        Marks an unauthorized session safely in the database and stops it,
        WITHOUT deleting session files or credentials so user data is never lost.
        """
        logger.warning(f"Marking userbot session {self.session_id} as unauthorized: {reason}")
        self.is_running = False
        
        # 1. Disconnect client cleanly
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.debug(f"Error disconnecting client during auth-cleanup for {self.session_id}: {e}")
        
        # 2. Update status in MongoDB database (preserving session_bytes and session file)
        sess_data = database.get_session(self.session_id)
        if sess_data:
            sess_data["status"] = "unauthorized"
            sess_data["last_error"] = reason
            database.save_session(sess_data)
                    
        # 3. Remove from manager's running bots list locally
        try:
            from villain_ads_bot.services import userbot_manager
            bot = userbot_manager.find_running_bot(self.session_id)
            for k in list(userbot_manager._running_bots.keys()):
                if userbot_manager._running_bots[k] is self or (bot and userbot_manager._running_bots[k] is bot):
                    userbot_manager._running_bots.pop(k, None)
            logger.info(f"Removed userbot {self.session_id} from running registry.")
        except Exception as manager_err:
            logger.warning(f"Could not remove bot {self.session_id} from manager registry: {manager_err}")

    async def _watchdog_loop(self):
        """
        Background watchdog to keep the userbot connection alive indefinitely,
        auto-reconnect on network interruptions, and keep settings synchronized from MongoDB.
        """
        logger.info(f"Watchdog started for userbot {self.session_id}")
        while self.is_running:
            try:
                await asyncio.sleep(30.0)
                if not self.is_running:
                    break

                # 1. Periodically sync settings from database
                try:
                    sess_data = database.get_session(self.session_id)
                    if sess_data and "settings" in sess_data:
                        self.settings = sess_data["settings"]
                except Exception as sync_err:
                    logger.debug(f"Watchdog settings sync failed for {self.session_id}: {sync_err}")

                # 1.b Honour the "Run Timer" (run_expiry) - stop the userbot when
                #     the configured run time has elapsed.
                try:
                    sess_data = database.get_session(self.session_id)
                    run_expiry = sess_data.get("run_expiry") if sess_data else None
                    if run_expiry:
                        remaining = float(run_expiry) - time.time()
                        if remaining <= 0:
                            logger.info(f"Run timer expired for userbot {self.session_id}. Stopping.")
                            try:
                                sess_data["run_expiry"] = None
                                sess_data.pop("run_expiry", None)
                                sess_data["status"] = "stopped"
                                database.save_session(sess_data)
                            except Exception:
                                pass
                            # Stop in a separate task: stop() cancels the
                            # watchdog task (which is the current one).
                            asyncio.create_task(self.stop())
                            return
                except Exception as timer_err:
                    logger.debug(f"Run timer check failed for {self.session_id}: {timer_err}")

                # 2. Check and maintain Telethon connection
                if self.client:
                    if not self.client.is_connected():
                        logger.info(f"Watchdog: Userbot {self.session_id} disconnected. Reconnecting...")
                        try:
                            await asyncio.wait_for(self.client.connect(), timeout=15.0)
                            if await self.client.is_user_authorized():
                                logger.info(f"Watchdog: Userbot {self.session_id} reconnected successfully.")
                            else:
                                logger.warning(f"Watchdog: Userbot {self.session_id} reconnecting - authorization pending.")
                        except Exception as rec_err:
                            from telethon.errors import AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError
                            if isinstance(rec_err, (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError)):
                                logger.error(f"Watchdog: Userbot {self.session_id} session permanently revoked: {rec_err}")
                                await self._mark_unauthorized_and_cleanup(reason=str(rec_err))
                                break
                            logger.warning(f"Watchdog: Userbot {self.session_id} temporary reconnect error: {rec_err}. Will retry in 10s...")
                            await asyncio.sleep(10.0)
                            continue
                    else:
                        # Send keep-alive ping to prevent Telegram idle timeout
                        try:
                            me = await asyncio.wait_for(self.client.get_me(), timeout=10.0)
                            if me:
                                self.me_id = me.id
                                if me.username:
                                    self.username = me.username
                        except Exception as ping_err:
                            from telethon.errors import AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError
                            if isinstance(ping_err, (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedError)):
                                logger.error(f"Watchdog: Userbot {self.session_id} session permanently revoked during ping: {ping_err}")
                                await self._mark_unauthorized_and_cleanup(reason=str(ping_err))
                                break
                            logger.debug(f"Watchdog: Keepalive ping failed for {self.session_id}: {ping_err}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in watchdog loop for {self.session_id}: {e}")
                await asyncio.sleep(5.0)

    async def join_voice_chat(self, link_or_id: Union[str, int]) -> tuple:
        """
        Attempts to join the active voice call of a group/channel.
        Supports group link (public or private invite), username, or direct Chat ID.
        Uses PyTgCalls if available, with resilient fallback to native MTProto JoinGroupCallRequest.
        """
        if not self.is_running or not self.client:
            return False, "Userbot is not running. Please start it first."
            
        try:
            import telethon.utils as tu
            from telethon.tl.functions.channels import GetFullChannelRequest
            from telethon.tl.functions.messages import GetFullChatRequest
            from telethon.tl.functions.phone import JoinGroupCallRequest
            from telethon.tl.types import InputGroupCall, DataJSON, Channel, GroupCallDiscarded
            import random

            link_str = str(link_or_id).strip()
            entity = None
            
            # 1. Direct integer chat ID resolution
            if link_str.lstrip("-").isdigit():
                try:
                    entity = await self.client.get_entity(int(link_str))
                except Exception:
                    pass
                    
            # 2. Resolve via get_peer_from_link
            if not entity:
                entity = await get_peer_from_link(self.client, link_str)
                
            # 3. Direct dialog search fallback
            if not entity:
                async for dialog in self.client.iter_dialogs(limit=200):
                    if str(dialog.id) == link_str or (dialog.name and dialog.name.lower() in link_str.lower()):
                        entity = dialog.entity
                        break

            if not entity:
                return False, "Could not resolve the group link, username, or Chat ID. Please ensure the invite link is valid and UserBot has joined the group."

            full_chat_id = tu.get_peer_id(entity)
            chat_title = getattr(entity, 'title', f"Chat {full_chat_id}")

            # 4. Check whether active voice call exists in group
            try:
                if isinstance(entity, Channel):
                    full = await self.client(GetFullChannelRequest(entity))
                else:
                    full = await self.client(GetFullChatRequest(entity.id))
                group_call = full.full_chat.call
            except Exception as e_full:
                logger.warning(f"Failed to fetch full chat info for {full_chat_id}: {e_full}")
                group_call = None

            if not group_call or isinstance(group_call, GroupCallDiscarded):
                return False, f"No active voice chat (VC) is currently running in <b>{chat_title}</b>. Please start the VC in the group first!"

            # 5. Attempt join: PyTgCalls first (if available)
            joined = False
            if PYTGCALLS_AVAILABLE and PyTgCalls is not None:
                try:
                    pytg = await self.get_pytgcalls()
                    if group_call and not isinstance(group_call, GroupCallDiscarded):
                        try:
                            bind_client = getattr(pytg._app, '_bind_client', None)
                            if bind_client and hasattr(bind_client, '_cache'):
                                bind_client._cache.set_cache(
                                    full_chat_id,
                                    InputGroupCall(id=group_call.id, access_hash=group_call.access_hash)
                                )
                        except Exception as c_err:
                            logger.debug(f"Pre-caching call in join_voice_chat: {c_err}")
                    silence_file = await generate_silence()
                    if silence_file and os.path.exists(silence_file):
                        try:
                            await self._safe_play(pytg, full_chat_id, MediaStream(silence_file))
                            joined = True
                        except Exception as join_err:
                            logger.debug(f"Initial silence stream play note: {join_err}")
                        if joined:
                            # The userbot only joins to LISTEN - the microphone MUST stay muted.
                            # PyTgCalls 2.x joins a call with muted=False, so we explicitly mute.
                            try:
                                await utils.pytg_mute(pytg, full_chat_id)
                                self.is_muted = True
                                logger.info(f"Userbot {self.session_id} joined VC {full_chat_id} muted (listener mode).")
                            except Exception as mute_err:
                                logger.warning(f"Could not mute after joining VC: {mute_err}")
                                # Fall back to a zero-volume stream so the mic stays silent.
                                try:
                                    await pytg.change_volume_call(full_chat_id, 0)
                                except Exception:
                                    pass
                except Exception as pytg_err:
                    logger.warning(f"PyTgCalls join attempt failed: {pytg_err}. Falling back to native MTProto...")

            # 6. Fallback to native MTProto JoinGroupCallRequest
            if not joined:
                last_err = None
                for attempt in range(5):
                    random_ssrc = random.randint(100000, 999999999)
                    try:
                        params_json = f'{{"transport":{{"webrtc":true}},"muted":true,"video_stopped":true,"ssrc":{random_ssrc}}}'
                        await self.client(JoinGroupCallRequest(
                            call=InputGroupCall(
                                id=group_call.id,
                                access_hash=group_call.access_hash
                            ),
                            join_as=await self.client.get_input_entity('me'),
                            params=DataJSON(data=params_json),
                            muted=True
                        ))
                        joined = True
                        logger.info(f"Successfully joined VC for {chat_title} using native MTProto with SSRC {random_ssrc}")
                        break
                    except Exception as nat_err:
                        last_err = nat_err
                        err_str = str(nat_err).lower()
                        if "ssrc" in err_str or "duplicate" in err_str:
                            await asyncio.sleep(0.5)
                            continue
                        elif "already" in err_str or "participant" in err_str:
                            joined = True
                            break
                        else:
                            break

                if not joined:
                    detail = str(last_err) if last_err else "Unknown error"
                    utils.report_error(f"join_voice_chat:{self.session_id}", RuntimeError(detail))
                    return False, f"Failed to join VC in <b>{chat_title}</b>: <code>{detail[:120]}</code>"

            self.current_vc_chat_id = full_chat_id
            self.current_vc_link = link_str
            self.joined_vcs.add(full_chat_id)

            # Save VC status in MongoDB
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["vc_chat_id"] = full_chat_id
                sess_data["vc_link"] = link_str
                database.save_session(sess_data)
                
            return True, f"Successfully joined Voice Chat of <b>{chat_title}</b>!"
        except Exception as e:
            logger.exception("Error in join_voice_chat")
            utils.report_error(f"join_voice_chat:{self.session_id}", e)
            return False, "Failed to join the voice chat. The error was reported to the admin team."

    async def join_all_active_group_vcs(self) -> tuple:
        """
        Scans all groups of this userbot, finds groups with active voice chats, and joins them.
        Returns (joined_count: int, total_active_vcs: int)
        """
        if not self.is_running or not self.client:
            return 0, 0
            
        groups = await self.get_groups(force_refresh=True)
        joined = 0
        total_vcs = 0
        
        for g in groups:
            chat_id = getattr(g, "id", None) or (g.get("id") if isinstance(g, dict) else g)
            try:
                success, _ = await self.join_voice_chat(str(chat_id))
                if success:
                    joined += 1
                    total_vcs += 1
            except Exception:
                continue
                
        return joined, total_vcs

    async def leave_voice_chat(self, chat_id: Optional[int] = None) -> tuple:
        """
        Leaves any active group call/VC the userbot is in.
        """
        if not self.is_running or not self.client:
            return False, "Userbot is not running."
            
        target_chat = chat_id or self.current_vc_chat_id
        try:
            if self.pytgcalls_client and target_chat:
                try:
                    await self._safe_leave(self.pytgcalls_client, target_chat)
                except Exception as e:
                    logger.warning(f"Error leaving via pytgcalls: {e}")
                try:
                    self.joined_vcs.discard(target_chat)
                except Exception:
                    pass
                    
            if target_chat == self.current_vc_chat_id:
                self.current_vc_chat_id = None
                self.current_vc_link = None
            
            # Clear VC status in MongoDB
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["vc_chat_id"] = None
                sess_data["vc_link"] = None
                sess_data["current_song"] = None
                database.save_session(sess_data)
                
            return True, "Successfully left voice chat."
        except Exception as e:
            logger.warning(f"Error leaving VC for userbot {self.session_id}: {e}")
            return False, f"Error leaving VC: {e}"

    async def leave_all_voice_chats(self) -> tuple:
        """
        Leaves all active voice chats / group calls that this userbot has joined.
        Returns (left_count: int, message: str)
        """
        if not self.is_running or not self.client:
            return 0, "Userbot is not running."

        left_count = 0
        chats_to_leave = set(getattr(self, "joined_vcs", set()))
        if self.current_vc_chat_id:
            chats_to_leave.add(self.current_vc_chat_id)

        # Also check MongoDB for any saved vc_chat_id
        sess_data = database.get_session(self.session_id)
        if sess_data and sess_data.get("vc_chat_id"):
            chats_to_leave.add(sess_data["vc_chat_id"])

        for cid in list(chats_to_leave):
            try:
                if self.pytgcalls_client:
                    await self._safe_leave(self.pytgcalls_client, cid)
                left_count += 1
            except Exception as e:
                logger.warning(f"Error leaving group call {cid} for userbot {self.session_id}: {e}")
            finally:
                if hasattr(self, "joined_vcs"):
                    self.joined_vcs.discard(cid)

        self.current_vc_chat_id = None
        self.current_vc_link = None

        if sess_data:
            sess_data["vc_chat_id"] = None
            sess_data["vc_link"] = None
            sess_data["current_song"] = None
            database.save_session(sess_data)

        msg = f"Successfully left {max(left_count, 1)} voice chat(s)." if left_count > 0 else "Left all voice chats."
        return max(left_count, 1 if chats_to_leave else 0), msg

    async def get_pytgcalls(self):
        if not PYTGCALLS_AVAILABLE or PyTgCalls is None:
            raise RuntimeError("PyTgCalls is not available or supported in this environment.")
        if not self.pytgcalls_client:
            self.pytgcalls_client = PyTgCalls(self.client)
            
            if hasattr(self.pytgcalls_client, "on_update"):
                try:
                    from pytgcalls.types import StreamEnded
                    @self.pytgcalls_client.on_update()
                    async def handle_stream_update(client, update):
                        if isinstance(update, StreamEnded):
                            try:
                                chat_id = getattr(update, "chat_id", None)
                                if chat_id:
                                    from villain_ads_bot.handlers.player import play_next_in_queue
                                    await play_next_in_queue(chat_id)
                            except Exception as e:
                                logger.error(f"Error in handle_stream_update: {e}")
                except Exception as reg_err:
                    logger.warning(f"Could not register StreamEnded update listener: {reg_err}")
            elif hasattr(self.pytgcalls_client, "on_stream_end"):
                try:
                    @self.pytgcalls_client.on_stream_end()
                    async def on_stream_end(client, update):
                        try:
                            chat_id = getattr(update, "chat_id", None)
                            if chat_id:
                                from villain_ads_bot.handlers.player import play_next_in_queue
                                await play_next_in_queue(chat_id)
                        except Exception as e:
                            logger.error(f"Error in on_stream_end: {e}")
                except Exception as reg_err:
                    logger.warning(f"Could not register on_stream_end listener: {reg_err}")

            await self.pytgcalls_client.start()
        return self.pytgcalls_client

    async def _prepopulate_pytgcalls_cache(self, pytg, chat_id):
        """Ensures PyTgCalls internal cache has the active InputGroupCall for chat_id, eliminating NoActiveGroupCall."""
        if not self.client or not pytg:
            return
        try:
            from telethon.tl.functions.channels import GetFullChannelRequest
            from telethon.tl.functions.messages import GetFullChatRequest
            from telethon.tl.types import Channel, InputGroupCall, GroupCallDiscarded
            import telethon.utils as tu

            target_entity = None
            try:
                target_entity = await self.client.get_entity(chat_id)
            except Exception:
                pass

            if not target_entity:
                return

            if isinstance(target_entity, Channel):
                full = await self.client(GetFullChannelRequest(target_entity))
            else:
                full = await self.client(GetFullChatRequest(target_entity.id))

            group_call = getattr(full.full_chat, 'call', None)
            if group_call and not isinstance(group_call, GroupCallDiscarded):
                input_call = InputGroupCall(id=group_call.id, access_hash=group_call.access_hash)
                numeric_chat_id = tu.get_peer_id(target_entity)
                
                bind_client = getattr(pytg._app, '_bind_client', None)
                if bind_client and hasattr(bind_client, '_cache'):
                    bind_client._cache.set_cache(numeric_chat_id, input_call)
                    if isinstance(chat_id, int) and chat_id != numeric_chat_id:
                        bind_client._cache.set_cache(chat_id, input_call)
                    logger.info(f"PyTgCalls cache pre-populated for chat {numeric_chat_id} (Call ID: {group_call.id})")
        except Exception as e:
            logger.debug(f"Could not pre-populate PyTgCalls cache for {chat_id}: {e}")

    async def _safe_play(self, pytg, chat_id, stream_obj):
        """Unified media player for pytgcalls 1.x and 2.x with automatic cache pre-population."""
        await self._prepopulate_pytgcalls_cache(pytg, chat_id)
        if hasattr(pytg, "play"):
            return await pytg.play(chat_id, stream_obj)
        elif hasattr(pytg, "join_group_call"):
            try:
                return await pytg.join_group_call(chat_id, stream_obj)
            except Exception:
                if hasattr(pytg, "change_stream"):
                    return await pytg.change_stream(chat_id, stream_obj)
                raise
        elif hasattr(pytg, "change_stream"):
            return await pytg.change_stream(chat_id, stream_obj)

    async def _safe_leave(self, pytg, chat_id):
        """Unified call leaver for pytgcalls 1.x and 2.x."""
        if hasattr(pytg, "leave_call"):
            return await pytg.leave_call(chat_id)
        elif hasattr(pytg, "leave_group_call"):
            return await pytg.leave_group_call(chat_id)

    async def mute_mic(self, chat_id: Optional[int] = None) -> tuple:
        target_chat = chat_id or self.current_vc_chat_id
        if not self.is_running or not target_chat:
            return False, "Bot is not in any VC."
        try:
            pytg = await self.get_pytgcalls()
            await utils.pytg_mute(pytg, target_chat)
            self.is_muted = True
            return True, "Mic turned OFF (Muted)."
        except Exception as e:
            logger.error(f"Failed to mute: {e}")
            utils.report_error(f"userbot.mute_mic:{self.session_id}", e)
            return False, "Failed to mute the microphone."

    async def unmute_mic(self, chat_id: Optional[int] = None) -> tuple:
        target_chat = chat_id or self.current_vc_chat_id
        if not self.is_running or not target_chat:
            return False, "Bot is not in any VC."
        try:
            pytg = await self.get_pytgcalls()
            await utils.pytg_unmute(pytg, target_chat)
            self.is_muted = False
            return True, "Mic turned ON (Unmuted)."
        except Exception as e:
            logger.error(f"Failed to unmute: {e}")
            utils.report_error(f"userbot.unmute_mic:{self.session_id}", e)
            return False, "Failed to unmute the microphone."

    async def stop_song(self, chat_id: Optional[int] = None) -> tuple:
        target_chat = chat_id or self.current_vc_chat_id
        if not target_chat:
            return False, "Not in a Voice Chat."
        try:
            from villain_ads_bot.handlers.player import _chat_queues, _active_chat_players, _track_timer_tasks
            # 4.3 - the cleanup ran AFTER the "is running" check, so a stopped
            # userbot never cleared the queue, the card or the timer.
            _chat_queues.pop(target_chat, None)
            # The "Now Playing" card is removed from the chat as well, so a
            # stopped song never leaves a stale player card behind.
            stale = _active_chat_players.pop(target_chat, None)
            if stale:
                try:
                    from villain_ads_bot.handlers.player import _delete_card_by_session
                    await _delete_card_by_session(stale, target_chat)
                except Exception:
                    pass
            prev_timer = _track_timer_tasks.pop(target_chat, None)
            if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
                prev_timer.cancel()

            if not self.is_running:
                # The userbot is already stopped - the cleanup above is done.
                return True, "Playback stopped."

            pytg = await self.get_pytgcalls()
            try:
                await self._safe_leave(pytg, target_chat)
                logger.info(f"Userbot {self.session_id} left VC {target_chat} after stop.")
            except Exception as leave_err:
                err_str = str(leave_err).lower()
                if "not in a group call" in err_str or "not_in" in err_str or "no active" in err_str:
                    logger.info(f"Userbot {self.session_id} was already not in VC {target_chat}.")
                else:
                    logger.warning(f"leave call error: {leave_err} — falling back to silence+mute")
                    try:
                        silence_file = await generate_silence()
                        await self._safe_play(pytg, target_chat, MediaStream(silence_file))
                        await utils.pytg_mute(pytg, target_chat)
                        self.is_muted = True
                    except Exception:
                        pass

            # Update in-memory VC state
            self.joined_vcs.discard(target_chat)
            if target_chat == self.current_vc_chat_id:
                self.current_vc_chat_id = None
                self.current_vc_link = None
            self.is_muted = False

            # Clear MongoDB status
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["current_song"] = None
                sess_data["vc_chat_id"] = None
                database.save_session(sess_data)
            return True, "Playback stopped and left voice chat."
        except Exception as e:
            logger.error(f"Failed to stop playback: {e}")
            return False, f"Failed to stop: {e}"

    async def play_song(self, query: str, play_type: str = "audio", chat_id: Optional[Union[int, str]] = None, local_file: str = None, title: str = None, duration: int = 30) -> tuple:
        """
        Plays a song (audio or video) in the specified or active Voice Chat of this userbot.
        Automatically joins the Voice Chat if not already connected.
        Returns (success: bool, message: str, song_info: dict)
        """
        if not self.is_running or not self.client:
            return False, "Userbot is not running.", None
            
        target_chat_id = chat_id or self.current_vc_chat_id
        if not target_chat_id:
            return False, "No target Voice Chat specified.", None
            
        # If passed as string/int, normalize
        try:
            target_chat_id = int(str(target_chat_id).strip())
        except Exception:
            pass

        actual_chat = target_chat_id
        file_path = None
        thumb = None
        
        if local_file:
            if not os.path.exists(local_file):
                return False, f"Local file not found: {local_file}", None
            file_path = local_file
            title = title or "Uploaded Media"
            
            # Extract accurate duration using ffprobe for local files
            try:
                import subprocess
                cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
                proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, _ = await proc.communicate()
                if proc.returncode == 0 and stdout:
                    duration = int(float(stdout.decode().strip()))
            except Exception:
                pass
                
            duration = duration or 30
        else:
            # Download the media
            logger.info(f"Userbot {self.session_id} downloading {play_type} query: {query}")
            try:
                file_path, title, duration, thumb = await download_media(query, download_type=play_type)
            except Exception as dl_err:
                return False, f"YouTube download failed: {dl_err}", None
                
            if not file_path or not os.path.exists(file_path):
                return False, "Failed to download or parse media from YouTube. The link might be broken or region-restricted.", None
            
        import telethon.utils as tu
        try:
            entity = await self.client.get_entity(actual_chat)
            actual_chat = tu.get_peer_id(entity)
        except Exception as e_res:
            logger.debug(f"Entity resolution for {actual_chat} note: {e_res}")

        try:
            pytg = await self.get_pytgcalls()
            from pytgcalls.types import MediaStream, VideoQuality

            # Check if transition between audio and video requires leaving the call first.
            # Telegram group calls require a fresh JoinGroupCall with video_stopped=False to broadcast video.
            # If the userbot was already connected (e.g. from silence stream or prior audio play),
            # PyTgCalls play() only updates sources via set_stream_sources without re-negotiating video_stopped=False.
            # We leave first so PyTgCalls executes a full fresh _connect_call with video_stopped=False!
            sess_data = database.get_session(self.session_id) or {}
            prev_play_type = (sess_data.get("current_song") or {}).get("play_type")
            should_reconnect = (play_type == "video") or (prev_play_type == "video" and play_type == "audio")

            if should_reconnect:
                try:
                    is_already_in_call = False
                    if hasattr(pytg, "_binding"):
                        active_calls = await pytg._binding.calls()
                        for c in active_calls:
                            if str(c) in str(actual_chat) or str(actual_chat) in str(c):
                                is_already_in_call = True
                                break
                    if not is_already_in_call and (actual_chat in self.joined_vcs or self.current_vc_chat_id == actual_chat):
                        is_already_in_call = True

                    if is_already_in_call:
                        logger.info(f"Userbot {self.session_id} leaving existing call in {actual_chat} to reset video_stopped state for {play_type} stream.")
                        try:
                            await self._safe_leave(pytg, actual_chat)
                        except Exception as leave_err:
                            logger.debug(f"Pre-{play_type} safe_leave note: {leave_err}")
                        await asyncio.sleep(0.5)
                except Exception as leave_check_err:
                    logger.debug(f"Pre-{play_type} leave check note: {leave_check_err}")
            
            if play_type == "video":
                has_video_track = False
                try:
                    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type,width,height", "-of", "csv=s=x:p=0", file_path]
                    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await proc.communicate()
                    if proc.returncode == 0 and stdout:
                        res = stdout.decode().strip()
                        if 'video' in res or 'x' in res:
                            has_video_track = True
                            logger.info(f"Video track confirmed in {file_path}")
                except Exception as e:
                    logger.warning(f"Could not check video track: {e}")
                    has_video_track = True  # Assume true for mp4 if probe fails
                    
                if has_video_track:
                    stream_obj = MediaStream(
                        file_path,
                        video_parameters=VideoQuality.HD_720p,
                        video_flags=MediaStream.Flags.REQUIRED
                    )
                    logger.info(f"Streaming video (MediaStream HD_720p REQUIRED): {file_path}")
                else:
                    logger.info(f"No video track in {file_path}, falling back to audio seamlessly")
                    stream_obj = MediaStream(file_path, video_flags=MediaStream.Flags.IGNORE)
            else:
                stream_obj = MediaStream(file_path, video_flags=MediaStream.Flags.IGNORE)
                
            utils.mark_media_active(file_path)
            try:
                await self._safe_play(pytg, actual_chat, stream_obj)
                self.joined_vcs.add(actual_chat)
                self.current_vc_chat_id = actual_chat
                logger.info(f"Playback succeeded for {actual_chat}")
            except Exception as play_err:
                logger.warning(f"Initial playback failed ({play_err}), attempting fallback...")
                try:
                    await self._safe_leave(pytg, actual_chat)
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
                if play_type == "video":
                    stream_obj = MediaStream(
                        file_path,
                        video_parameters=VideoQuality.SD_480p,
                        video_flags=MediaStream.Flags.REQUIRED
                    )
                else:
                    stream_obj = MediaStream(file_path, video_flags=MediaStream.Flags.IGNORE)
                try:
                    await self._safe_play(pytg, actual_chat, stream_obj)
                    self.joined_vcs.add(actual_chat)
                    self.current_vc_chat_id = actual_chat
                except Exception as final_err:
                    return False, f"Could not stream media: {final_err}", None
                    
            song_info = {
                "title": title,
                "duration": duration,
                "thumb": thumb,
                "file_path": file_path,
                "play_type": play_type,
                "userbot_name": self.name,
                "userbot_id": self.session_id,
                "username": self.username
            }
            
            # Automatically unmute the stream for music playback
            try:
                await utils.pytg_unmute(pytg, actual_chat)
                self.is_muted = False
                logger.info(f"Automatically unmuted userbot {self.session_id} for song playing in {actual_chat}.")
            except Exception as unmute_err:
                logger.warning(f"Could not auto-unmute stream on play: {unmute_err}")
                utils.report_error(f"userbot.play_song.unmute:{self.session_id}", unmute_err)

            # If playing video, ensure video is unstopped in group call status
            if play_type == "video":
                try:
                    from telethon.tl.types import InputPeerSelf
                    from telethon.tl.functions.phone import EditGroupCallParticipantRequest
                    # Set via telethon client directly using InputGroupCall & InputPeerSelf
                    input_call = None
                    if hasattr(pytg, "_app") and hasattr(pytg._app, "get_input_call"):
                        input_call = await pytg._app.get_input_call(actual_chat)
                    if input_call:
                        await self.client(
                            EditGroupCallParticipantRequest(
                                call=input_call,
                                participant=InputPeerSelf(),
                                muted=False,
                                video_stopped=False,
                                video_paused=False,
                                presentation_paused=False
                            )
                        )
                        logger.info(f"Directly called EditGroupCallParticipantRequest video_stopped=False for {actual_chat}")
                except Exception as vid_status_err:
                    logger.debug(f"Direct video call status update note: {vid_status_err}")
            
            # Save playing status in MongoDB
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["current_song"] = {
                    "title": title,
                    "duration": duration,
                    "play_type": play_type,
                    "query": query or title,
                    "chat_id": actual_chat
                }
                database.save_session(sess_data)
                
            return True, f"Now playing {title}", song_info
        except Exception as e:
            logger.error(f"Error playing media: {e}")
            return False, f"Error playing media: {e}", None

    async def get_groups(self, force_refresh: bool = False) -> list:
        """
        Returns group dialogs, utilizing a 60-second cache to ensure newly joined groups
        are automatically detected and broadcasted to without requiring a bot restart.
        """
        current_time = time.time()
        # Cache dialogs for 60 seconds unless force-refreshed (so new groups are dynamically picked up)
        if force_refresh or not self.groups_cache or (current_time - self.groups_cache_time > 60):
            if not self.client or not self.client.is_connected():
                logger.debug(f"Userbot {self.session_id} is disconnected, skipping dialog fetch.")
                return self.groups_cache or []
            try:
                logger.info(f"Fetching dialogs for userbot {self.session_id} to refresh groups cache...")
                async with _dialog_fetch_semaphore:
                    dialogs = await asyncio.wait_for(self.client.get_dialogs(limit=None), timeout=30.0)
                # Channels / supergroups / groups - the broadcast must reach
                # every chat the account is a member of.
                self.groups_cache = [d for d in dialogs if d.is_group or d.is_channel]
                self.groups_cache_time = current_time
                
                # Update DB stats concurrently
                sess_data = database.get_session(self.session_id)
                if sess_data:
                    sess_data.setdefault("stats", {})["group_count"] = len(self.groups_cache)
                    sess_data["stats"]["user_count"] = sum(1 for d in dialogs if d.is_user)
                    database.save_session(sess_data)
            except Exception as e:
                logger.error(f"Error fetching dialogs for userbot {self.session_id}: {e}")
                if "database disk image is malformed" in str(e).lower():
                    logger.error(f"SQLite DB corrupted for {self.session_id}. Removing from MongoDB to force re-login.")
                    sess_data = database.get_session(self.session_id)
                    if sess_data and "session_bytes" in sess_data:
                        del sess_data["session_bytes"]
                        sess_data["status"] = "stopped"
                        database.save_session(sess_data)
                    try:
                        os.remove(sess_data.get("session_file") or f"user_data/{self.me_id}/sessions/{self.session_id}.session")
                    except Exception:
                        pass
                if not self.groups_cache:
                    self.groups_cache = []
        return self.groups_cache

    async def start(self) -> bool:
        if self.is_running:
            return True
            
        sess_data = database.get_session(self.session_id, include_bytes=True)
        if not sess_data:
            logger.error(f"Session data not found in DB for {self.session_id}")
            return False
            
        # Initialize in-memory settings
        self.settings = sess_data.get("settings", {})
        
        # Cross-platform session path normalization:
        # DB may contain Windows paths (e.g. F:\villainAdsbot4\user_data\...) when running on Linux/Docker,
        # or vice-versa. Always normalize to the current platform's USER_DATA_DIR.
        user_id_val = sess_data.get("user_id") or "common"
        phone_val = sess_data.get("phone") or self.session_id
        normalized_dir = os.path.join(config.USER_DATA_DIR, str(user_id_val), "sessions")
        expected_session_file = os.path.join(normalized_dir, f"{phone_val}.session")
        
        raw_session_file = sess_data.get("session_file")
        if not raw_session_file or not os.path.exists(raw_session_file):
            session_file = expected_session_file
        else:
            session_file = raw_session_file
            
        sess_data["session_file"] = session_file

        # Restore session file from MongoDB if it was saved
        session_bytes = sess_data.get("session_bytes")
        if session_bytes:
            try:
                os.makedirs(os.path.dirname(session_file), exist_ok=True)
                with open(session_file, "wb") as f:
                    f.write(session_bytes)
                
                # Delete any stale SQLite journal/WAL files to prevent mismatch corruption
                for suffix in ["-wal", "-shm", "-journal"]:
                    try:
                        if os.path.exists(session_file + suffix):
                            os.remove(session_file + suffix)
                    except:
                        pass
                        
                logger.info(f"Restored session file from MongoDB to {session_file}")
            except Exception as e:
                logger.error(f"Failed to restore session file from MongoDB to {session_file}: {e}")

        if not os.path.exists(session_file):
            # Fallback check in normalized_dir if original path didn't exist
            if os.path.exists(expected_session_file):
                session_file = expected_session_file
                sess_data["session_file"] = session_file
            else:
                logger.error(f"Session file not found: {session_file} (tried expected: {expected_session_file})")
                # Mark status stopped/unauthorized in database so it doesn't get retried
                sess_data = database.get_session(self.session_id)
                if sess_data:
                    sess_data["status"] = "stopped"
                    database.save_session(sess_data)
                session_lock.release(self.session_id)
                return False
            
        # Spoof a deterministic device profile to avoid detection
        device_prof = utils.get_device_profile(self.session_id)
        # Ensure session uses consistent API credentials (saved in DB or deterministically mapped)
        sess_data = database.get_session(self.session_id)
        api_id = sess_data.get("api_id") if sess_data else None
        api_hash = sess_data.get("api_hash") if sess_data else None
        if not api_id or not api_hash:
            api_id, api_hash = config.get_deterministic_api_id_hash(self.session_id)
            if sess_data and api_id and api_hash:
                sess_data["api_id"] = api_id
                sess_data["api_hash"] = api_hash
                database.save_session(sess_data)
        # A session may only be used by ONE process. Two instances using the
        # same session file destroy the account with AuthKeyDuplicatedError.
        if not session_lock.acquire(self.session_id):
            logger.error(
                f"Session {self.session_id} is already in use by another "
                f"process - this account is NOT started here."
            )
            self.is_running = False
            return False

        from telethon.network.connection import ConnectionTcpObfuscated

        # The proxy of this account. Telegram counts every account of an IP
        # address together, so without a proxy only a handful of accounts can
        # be active at the same time.
        #
        # IMPORTANT: the proxy is read ONCE, when the account starts. The IP
        # of a running account never changes on its own - Telegram revokes the
        # session (AUTH_KEY_DUPLICATED) when the same auth key suddenly shows
        # up from a different IP. Rotating is a deliberate admin action that
        # disconnects the account first.
        proxy_kwargs = {}
        try:
            from villain_ads_bot.core import proxy as _proxy
            account_proxy = _proxy.resolve_session_proxy(self.session_id)
            if account_proxy:
                telethon_proxy = _proxy.build_telethon_proxy(account_proxy)
                if telethon_proxy:
                    proxy_kwargs["proxy"] = telethon_proxy
                    connection_class = _proxy.get_connection_class(account_proxy)
                    proxy_kwargs["connection"] = connection_class
                    logger.info(
                        f"Userbot {self.session_id} will connect through "
                        f"{_proxy.redact(account_proxy)}"
                    )
        except Exception as proxy_err:
            logger.warning(f"Could not apply the proxy for {self.session_id}: {proxy_err}")

        self.client = TelegramClient(
            session_file, 
            api_id, 
            api_hash,
            connection=proxy_kwargs.pop("connection", ConnectionTcpObfuscated),
            connection_retries=6,
            retry_delay=1,
            timeout=15,
            auto_reconnect=True,
            device_model=device_prof["device_model"],
            system_version=device_prof["system_version"],
            app_version=device_prof["app_version"],
            lang_code=device_prof.get("lang_code", "en"),
            **proxy_kwargs
        )
        self.client.parse_mode = "html"
        
        # SQLite optimizations: use MEMORY journal to avoid detached WAL files
        try:
            conn = self.client.session._conn
            if conn:
                conn.execute("PRAGMA journal_mode=MEMORY;")
                conn.execute("PRAGMA synchronous=NORMAL;")
        except Exception as sqlite_opt_err:
            logger.warning(f"Could not optimize SQLite session parameters: {sqlite_opt_err}")
        
        # NEW: Check if the sqlite file is locked by the OS.
        # This prevents the main bot event loop from hanging indefinitely during connect()
        import sqlite3
        if os.path.exists(session_file):
            test_conn = None
            try:
                # Use timeout=0.1 so it fails fast without blocking the event loop
                test_conn = sqlite3.connect(session_file, timeout=0.1)
                test_conn.execute("BEGIN IMMEDIATE")
                test_conn.commit()
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e).lower() or "busy" in str(e).lower():
                    logger.error(f"Cannot start userbot {self.session_id} because session file is locked by the OS.")
                    # Return False so the manager doesn't crash the bot
                    return False
            finally:
                if test_conn:
                    try:
                        test_conn.close()
                    except Exception:
                        pass

        try:
            # Wrap connect() in wait_for with generous 25s timeout for high-concurrency hosting
            await asyncio.wait_for(self.client.connect(), timeout=25.0)
            
            # Check authorization with retry
            is_authorized = False
            try:
                is_authorized = await asyncio.wait_for(self.client.is_user_authorized(), timeout=15.0)
            except Exception:
                pass
            if not is_authorized:
                await asyncio.sleep(2.0)
                try:
                    is_authorized = await asyncio.wait_for(self.client.is_user_authorized(), timeout=15.0)
                except Exception:
                    pass

            if not is_authorized:
                logger.warning(f"Userbot {self.session_id} is unauthorized or pending authorization.")
                # Mark status as unauthorized safely WITHOUT deleting session files or credentials
                await self._mark_unauthorized_and_cleanup(reason="Session unauthorized or requires re-login.")
                return False
                
            self.is_running = True
            
            # Refresh name and username info preserving cloned profile or custom name
            try:
                me = await self.client.get_me()
                if me:
                    self.me_id = me.id
                    full_n = f"{me.first_name or ''} {me.last_name or ''}".strip()
                    if sess_data.get("is_cloned") or sess_data.get("cloned_name"):
                        self.name = sess_data.get("cloned_name") or full_n or "UserBot"
                    else:
                        self.name = full_n or sess_data.get("name") or "UserBot"
                    sess_data["name"] = self.name
                    self.username = me.username or ""
                    sess_data["username"] = me.username or ""
            except Exception as me_err:
                logger.warning(f"Could not fetch get_me for {self.session_id} on startup: {me_err}")
                
            # Apply configurations safely
            global_settings = database.get_global_settings()
            
            # Auto-join support links only if not already joined
            # DO NOT join force_join_links on userbots (those are for bot users, joining them causes Telegram freeze)
            joined_channels = sess_data.get("joined_channels", [])
            support_links = []
            support_channel = (global_settings.get("support_channel") or "https://t.me/TheVillainActive").strip()
            support_group = (global_settings.get("support_group") or "https://t.me/+WzyoJkg4bzhlNTFl").strip()
            for sl in (support_channel, support_group):
                if sl and sl not in joined_channels:
                    support_links.append(sl)
            
            ub_joins = global_settings.get("userbot_auto_join_links", [])
            for uj in ub_joins:
                uj_clean = (uj or "").strip()
                if uj_clean and uj_clean not in joined_channels and uj_clean not in support_links:
                    support_links.append(uj_clean)
                    
            if support_links and not sess_data.get("support_joined"):
                async def _delayed_safe_joins():
                    # Wait 25s after connection to avoid spamming MTProto during handshake
                    await asyncio.sleep(25.0)
                    if self.is_running and self.client and self.client.is_connected():
                        await force_join_channels(self.client, support_links, session_id=self.session_id)
                        sess_data["support_joined"] = True
                        database.save_session(sess_data)
                t_join1 = asyncio.create_task(_delayed_safe_joins())
                self.bg_tasks.add(t_join1)
                t_join1.add_done_callback(self.bg_tasks.discard)

            # Apply branding to this freshly hosted userbot.
            # ANY of name-text / bio-text / branding-username triggers it, so a
            # bio-only branding configuration is honoured as well.
            brand_name_text = (global_settings.get("branding_name_text") or "").strip()
            brand_bio_text = (global_settings.get("branding_bio_text") or "").strip()
            brand_username = (global_settings.get("branding_username") or "").strip()
            if (brand_name_text or brand_bio_text or brand_username) and not _is_cloned_session(sess_data):
                t_brand = asyncio.create_task(apply_branding(self.client, brand_username, sess_data))
                self.bg_tasks.add(t_brand)
                t_brand.add_done_callback(self.bg_tasks.discard)
            elif sess_data.get("branding_base_name") is not None:
                # Branding was switched off while the bot was offline -> revert it.
                t_brand = asyncio.create_task(apply_branding(self.client, brand_username, sess_data))
                self.bg_tasks.add(t_brand)
                t_brand.add_done_callback(self.bg_tasks.discard)
                
            # Register event handlers
            self._register_handlers()
            
            # Launch broadcast loop
            self.broadcast_task = asyncio.create_task(self.broadcast_loop())
            
            # Launch connection watchdog loop to keep userbot permanently active
            self.watchdog_task = asyncio.create_task(self._watchdog_loop())
            self.bg_tasks.add(self.watchdog_task)
            self.watchdog_task.add_done_callback(self.bg_tasks.discard)
            
            # Update status
            sess_data["status"] = "running"
                
            # Read session file into bytes to back up
            if os.path.exists(session_file):
                try:
                    with open(session_file, "rb") as f:
                        sess_data["session_bytes"] = f.read()
                except Exception as read_err:
                    logger.error(f"Failed to read session file for DB backup in start(): {read_err}")
                    
            # Auto-rejoin Voice Chat & resume song if configured in MongoDB
            saved_vc_link = sess_data.get("vc_link")
            if saved_vc_link:
                async def _auto_rejoin_vc():
                    await asyncio.sleep(3.0)
                    logger.info(f"Userbot {self.session_id} auto-rejoining saved VC: {saved_vc_link}")
                    success, join_msg = await self.join_voice_chat(saved_vc_link)
                    if success:
                        saved_song = sess_data.get("current_song")
                        if saved_song:
                            logger.info(f"Userbot {self.session_id} resuming saved song: {saved_song['title']}")
                            await self.play_song(
                                query=saved_song.get("query"),
                                play_type=saved_song.get("play_type", "audio")
                            )
                t_rejoin = asyncio.create_task(_auto_rejoin_vc())
                self.bg_tasks.add(t_rejoin)
                t_rejoin.add_done_callback(self.bg_tasks.discard)

            database.save_session(sess_data)
            logger.info(f"Userbot {self.session_id} started successfully.")
            import gc
            gc.collect()
            return True
        except asyncio.TimeoutError:
            logger.error(f"Timeout starting userbot {self.session_id} (connection took too long)")
            self.is_running = False
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            await self._mark_start_failed("Session start timed out")
            session_lock.release(self.session_id)
            return False
        except Exception as e:
            err_name = type(e).__name__
            if "AuthKeyDuplicated" in err_name:
                # The session is dead for good - the account has to log in again.
                logger.error(
                    f"Session {self.session_id} was used from two different IPs "
                    f"at the same time and can no longer be used. The account "
                    f"has to log in again."
                )
                try:
                    _sess = database.get_session(self.session_id)
                    if _sess:
                        _sess["status"] = "unauthorized"
                        _sess["last_error"] = (
                            "AuthKeyDuplicatedError - the session was used from two "
                            "different IPs at the same time. Log the account in again."
                        )
                        database.save_session(_sess)
                except Exception:
                    pass
                session_lock.release(self.session_id)
                return False
            logger.error(f"Failed to start userbot {self.session_id}: {e}")
            self.is_running = False
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
            
            err_class = e.__class__.__name__
            if err_class in ("UserDeactivatedError", "AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError"):
                logger.warning(f"Detected deactivated/revoked session {self.session_id} on startup. Performing cleanup.")
                await self._mark_unauthorized_and_cleanup()
            else:
                await self._mark_start_failed(str(e))
            session_lock.release(self.session_id)
            return False

    async def _mark_start_failed(self, reason: str):
        """
        A failed start must not leave the status as "running" in MongoDB.
        Otherwise the dashboard shows a phantom running account and the bot
        retries it on every restart.
        """
        try:
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["status"] = "stopped"
                sess_data["last_error"] = str(reason)[:300]
                database.save_session(sess_data)
        except Exception:
            pass

    async def stop(self):
        if not self.is_running:
            session_lock.release(self.session_id)
            return
            
        self.is_running = False
        
        if hasattr(self, "watchdog_task") and self.watchdog_task:
            self.watchdog_task.cancel()
            self.watchdog_task = None
            
        if self.broadcast_task:
            self.broadcast_task.cancel()
            
        if self.vc_keepalive_task:
            self.vc_keepalive_task.cancel()
            self.vc_keepalive_task = None
            
            # The background tasks were never cancelled. _auto_rejoin_vc sleeps
            # 3 seconds and then joins a voice chat - even when the userbot was
            # stopped in the meantime.
            for task in list(self.bg_tasks):
                try:
                    task.cancel()
                except Exception:
                    pass
            self.bg_tasks.clear()

            # 4.2 - the queue, the track timer and the "Now Playing" card were
            # never cleaned up. The timer kept running and later discarded
            # every queued track.
            try:
                from villain_ads_bot.handlers import player as _player
                for chat_id in list(_player._chat_queues.keys()):
                    try:
                        await self.stop_song(chat_id)
                    except Exception:
                        pass
                for chat_id, timer in list(_player._track_timer_tasks.items()):
                    try:
                        if timer and not timer.done():
                            timer.cancel()
                    except Exception:
                        pass
                _player._track_timer_tasks.clear()
            except Exception:
                pass
            
        if self.pytgcalls_client:
            try:
                if self.current_vc_chat_id:
                    await self._safe_leave(self.pytgcalls_client, self.current_vc_chat_id)
            except Exception:
                pass
            try:
                await self.pytgcalls_client.stop()
            except Exception:
                pass
            self.pytgcalls_client = None
            
        self.current_vc_chat_id = None
            
        sess_data = database.get_session(self.session_id)
        if sess_data:
            sess_data["status"] = "stopped"
            
            # Try to restore profile branding before disconnecting
            if self.client and self.client.is_connected():
                try:
                    await restore_branding(self.client, sess_data)
                except Exception as e:
                    logger.warning(f"Error restoring branding during stop: {e}")
                    
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting client: {e}")
        
        # Free the session for the next start.
        session_lock.release(self.session_id)
                
        # Now read session file and save to MongoDB after client is disconnected
        if sess_data:
            session_file = sess_data.get("session_file")
            if session_file and os.path.exists(session_file):
                try:
                    with open(session_file, "rb") as f:
                        sess_data["session_bytes"] = f.read()
                except Exception as read_err:
                    logger.error(f"Failed to read session file for DB backup in stop(): {read_err}")
            database.save_session(sess_data)
            
        import gc
        gc.collect()
                
        logger.info(f"Userbot {self.session_id} stopped.")

    def _register_handlers(self):
        @self.client.on(events.ChatAction())
        async def chat_action_handler(event):
            if not self.is_running:
                return
            try:
                # If this userbot joined or was added to a group/chat
                if event.user_joined or event.user_added:
                    me_id = getattr(self, "me_id", None)
                    if not me_id and self.client:
                        try:
                            me = await self.client.get_me()
                            me_id = getattr(me, "id", None)
                            self.me_id = me_id
                        except Exception:
                            pass
                    is_me = False
                    if event.user_id and event.user_id == me_id:
                        is_me = True
                    elif event.users and any(getattr(u, "id", None) == me_id for u in event.users):
                        is_me = True
                    elif getattr(event, "added_by", None) and getattr(event, "user_id", None) == me_id:
                        is_me = True

                    if is_me:
                        logger.info(f"Userbot {self.session_id} joined/added to new group {event.chat_id}! Resetting cache so next broadcast includes this group immediately.")
                        self.groups_cache_time = 0
                        await self.get_groups(force_refresh=True)
            except Exception as e:
                logger.debug(f"ChatAction error for userbot {self.session_id}: {e}")

        @self.client.on(events.NewMessage())
        async def message_handler(event):
            if not self.is_running:
                return
                
            # If a message is received in a group that isn't yet in groups_cache, mark cache expired so next broadcast includes it
            if event.is_group and self.groups_cache is not None:
                if event.chat_id not in [getattr(g, 'id', None) for g in self.groups_cache]:
                    self.groups_cache_time = 0

            # Group message handling (now allowed everywhere)
            if True:
                raw_text = (event.raw_text or "").strip()
                # Check for music/player commands (.play, /play, .vplay, .playforce, .skip, .queue, .song, etc.)
                match = re.match(r"(?i)^[./!?](play|vplay|cplay|stream|vstream|playforce|vplayforce|cplayforce|forceplay|vforceplay|skip|next|cskip|cnext|queue|q|playlist|cqueue|pause|resume|stop|end|mute|unmute|vc|joinvc|leavevc|vcleft|song|music|thumb|thumbnail)(?:@\w+)?(?:\s+([\s\S]*))?$", raw_text)
                if match:
                    sender_id = event.sender_id
                    sess = database.get_session(self.session_id)
                    owner_uid = sess.get("user_id") if sess else None
                    
                    me_id = getattr(self, "me_id", None)
                    if not me_id and self.client:
                        try:
                            me = await self.client.get_me()
                            me_id = getattr(me, "id", None)
                            self.me_id = me_id
                        except Exception:
                            pass
                            
                    global_settings = database.get_global_settings()
                    admins_list = global_settings.get("admins", [])
                    
                    is_authorized = (
                        bool(getattr(event, "out", False)) or 
                        (sender_id and owner_uid and str(sender_id) == str(owner_uid)) or 
                        (sender_id and me_id and sender_id == me_id) or 
                        (sender_id in config.ORIGINAL_ADMIN_IDS) or 
                        (sender_id in admins_list)
                    )
                    
                    cmd_str = match.group(1).lower()
                    
                    if not is_authorized and cmd_str not in ("song", "music"):
                        # Silently ignore commands from unauthorized users!
                        return

                    cmd = cmd_str
                    query = (match.group(2) or "").strip()
                    chat_id = event.chat_id
                    is_outgoing = bool(getattr(event, "out", False))
                    
                    # Deduplicate in case main bot also saw it
                    if utils.check_and_mark_command(chat_id, raw_text, msg_id=getattr(event, "id", 0)):
                        if cmd in ("play", "vplay", "cplay", "stream", "vstream", "playforce", "vplayforce", "cplayforce", "forceplay", "vforceplay"):
                            play_type = "video" if ("vplay" in cmd or "vstream" in cmd) else "audio"
                            is_force = any(k in cmd for k in ["force", "cplayforce"])
                            reply_msg = await event.get_reply_message() if event.is_reply else None
                            local_file_path = None
                            audio_title = None
                            audio_duration = 0
                            
                            from villain_ads_bot.handlers.player import _active_chat_players, _chat_queues, _track_timer_tasks, _preparing_chats, get_chat_lock, play_next_in_queue
                            
                            display_target = query or (audio_title if audio_title else 'replied media')
                            inquiry_text = f"<b>» 🎧 Playing :</b> <b>{display_target}</b>"
                            if is_outgoing:
                                try:
                                    await event.edit(inquiry_text)
                                    prog = event
                                except Exception:
                                    prog = await event.reply(inquiry_text)
                            else:
                                prog = await event.reply(inquiry_text)
                            
                            if reply_msg and (reply_msg.audio or reply_msg.video or reply_msg.voice or reply_msg.document):
                                media = reply_msg.audio or reply_msg.video or reply_msg.voice or reply_msg.document
                                if media:
                                    os.makedirs(config.DOWNLOADS_DIR, exist_ok=True)
                                    try:
                                        local_file_path = await self.client.download_media(reply_msg, file="downloads/")
                                    except Exception as dl_err:
                                        await prog.edit(
                                            utils.format_html_message(
                                                f"<blockquote><b>» ❌ Failed To Download Media</b>\n\n"
                                                f"⚠️ <b>Error :</b> <code>{dl_err}</code></blockquote>"
                                            )
                                        )
                                        return
                            elif not query:
                                await prog.edit(
                                    utils.format_html_message(
                                        f"<blockquote><b>» 💡 Music Player Usage Guide</b>\n\n"
                                        f"• <code>.{cmd} &lt;song name&gt;</code> (E.G. <code>.{cmd} Faded Alan Walker</code>)\n"
                                        f"• <code>/{cmd} &lt;song name&gt;</code>\n"
                                        f"• <code>.playforce &lt;song name&gt;</code> (Force Play Now)\n"
                                        f"• <code>.queue</code> / <code>.skip</code>\n"
                                        f"• <b>Thumbnail Mode :</b> <code>.thumb on</code> Or <code>.thumb off</code>\n"
                                        f"• Reply To Any Audio/Video File With <code>.{cmd}</code>\n\n"
                                        f"⚡ <i>Start Group Voice Chat Before Streaming.</i></blockquote>"
                                    )
                                )
                                return
                                
                            sender = await event.get_sender()
                            sender_name = getattr(sender, "first_name", "User") or "User"
                            requester_id = getattr(sender, "id", 0) or 0

                            # Fetch metadata if it's a query and not a local file
                            if query and not local_file_path:
                                try:
                                    t, m, s, thumb, vid = await YouTube.details(query)
                                    if t:
                                        audio_title = t
                                        try:
                                            await prog.edit(f"<b>» 🎧 Playing :</b> <b>{audio_title}</b>")
                                        except Exception:
                                            pass
                                    if s:
                                        audio_duration = s
                                except Exception as yt_err:
                                    logger.warning(f"Error fetching YouTube details in userbot: {yt_err}")
                                    
                            # If already playing, preparing, or queue has songs, and not force play -> Add to Queue
                            async with get_chat_lock(chat_id):
                                is_busy = (
                                    (chat_id in _active_chat_players and _active_chat_players[chat_id].get("bot")) or
                                    (chat_id in _preparing_chats) or
                                    (len(_chat_queues.get(chat_id, [])) > 0)
                                )
                                if is_busy and not is_force:
                                    if chat_id not in _chat_queues:
                                        _chat_queues[chat_id] = []
                                    _chat_queues[chat_id].append({
                                        "query": query,
                                        "play_type": play_type,
                                        "requester_name": sender_name,
                                        "requester_id": requester_id,
                                        "local_file": local_file_path,
                                        "title": audio_title or query,
                                        "duration": audio_duration,
                                        "thumb": None
                                    })
                                    pos = len(_chat_queues[chat_id])
                                    mins, secs = divmod(audio_duration or 0, 60)
                                    dur_str = f"{mins:02d}:{secs:02d}" if audio_duration else "03:00"
                                    mode_emoji = "🎬 Video" if play_type == "video" else "🎙️ Audio"
                                    
                                    await prog.edit(
                                        utils.format_html_message(
                                            f"<blockquote><b>» 📋 Added To Queue : #{pos}</b>\n\n"
                                            f"<b>📌 Title :</b> <b>{audio_title or query}</b>\n"
                                            f"<b>⏱️ Duration :</b> <code>{dur_str}</code>\n"
                                            f"<b>🎧 Mode :</b> <b>{mode_emoji}</b>\n"
                                            f"<b>👤 Requested By :</b> <a href=\"tg://user?id={requester_id}\">{sender_name}</a>\n\n"
                                            f"💡 <i>Track Will Play Automatically After Current Song Finishes.</i></blockquote>"
                                        )
                                    )
                                    return
                                else:
                                    _preparing_chats.add(chat_id)

                            try:
                                # Cancel previous timer
                                prev_timer = _track_timer_tasks.pop(chat_id, None)
                                if prev_timer and not prev_timer.done():
                                    prev_timer.cancel()
                                    
                                success, msg, song_info = await self.play_song(
                                    query=query,
                                    play_type=play_type,
                                    chat_id=chat_id,
                                    local_file=local_file_path,
                                    title=audio_title,
                                    duration=audio_duration
                                )
                                
                                if not success or not song_info:
                                    await prog.edit(
                                        utils.format_html_message(
                                            f"<blockquote><b>» ❌ Playback Failed</b>\n\n"
                                            f"⚠️ <b>Error :</b> <code>{msg}</code>\n\n"
                                            f"💡 <b>Tip :</b> Make Sure Group Voice Chat Is Started!</blockquote>"
                                        )
                                    )
                                    try:
                                        await asyncio.sleep(5)
                                        await prog.delete()
                                    except Exception:
                                        pass
                                    return
                            finally:
                                _preparing_chats.discard(chat_id)
                                
                            title = song_info.get("title", "Unknown Track")
                            duration = song_info.get("duration", 0)
                            mins, secs = divmod(duration, 60)
                            dur_str = f"{mins:02d}:{secs:02d}" if duration > 0 else "Live Stream"
                            mode_emoji = "🎬 Video Stream" if play_type == "video" else "🎙️ Audio Stream"
                            
                            np_text = utils.format_html_message(
                                f"<blockquote><b>» 🎵 Now Streaming</b>\n\n"
                                f"<b>📌 Title :</b> <b>{title}</b>\n"
                                f"<b>⏱️ Duration :</b> <code>{dur_str}</code>\n"
                                f"<b>🎧 Mode :</b> <b>{mode_emoji}</b>\n"
                                f"<b>👤 Requested By :</b> <a href=\"tg://user?id={requester_id}\">{sender_name}</a>\n"
                                f"<b>🤖 Stream Source :</b> <b>{self.name}</b>\n\n"
                                f"⚡ <i>Controls : .pause, .resume, .skip, .queue, .stop, .vc</i></blockquote>"
                            )
                            
                            # Remove the card of the previous song so the chat
                            # never accumulates stale player cards. The old
                            # session must be captured BEFORE it is replaced.
                            try:
                                from villain_ads_bot.handlers.player import _delete_card_by_session
                                stale = _active_chat_players.get(chat_id)
                                if stale:
                                    await _delete_card_by_session(stale, chat_id)
                            except Exception:
                                pass
                            
                            _active_chat_players[chat_id] = {
                                "bot": self,
                                "song_info": song_info,
                                "msg_id": None,
                                "is_paused": False,
                                "is_muted": False
                            }
                            
                            dur_track = song_info.get("duration", 30) or 30
                            async def ub_track_auto_timer():
                                await asyncio.sleep(dur_track + 2)
                                await play_next_in_queue(chat_id)
                            _track_timer_tasks[chat_id] = asyncio.create_task(ub_track_auto_timer())
                            
                            thumb_enabled = database.get_thumbnail_setting(chat_id)
                            thumb_file = song_info.get("thumb") if thumb_enabled else None
                            _tf_valid = bool(thumb_file) and (str(thumb_file).startswith("http") or (os.path.exists(str(thumb_file)) and os.path.getsize(str(thumb_file)) > 0))
                            
                            from villain_ads_bot.handlers.player import _main_bot, build_now_playing_markup
                            buttons = build_now_playing_markup(chat_id, is_paused=False, is_muted=False)
                            
                            sent_msg = None
                            if _main_bot:
                                try:
                                    await prog.delete()
                                except Exception:
                                    pass
                                try:
                                    if thumb_enabled and _tf_valid:
                                        sent_msg = await _main_bot.send_message(chat_id, np_text, file=thumb_file, buttons=buttons)
                                    else:
                                        sent_msg = await _main_bot.send_message(chat_id, np_text, buttons=buttons)
                                except Exception as e:
                                    if thumb_enabled and _tf_valid:
                                        sent_msg = await event.respond(np_text, file=thumb_file)
                                    else:
                                        sent_msg = await event.respond(np_text)
                            else:
                                if thumb_enabled and _tf_valid:
                                    try:
                                        await prog.delete()
                                    except Exception:
                                        pass
                                    sent_msg = await event.respond(np_text, file=thumb_file)
                                else:
                                    try:
                                        sent_msg = await prog.edit(np_text)
                                    except Exception:
                                        sent_msg = await event.respond(np_text)
                                        
                            if sent_msg and hasattr(sent_msg, "id") and chat_id in _active_chat_players:
                                _active_chat_players[chat_id]["msg_id"] = sent_msg.id
                            return

                        elif cmd in ("skip", "next", "cskip", "cnext"):
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            from villain_ads_bot.handlers.player import _chat_queues, _active_chat_players, _track_timer_tasks, play_next_in_queue
                            
                            old_p = _active_chat_players.get(chat_id)
                            if old_p and old_p.get("msg_id"):
                                try:
                                    from villain_ads_bot.handlers.player import _main_bot
                                    if _main_bot:
                                        await _main_bot.delete_messages(chat_id, old_p["msg_id"])
                                    else:
                                        await self.client.delete_messages(chat_id, old_p["msg_id"])
                                except Exception:
                                    pass
                                    
                            queue_list = _chat_queues.get(chat_id, [])
                            prev_timer = _track_timer_tasks.pop(chat_id, None)
                            if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
                                prev_timer.cancel()

                            if not queue_list:
                                await self.stop_song(chat_id)
                                prog = await event.respond(
                                    utils.format_html_message(
                                        "<blockquote><b>» ⏭️ Queue Empty</b>\n\n"
                                        "No More Songs In Queue. Stream Has Been Stopped.</blockquote>"
                                    )
                                )
                                await asyncio.sleep(3)
                                try:
                                    await prog.delete()
                                except Exception:
                                    pass
                                return

                            prog = await event.respond(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏭️ Skipping Track</b>\n\n"
                                    "Skipping To Next Song In Queue...</blockquote>"
                                )
                            )
                            await play_next_in_queue(chat_id)
                            try:
                                await prog.delete()
                            except Exception:
                                pass
                            return

                        elif cmd in ("queue", "q", "playlist", "cqueue"):
                            from villain_ads_bot.handlers.player import _active_chat_players, _chat_queues
                            active = _active_chat_players.get(chat_id)
                            queue = _chat_queues.get(chat_id, [])
                            
                            if not active and not queue:
                                prog = await event.reply(
                                    utils.format_html_message(
                                        "<blockquote><b>» 📜 Queue Playlist</b>\n\n"
                                        "<i>Queue Is Currently Empty. Use <code>.play &lt;song&gt;</code> To Add Tracks!</i></blockquote>"
                                    )
                                )
                                return
                                
                            np_info = active.get("song_info", {}) if active else {}
                            np_title = np_info.get("title", "Unknown Track")
                            np_dur = np_info.get("duration", 0)
                            mins, secs = divmod(np_dur, 60)
                            np_dur_str = f"{mins:02d}:{secs:02d}" if np_dur else "Live"
                            
                            text = (
                                "<blockquote><b>» 📜 Queue Playlist</b>\n\n"
                                f"🎵 <b>Now Playing :</b>\n"
                                f"• <b>{np_title}</b> (<code>{np_dur_str}</code>)\n\n"
                            )
                            
                            if queue:
                                text += "📋 <b>Upcoming Queue :</b>\n"
                                for idx, item in enumerate(queue[:10], 1):
                                    item_title = item.get("title") or item.get("query") or "Unknown"
                                    item_dur = item.get("duration", 0)
                                    m, s = divmod(item_dur, 60)
                                    d_str = f"{m:02d}:{s:02d}" if item_dur else "03:00"
                                    req = item.get("requester_name", "User")
                                    text += f"{idx}. <b>{item_title}</b> (<code>{d_str}</code>) | <i>{req}</i>\n"
                                    
                                if len(queue) > 10:
                                    text += f"\n<i>...And {len(queue) - 10} More Tracks</i>\n"
                            else:
                                text += "📋 <b>Upcoming Queue :</b>\n<i>No Tracks In Queue.</i>\n"
                                
                            text += "\n💡 <i>Use <code>.skip</code> To Play Next Track.</i></blockquote>"
                            prog = await event.reply(utils.format_html_message(text))
                            return

                        elif cmd in ("song", "music"):
                            if not query:
                                prog = await event.reply(
                                    utils.format_html_message(
                                        f"<blockquote><b>» 💡 Song Download Usage</b>\n\n"
                                        f"• <code>.{cmd} &lt;song name or youtube link&gt;</code></blockquote>"
                                    )
                                )
                                return
                                
                            prog = await event.reply(
                                utils.format_html_message(
                                    f"<blockquote><b>» 📥 Downloading Song</b>\n\n"
                                    f"⏳ <i>Searching And Downloading <code>{query}</code> From Youtube...</i></blockquote>"
                                )
                            )
                            file_path, title, duration, thumb = await download_media(query, download_type="audio")
                            if not file_path or not os.path.exists(file_path):
                                await prog.edit(
                                    utils.format_html_message(
                                        f"<blockquote><b>» ❌ Download Failed</b>\n\n"
                                        f"⚠️ Failed To Download Audio From Youtube.</blockquote>"
                                    )
                                )
                                return
                                
                            mins, secs = divmod(duration or 0, 60)
                            dur_str = f"{mins:02d}:{secs:02d}" if duration else "03:00"
                            caption = utils.format_html_message(
                                f"<blockquote><b>» 🎵 Downloaded Track</b>\n\n"
                                f"<b>📌 Title :</b> <b>{title or query}</b>\n"
                                f"<b>⏱️ Duration :</b> <code>{dur_str}</code>\n"
                                f"<b>🤖 Downloaded By :</b> <b>{self.name}</b></blockquote>"
                            )
                            try:
                                dur_int = int(duration or 0)
                                audio_attr = types.DocumentAttributeAudio(
                                    duration=dur_int,
                                    title=str(title or query),
                                    performer="Villain Music"
                                ) if hasattr(types, "DocumentAttributeAudio") else None
                                
                                await self.client.send_file(
                                    chat_id,
                                    file_path,
                                    caption=caption,
                                    thumb=thumb if (thumb and os.path.exists(thumb)) else None,
                                    attributes=[audio_attr] if audio_attr else None
                                )
                                await prog.delete()
                            except Exception as up_err:
                                await prog.edit(
                                    utils.format_html_message(
                                        f"<blockquote><b>» ❌ Upload Failed</b>\n\n"
                                        f"⚠️ <code>{up_err}</code></blockquote>"
                                    )
                                )
                            return
                            
                        elif cmd == "pause":
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            prog = await event.respond(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏳ Pausing Stream</b>\n\nPausing Voice Chat Stream...</blockquote>"
                                )
                            )
                            try:
                                pytg = await self.get_pytgcalls()
                                await pytg.pause_stream(chat_id)
                                await prog.edit(
                                    utils.format_html_message(
                                        "<blockquote><b>» ⏸️ Playback Paused</b>\n\n"
                                        "Stream Is Paused. Use <code>.resume</code> Or <code>/resume</code> To Continue Playing.</blockquote>"
                                    )
                                )
                            except Exception as e:
                                await prog.edit(utils.format_html_message(f"<blockquote><b>» ❌ Error Pausing</b>\n\n⚠️ <code>{e}</code></blockquote>"))
                            return
                            
                        elif cmd == "resume":
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            prog = await event.respond(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏳ Resuming Stream</b>\n\nResuming Voice Chat Stream...</blockquote>"
                                )
                            )
                            try:
                                pytg = await self.get_pytgcalls()
                                await pytg.resume_stream(chat_id)
                                await prog.edit(
                                    utils.format_html_message(
                                        "<blockquote><b>» ▶️ Playback Resumed</b>\n\nStream Resumed Successfully.</blockquote>"
                                    )
                                )
                            except Exception as e:
                                await prog.edit(utils.format_html_message(f"<blockquote><b>» ❌ Error Resuming</b>\n\n⚠️ <code>{e}</code></blockquote>"))
                            return
                            
                        elif cmd in ("stop", "end"):
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            from villain_ads_bot.handlers.player import _chat_queues, _active_chat_players, _track_timer_tasks
                            
                            old_p = _active_chat_players.get(chat_id)
                            if old_p and old_p.get("msg_id"):
                                try:
                                    from villain_ads_bot.handlers.player import _main_bot
                                    if _main_bot:
                                        await _main_bot.delete_messages(chat_id, old_p["msg_id"])
                                    else:
                                        await self.client.delete_messages(chat_id, old_p["msg_id"])
                                except Exception:
                                    pass
                                    
                            _chat_queues.pop(chat_id, None)
                            _active_chat_players.pop(chat_id, None)
                            prev_timer = _track_timer_tasks.pop(chat_id, None)
                            if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
                                prev_timer.cancel()

                            prog = await event.respond(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏳ Stopping Stream</b>\n\nStopping Voice Chat Stream...</blockquote>"
                                )
                            )
                            success, msg = await self.stop_song(chat_id)
                            if success:
                                await prog.edit(
                                    utils.format_html_message(
                                        "<blockquote><b>» ⏹️ Playback Stopped</b>\n\nVoice Chat Stream Has Been Stopped & Queue Cleared.</blockquote>"
                                    )
                                )
                                await asyncio.sleep(3)
                                try:
                                    await prog.delete()
                                except Exception:
                                    pass
                            else:
                                await prog.edit(utils.format_html_message(f"<blockquote><b>» ❌ Failed To Stop</b>\n\n⚠️ <code>{msg}</code></blockquote>"))
                            return
                            
                        elif cmd == "mute":
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            prog = await event.respond(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏳ Muting Mic</b>\n\nMuting Userbot In Voice Chat...</blockquote>"
                                )
                            )
                            success, msg = await self.mute_mic(chat_id)
                            await prog.edit(
                                utils.format_html_message(
                                    "<blockquote><b>» 🔇 Mic Muted</b>\n\nUserbot Microphone Has Been Turned <b>Off</b>.</blockquote>"
                                )
                            )
                            return
                            
                        elif cmd == "unmute":
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            prog = await event.respond(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏳ Unmuting Mic</b>\n\nUnmuting Userbot In Voice Chat...</blockquote>"
                                )
                            )
                            success, msg = await self.unmute_mic(chat_id)
                            await prog.edit(
                                utils.format_html_message(
                                    "<blockquote><b>» 🔊 Mic Unmuted</b>\n\nUserbot Microphone Has Been Turned <b>On</b>.</blockquote>"
                                )
                            )
                            return
                            
                        elif cmd in ("vc", "joinvc"):
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            prog = await event.respond(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏳ Joining Voice Chat</b>\n\nConnecting Userbot To Group Voice Chat...</blockquote>"
                                )
                            )
                            success, msg = await self.join_voice_chat(str(chat_id))
                            if success:
                                await prog.edit(
                                    utils.format_html_message(
                                        "<blockquote><b>» 🎙️ Connected To Voice Chat</b>\n\n"
                                        "Userbot Successfully Connected To Voice Chat!\n\n"
                                        "💡 <i>Use <code>.play &lt;song&gt;</code> Or <code>.vplay &lt;video&gt;</code> To Stream Media.</i></blockquote>"
                                    )
                                )
                            else:
                                await prog.edit(
                                    utils.format_html_message(
                                        f"<blockquote><b>» ❌ Failed To Join Vc</b>\n\n⚠️ <code>{msg}</code></blockquote>"
                                    )
                                )
                            return
                            
                        elif cmd in ("leavevc", "vcleft"):
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            prog = await event.respond(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏳ Leaving Voice Chat</b>\n\nDisconnecting Userbot From Voice Chat...</blockquote>"
                                )
                            )
                            success, msg = await self.leave_voice_chat(chat_id)
                            if success:
                                await prog.edit(
                                    utils.format_html_message(
                                        "<blockquote><b>» 👋 Left Voice Chat</b>\n\nUserbot Has Disconnected From The Voice Chat.</blockquote>"
                                    )
                                )
                            else:
                                await prog.edit(utils.format_html_message(f"<blockquote><b>» ❌ Failed To Leave Vc</b>\n\n⚠️ <code>{msg}</code></blockquote>"))
                            return
                            
                        elif cmd in ("thumb", "thumbnail"):
                            prog = await event.reply(
                                utils.format_html_message(
                                    "<blockquote><b>» ⏳ Updating Settings</b>\n\nUpdating Thumbnail Configuration...</blockquote>"
                                )
                            )
                            current = database.get_thumbnail_setting(chat_id)
                            if query and query.lower() in ("on", "enable", "true"):
                                new_state = True
                            elif query and query.lower() in ("off", "disable", "false"):
                                new_state = False
                            else:
                                new_state = not current
                                
                            database.set_thumbnail_setting(chat_id, new_state)
                            status_text = "🟢 <b>Enabled</b>" if new_state else "🔴 <b>Disabled</b>"
                            mode_desc = "Artwork Thumbnail Banner Will Be Displayed." if new_state else "Clean Text-Only Mode Active (No Thumbnail)."
                            
                            await prog.edit(
                                utils.format_html_message(
                                    f"<blockquote><b>» 🖼️ Thumbnail Setting</b>\n\n"
                                    f"<b>• Status :</b> {status_text}\n"
                                    f"<b>• Display Mode :</b> <i>{mode_desc}</i></blockquote>"
                                )
                            )
                            return

                # Ignore outgoing messages to prevent self-reply loops
                if getattr(event, "out", False):
                    return

                # 2. Group / Channel message handling (Auto-Contact)
                if event.is_group or event.is_channel:
                    is_reply_to_us = False
                    if event.is_reply:
                        try:
                            reply_msg = await event.get_reply_message()
                            if reply_msg:
                                if self.me_id and reply_msg.sender_id == self.me_id:
                                    is_reply_to_us = True
                                elif hasattr(reply_msg, "from_id") and getattr(reply_msg.from_id, "user_id", None) == self.me_id:
                                    is_reply_to_us = True
                        except Exception:
                            pass

                    is_tagged = bool(getattr(event, "mentioned", False)) or is_reply_to_us

                    # Auto-Add Contact on Mention/Reply
                    if is_tagged and self.settings.get("auto_add_contact"):
                        sender = await event.get_sender()
                        if sender and not getattr(sender, "bot", False):
                            try:
                                from telethon.tl.functions.contacts import AddContactRequest
                                fname = sender.first_name or "Contact"
                                lname = sender.last_name or ""
                                await self.client(AddContactRequest(
                                    id=sender.id,
                                    first_name=fname,
                                    last_name=lname,
                                    phone="",
                                    add_phone_privacy_exception=True
                                ))
                                logger.info(f"Auto-added contact: {sender.id} ({fname} {lname}) on mention/reply in chat {event.chat_id}")
                            except Exception as add_err:
                                logger.warning(f"Failed to auto-add contact {sender.id}: {add_err}")

                # 3. Tag Auto-Reply (Works in Groups, Channels, and DMs)
                is_reply_target = False

                # Ensure me_id and username are known
                if not self.me_id and self.client:
                    try:
                        me = await self.client.get_me()
                        if me:
                            self.me_id = me.id
                            if me.username:
                                self.username = me.username
                    except Exception:
                        pass

                if event.is_group or event.is_channel:
                    is_reply_to_us = False
                    if event.is_reply:
                        try:
                            reply_msg = await event.get_reply_message()
                            if reply_msg:
                                if self.me_id and reply_msg.sender_id == self.me_id:
                                    is_reply_to_us = True
                                elif hasattr(reply_msg, "from_id") and getattr(reply_msg.from_id, "user_id", None) == self.me_id:
                                    is_reply_to_us = True
                        except Exception:
                            pass

                    is_mentioned = bool(getattr(event, "mentioned", False))
                    raw_text_lower = (event.raw_text or "").lower()
                    if self.username and f"@{self.username.lower()}" in raw_text_lower:
                        is_mentioned = True
                    if self.me_id and f"tg://user?id={self.me_id}" in raw_text_lower:
                        is_mentioned = True
                    if getattr(event, "entities", None):
                        try:
                            from telethon.tl import types as tl_types
                            for ent in event.entities:
                                if isinstance(ent, (tl_types.MessageEntityMentionName, tl_types.InputMessageEntityMentionName)):
                                    if getattr(ent, "user_id", None) == self.me_id:
                                        is_mentioned = True
                                        break
                        except Exception:
                            pass

                    is_reply_target = is_mentioned or is_reply_to_us

                # Fetch latest settings from DB to prevent stale in-memory state
                sess_data = database.get_session(self.session_id)
                current_settings = sess_data.get("settings", {}) if sess_data else self.settings
                if sess_data and "settings" in sess_data:
                    self.settings = sess_data["settings"]

                auto_reply_enabled = bool(current_settings.get("auto_reply") or current_settings.get("tag_reply"))

                if is_reply_target and auto_reply_enabled:
                    now = time.time()
                    last_reply_time = self.tag_cooldown.get(event.chat_id, 0)

                    # Prune the cooldown map so it cannot grow forever.
                    if len(self.tag_cooldown) > 500:
                        cutoff = now - 1800
                        self.tag_cooldown = {
                            cid: ts for cid, ts in self.tag_cooldown.items() if ts > cutoff
                        }

                    # A GLOBAL per account limiter. Without it one mention in
                    # 100 different groups produced 100 replies, which is
                    # exactly the pattern that gets a userbot banned.
                    last_global = getattr(self, "_last_reply_ts", 0.0)
                    global_gap = float(current_settings.get("auto_reply_global_gap", 15) or 15)
                    global_ok = (now - last_global) >= global_gap

                    # 3-second per-chat cooldown to prevent spambot limits while remaining responsive
                    if now - last_reply_time >= 3.0 and global_ok:
                        DEFAULT_REPLIES = [
                            "Hello! How can I help you? 😊",
                            "Hey there! Thanks for reaching out. Please leave a message! 💬",
                            "Hello, I am currently away, will get back to you soon. ✨"
                        ]

                        # The message list decides the mode - the user never
                        # has to remember to switch "multiple" on.
                        candidates = utils.get_auto_reply_messages(current_settings)
                        if not candidates:
                            candidates = DEFAULT_REPLIES
                        ar_mode = "multiple" if len(candidates) > 1 else "single"
                        msgs_to_send = [random.choice(candidates)]
                        for selected_reply in msgs_to_send:
                            processed_reply = antispam.vary_message(
                                selected_reply, group_id=event.chat_id, tag=False)
                            processed_reply = utils.normalize_text(processed_reply)

                            # The cooldown MUST be written BEFORE the sleep.
                            # Before the fix it was written afterwards, so every
                            # mention that arrived during the 10 second sleep
                            # also produced a reply.
                            self.tag_cooldown[event.chat_id] = now
                            self._last_reply_ts = now

                            try:
                                # Safe humanized delay between 1.5s to 2.5s (responsive and non-blocking)
                                await asyncio.sleep(random.uniform(1.5, 2.5))
                                # The userbot may have been stopped meanwhile.
                                if not self.is_running:
                                    logger.info(
                                        f"Auto-reply skipped in chat {event.chat_id} - "
                                        f"the userbot was stopped."
                                    )
                                    continue
                                try:
                                    await event.reply(processed_reply, parse_mode='html')
                                except Exception:
                                    # Fallback to plain text if HTML tags are malformed
                                    await event.reply(processed_reply, parse_mode=None)
                                logger.info(f"Auto-replied ({ar_mode} mode) in chat {event.chat_id} for userbot {self.session_id}")
                            except Exception as reply_err:
                                logger.warning(f"Could not send auto-reply in chat {event.chat_id}: {reply_err}")

                # 4. Private message handling (Auto-Welcome)
                if event.is_private:
                    if not sess_data:
                        sess_data = database.get_session(self.session_id)
                    current_settings = sess_data.get("settings", {}) if sess_data else self.settings
                    
                    if current_settings.get("auto_welcome"):
                        sender = await event.get_sender()
                        if sender and not getattr(sender, "bot", False) and not getattr(sender, "is_self", False):
                            welcomed_users = sess_data.setdefault("stats", {}).setdefault("welcomed_users", [])
                            if sender.id not in welcomed_users:
                                # The message list decides the mode.
                                messages_to_send = utils.get_welcome_messages(current_settings)
                                if messages_to_send:
                                    welcome_sent = False
                                    for welcome_msg in messages_to_send:
                                        processed_welcome = antispam.vary_message(
                                            welcome_msg, group_id=None, tag=False)
                                        processed_welcome = utils.normalize_text(processed_welcome)
                                        try:
                                            await asyncio.sleep(random.uniform(0.5, 1.5))
                                            # The userbot may have been stopped meanwhile.
                                            if not self.is_running:
                                                logger.info(
                                                    f"Welcome skipped for {sender.id} - "
                                                    f"the userbot was stopped."
                                                )
                                                break
                                            try:
                                                await event.reply(processed_welcome, parse_mode='html')
                                            except Exception:
                                                await event.reply(processed_welcome, parse_mode=None)
                                            logger.info(f"Sent welcome message to {sender.id} from userbot {self.session_id}")
                                            welcome_sent = True
                                        except Exception as e:
                                            logger.warning(f"Could not send welcome message to {sender.id}: {e}")
                                    if welcome_sent:
                                        welcomed_users.append(sender.id)
                                        try:
                                            database.save_session(sess_data)
                                        except Exception as db_save_err:
                                            logger.debug(f"Failed to save welcomed_user to DB: {db_save_err}")

    async def broadcast_round(self, progress_msg=None, delay: float = None,
                              force: bool = False) -> dict:
        """
        Sends ONE broadcast round to every group / channel of this userbot.

        * Flood waits are respected: the round continues automatically after
          the requested wait time.
        * The progress message is updated live with the amount of sent
          messages, the remaining ones and the estimated time left.
        * Returns the counters of the round.
        """
        import time as _time

        stats = {
            "total": 0, "sent": 0, "failed": 0, "skipped": 0,
            "flood_waits": 0, "wait_total": 0, "elapsed": 0,
        }
        # 1.3 - only one round per account at a time.
        if self._broadcast_lock.locked():
            logger.info(
                f"A broadcast round is already running for {self.session_id} - skipping."
            )
            stats["skipped"] = -1
            return stats
        try:
            await asyncio.wait_for(self._broadcast_lock.acquire(), timeout=2.0)
        except asyncio.TimeoutError:
            logger.info(f"Could not acquire the broadcast lock for {self.session_id}.")
            stats["skipped"] = -1
            return stats
        try:
            # Read the settings DIRECTLY from MongoDB. The cached document can
            # be up to 60 seconds old, so a message change was sometimes not
            # picked up at all.
            settings = self.settings
            try:
                fresh = database.get_session_fresh(self.session_id)
                if fresh and "settings" in fresh:
                    settings = fresh["settings"]
                    self.settings = fresh["settings"]
            except Exception:
                pass

            if delay is None:
                delay = utils.get_inter_group_delay(settings)

            # The message list decides the mode - the user never has to
            # remember to switch "multiple" on.
            messages = utils.get_broadcast_messages(settings)
            # The default placeholder is NOT a configured message.
            messages = [m for m in messages
                        if m and m != DEFAULT_BROADCAST_MESSAGE]
            if not messages:
                return stats
            force_refresh = (_time.time() - self.groups_cache_time > 60) or not self.groups_cache
            groups = await self.get_groups(force_refresh=force_refresh)
            stats["total"] = len(groups)
            if not groups:
                return stats

            # 3.1 The anti spam protection only WARNS - it never blocks a
            #     broadcast. The owner pays for the slots and decides
            #     himself how much to send.
            sess_for_budget = database.get_session(self.session_id) or {}
            stats["budget"] = antispam.budget_state(sess_for_budget)
            advice_text = antispam_guard.advice(sess_for_budget)
            if advice_text:
                stats["advice"] = advice_text
                logger.info(f"Broadcast advice for {self.session_id}: {advice_text}")
            stats["budget"] = antispam.budget_state(sess_for_budget)

            started = _time.time()
            for index, g in enumerate(groups, 1):
                if not self.is_running:
                    break
                # A manual broadcast (force) always runs, even when the
                # automatic broadcast is switched off.
                if not force and not settings.get("auto_spam") and progress_msg is None:
                    break

                current_msg = random.choice(messages)
                # Send the exact message configured by user.
                # Only apply spintax/tags/emoji if explicitly configured in settings.
                processed_msg = antispam.vary_message(
                    current_msg,
                    group_id=getattr(g, "id", None),
                    tag=bool(settings.get("unique_group_tag", False)),
                    emoji=bool(settings.get("rotate_emoji", False)),
                )
                processed_msg = utils.normalize_text(processed_msg)

                try:
                    try:
                        async for _ in self.client.iter_messages(g.input_entity, limit=2):
                            pass
                    except Exception:
                        pass

                    try:
                        async with self.client.action(g.input_entity, 'typing'):
                            await asyncio.sleep(random.uniform(1.5, 3.5))
                    except Exception:
                        pass

                    # The message is sent until it really goes through. A
                    # flood wait is waited out and the SAME group is retried
                    # (before it was silently dropped and never counted).
                    attempt = 0
                    while True:
                        attempt += 1
                        try:
                            await self.client.send_message(
                                g.input_entity, processed_msg, parse_mode='html')
                            break
                        except MessageEntityParsingError:
                            # A "<" or "&" in the text broke the HTML. Send it
                            # as plain text instead of losing the message.
                            logger.warning(
                                f"Broadcast message has invalid HTML for {self.session_id} "
                                f"- sending it as plain text."
                            )
                            await self.client.send_message(g.input_entity, processed_msg, parse_mode=None)
                            break
                        except (FloodWaitError, SlowModeWaitError) as wait_exc:
                            if attempt >= 3:
                                raise
                            wait_time = int(getattr(wait_exc, "seconds", 0) or 0)
                            if wait_time <= 0:
                                await asyncio.sleep(10)
                                continue
                            wait_time = min(wait_time + 5, 900)
                            stats["flood_waits"] += 1
                            stats["wait_total"] += wait_time
                            logger.info(
                                f"FloodWaitError on userbot {self.session_id}: waiting {wait_time}s "
                                f"({index}/{len(groups)} done) - retrying the same group."
                            )
                            if progress_msg is not None:
                                try:
                                    await progress_msg.edit(
                                        f"⏸️ <b>Flood Limit Hit</b>\n\n"
                                        f"✅ <b>Sent :</b> {stats['sent']} / {stats['total']}\n"
                                        f"⏳️ <b>Remaining :</b> {max(0, stats['total'] - index + 1)}\n"
                                        f"⏱️ <b>Waiting :</b> {wait_time}s\n\n"
                                        f"<i>The remaining groups are broadcast automatically afterwards.</i>",
                                        parse_mode="html"
                                    )
                                except Exception:
                                    pass
                            await asyncio.sleep(wait_time)
                    stats["sent"] += 1
                    # Count it (only informative - it never blocks anything).
                    try:
                        _sess = database.get_session(self.session_id)
                        if _sess:
                            _stats = antispam_guard.record(_sess)
                            _sess.setdefault("stats", {}).update(_stats)
                            database.save_session(_sess)
                    except Exception:
                        pass
                except PeerFloodError:
                    # The account is restricted - stop the round but keep the
                    # setting so the owner can decide.
                    logger.warning(
                        f"PeerFloodError on userbot {self.session_id}! Telegram restricted messaging."
                    )
                    self.settings["auto_spam"] = False
                    sess_data = database.get_session(self.session_id)
                    if sess_data:
                        sess_data.setdefault("settings", {})["auto_spam"] = False
                        sess_data["last_error"] = (
                            "Telegram restricted messaging (PeerFloodError). "
                            "Auto Broadcast paused to protect your account."
                        )
                        database.save_session(sess_data)
                    stats["skipped"] = len(groups) - index + 1
                    break
                except FloodWaitError as fwe:
                    # The inner retry loop already waited three times - count
                    # the group as failed instead of losing it silently.
                    wait_time = int(getattr(fwe, "seconds", 0) or 0)
                    logger.info(
                        f"FloodWaitError on userbot {self.session_id} for group {index}: "
                        f"giving up after 3 attempts (waited {wait_time}s)."
                    )
                    stats["failed"] += 1
                except SlowModeWaitError as smwe:
                    await asyncio.sleep(min(10, smwe.seconds))
                    stats["failed"] += 1
                except (UserBannedInChannelError, ChatWriteForbiddenError, ChannelPrivateError,
                        ChannelInvalidError, ChatIdInvalidError, UserNotParticipantError,
                        ValueError):
                    stats["failed"] += 1
                    # Do not retry a chat the account can never post in on
                    # every round - drop it from the cache.
                    try:
                        self.groups_cache = [
                            gg for gg in (self.groups_cache or [])
                            if getattr(gg, "id", None) != getattr(g, "id", None)
                        ]
                    except Exception:
                        pass
                except Exception as e:
                    err_class = e.__class__.__name__
                    if err_class in ("UserDeactivatedError", "AuthKeyUnregisteredError",
                                     "SessionRevokedError", "SessionExpiredError"):
                        logger.error(f"Broadcast failed with auth error: {e}. Cleaning up.")
                        await self._mark_unauthorized_and_cleanup()
                        break
                    stats["failed"] += 1
                    logger.debug(f"Failed to send broadcast message to group {g.id}: {e}")

                # Live progress
                if progress_msg is not None:
                    stats["elapsed"] = int(_time.time() - started)
                    remaining = max(0, stats["total"] - index)
                    eta = int(remaining * delay)
                    try:
                        await progress_msg.edit(
                            f"📢 <b>Broadcasting...</b> <code>({index}/{stats['total']})</code>\n\n"
                            f"✅ <b>Sent :</b> {stats['sent']}\n"
                            f"❌ <b>Failed :</b> {stats['failed']}\n"
                            f"⏳ <b>Remaining :</b> {remaining}\n"
                            f"⏱️ <b>Elapsed :</b> {stats['elapsed']}s"
                            + (f" | <b>~ETA :</b> {eta}s" if eta else "")
                            + (f"\n⏸️ <b>Flood waits :</b> {stats['flood_waits']}" if stats["flood_waits"] else ""),
                            parse_mode="html"
                        )
                    except Exception:
                        pass

                if index < stats["total"]:
                    await asyncio.sleep(delay)

            stats["elapsed"] = int(_time.time() - started)
        except Exception as e:
            logger.error(f"Error inside the broadcast round for {self.session_id}: {e}")
            utils.report_error(f"broadcast_round:{self.session_id}", e)

        # Persist the round statistics so the dashboard can show them.
        try:
            sess_data = database.get_session(self.session_id)
            if sess_data:
                # Only a round that really delivered something counts as a
                # broadcast - otherwise the counter was pure noise.
                if stats.get("sent", 0) > 0:
                    sess_data.setdefault("stats", {})["broadcast_count"] = (
                        sess_data["stats"].get("broadcast_count", 0) + 1
                    )
                sess_data["stats"]["last_broadcast"] = {
                    "total": stats["total"], "sent": stats["sent"],
                    "failed": stats["failed"], "elapsed": stats["elapsed"],
                    "flood_waits": stats["flood_waits"],
                    "at": time.time(),
                }
                database.save_session(sess_data)
        except Exception:
            pass
        finally:
            # Always release the broadcast lock.
            try:
                if self._broadcast_lock.locked():
                    self._broadcast_lock.release()
            except Exception:
                pass

        return stats

    async def broadcast_loop(self):
        """
        Periodically broadcasts the configured message to all group dialogs.
        Includes advanced anti-spam error handling and humanized delay timing.
        """
        while self.is_running:
            try:
                # Always read the freshest settings from MongoDB so changes made
                # from the dashboard apply immediately (even if the reload
                # bridge was not triggered).
                try:
                    fresh = database.get_session(self.session_id)
                    if fresh and "settings" in fresh:
                        self.settings = fresh["settings"]
                except Exception as sync_err:
                    logger.debug(f"Broadcast settings sync failed for {self.session_id}: {sync_err}")

                if self.settings.get("auto_spam"):
                    # One full round through every group / channel. Flood
                    # limits are handled inside broadcast_round().
                    await self.broadcast_round()
                    
                # Fetch broadcast interval from in-memory settings (default 300s)
                interval = utils.get_broadcast_interval(self.settings)
                    
                # Sleep in chunks to allow graceful termination
                for _ in range(max(1, interval)):
                    if not self.is_running:
                        break
                    await asyncio.sleep(1.0)

                
            except Exception as loop_e:
                logger.error(f"Critical error in broadcast loop for {self.session_id}: {loop_e}", exc_info=True)
                await asyncio.sleep(5)
