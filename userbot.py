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
import config
import database
import utils

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
from youtubesearchpython import VideosSearch, Playlist
import aiohttp
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, VideoQuality
import config

API_URL = getattr(config, "MEOW_API_URL", os.environ.get("MEOW_API_URL", "https://music.yukiapi.site"))
API_KEY = getattr(config, "MEOW_API_KEY", os.environ.get("MEOW_API_KEY", "yuki_238df692826dd11efbf4c4e3a4dac141"))


DOWNLOAD_DIR = "downloads"


def time_to_seconds(time):
    stringt = str(time)
    return sum(int(x) * 60 ** i for i, x in enumerate(reversed(stringt.split(":"))))


async def download_song(link: str) -> str:
    video_id = link.split("v=")[-1].split("&")[0] if "v=" in link else (link.split("youtu.be/")[-1].split("?")[0] if "youtu.be/" in link else link)
    if not video_id or len(video_id) < 3:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")

    if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
        return file_path

    try:
        async with aiohttp.ClientSession() as session:
            stream_url = f"{API_URL}/stream/{video_id}?key={API_KEY}&type=audio&quality=128"
            async with session.get(stream_url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status != 200:
                    return None
                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(131072):
                        f.write(chunk)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
            return file_path
        return None
    except Exception:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        return None


async def download_video(link: str) -> str:
    video_id = link.split("v=")[-1].split("&")[0] if "v=" in link else (link.split("youtu.be/")[-1].split("?")[0] if "youtu.be/" in link else link)
    if not video_id or len(video_id) < 3:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")

    if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
        return file_path

    try:
        async with aiohttp.ClientSession() as session:
            stream_url = f"{API_URL}/stream/{video_id}?key={API_KEY}&type=video&quality=480"
            async with session.get(stream_url, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                if resp.status != 200:
                    return None
                with open(file_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(131072):
                        f.write(chunk)

        if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
            return file_path
        return None
    except Exception:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        return None


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if hasattr(message_1, "reply_to_message") and message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        for message in messages:
            if not message:
                continue
            if getattr(message, "entities", None):
                for entity in message.entities:
                    if MessageEntityType and entity.type == MessageEntityType.URL:
                        text = message.text or message.caption or ""
                        return text[entity.offset: entity.offset + entity.length]
            elif getattr(message, "caption_entities", None):
                for entity in message.caption_entities:
                    if MessageEntityType and entity.type == MessageEntityType.TEXT_LINK:
                        return getattr(entity, "url", None)
        return None

    @staticmethod
    def _sync_search(link: str, limit: int = 1):
        try:
            s = VideosSearch(link, limit=limit)
            return s.result()
        except Exception as e:
            logger.error(f"VideosSearch error: {e}")
            return {}

    @staticmethod
    def _sync_details_fallback(query_str: str):
        try:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': True,
                'skip_download': True,
                'geo_bypass': True,
                'nocheckcertificate': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                q = query_str if query_str.startswith("http") else f"ytsearch1:{query_str}"
                info = ydl.extract_info(q, download=False)
                if info:
                    entry = info.get('entries', [info])[0] if 'entries' in info else info
                    if entry:
                        title = entry.get('title')
                        duration = entry.get('duration')
                        dur_sec = int(duration) if duration else 0
                        mins, secs = divmod(dur_sec, 60)
                        dur_min = f"{mins}:{secs:02d}"
                        thumb = entry.get('thumbnail')
                        vid = entry.get('id')
                        return title, dur_min, dur_sec, thumb, vid
        except Exception as e:
            logger.error(f"yt-dlp details fallback failed: {e}")
        return None, "0:00", 0, None, None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        res_data = await asyncio.to_thread(self._sync_search, link, 1)
        results = res_data.get("result", [])
        if results:
            result = results[0]
            title = result.get("title") or "YouTube Video"
            duration_min = result.get("duration") or "0:00"
            thumbs = result.get("thumbnails", [])
            thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else None
            vidid = result.get("id")
            duration_sec = int(time_to_seconds(duration_min)) if duration_min else 0
            return title, duration_min, duration_sec, thumbnail, vidid
            
        return await asyncio.to_thread(self._sync_details_fallback, link)

    async def title(self, link: str, videoid: Union[bool, str] = None):
        t, m, s, thumb, vid = await self.details(link, videoid=videoid)
        return t

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        t, m, s, thumb, vid = await self.details(link, videoid=videoid)
        return m

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        t, m, s, thumb, vid = await self.details(link, videoid=videoid)
        return thumb

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            downloaded_file = await download_video(link)
            if downloaded_file:
                return 1, downloaded_file
            return 0, "Video download failed"
        except Exception as e:
            return 0, f"Video download error: {e}"

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        if videoid:
            link = self.listbase + link
        if "&" in link:
            link = link.split("&")[0]
        try:
            plist = await Playlist.get(link)
        except Exception:
            return []
        videos = plist.get("videos") or []
        ids = []
        for data in videos[:limit]:
            if not data:
                continue
            vid = data.get("id")
            if not vid:
                continue
            ids.append(vid)
        return ids

    async def track(self, link: str, videoid: Union[bool, str] = None):
        t, m, s, thumb, vid = await self.details(link, videoid=videoid)
        track_details = {
            "title": t,
            "link": f"https://www.youtube.com/watch?v={vid}" if vid else link,
            "vidid": vid,
            "duration_min": m,
            "duration_sec": s,
            "thumb": thumb,
        }
        return track_details, vid

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        ytdl_opts = {"quiet": True}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r.get("formats", []):
                try:
                    if "dash" not in str(format.get("format", "")).lower():
                        formats_available.append(
                            {
                                "format": format.get("format"),
                                "filesize": format.get("filesize"),
                                "format_id": format.get("format_id"),
                                "ext": format.get("ext"),
                                "format_note": format.get("format_note"),
                                "yturl": link,
                            }
                        )
                except Exception:
                    continue
        return formats_available, link

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        res_data = await asyncio.to_thread(self._sync_search, link, 10)
        result = res_data.get("result", [])
        if not result or query_type >= len(result):
            return "Unknown", "0:00", None, None
        title = result[query_type]["title"]
        duration_min = result[query_type]["duration"]
        vidid = result[query_type]["id"]
        thumbnail = result[query_type]["thumbnails"][0]["url"].split("?")[0]
        return title, duration_min, thumbnail, vidid


    async def download(
        self,
        link: str,
        mystic=None,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> tuple:
        if videoid:
            link = self.base + link
        try:
            if video or songvideo:
                downloaded_file = await download_video(link)
            else:
                downloaded_file = await download_song(link)
            if downloaded_file:
                return downloaded_file, True
            return None, False
        except Exception:
            return None, False


YouTube = YouTubeAPI()


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

        # Try to generate a styled thumbnail using gen_thumb (if PIL/aiofiles are available)
        if vidid:
            try:
                from thumb_gen import gen_thumb
                styled = await gen_thumb(vidid)
                if styled and (str(styled).startswith("http") or (os.path.exists(str(styled)) and os.path.getsize(str(styled)) > 1000)):
                    final_thumb = styled
            except Exception as gt_err:
                logger.debug(f"gen_thumb skipped: {gt_err}")

        # 2. Check if file already exists in downloads
        prefix = "audio" if download_type == "audio" else "video"
        ext = "mp3" if download_type == "audio" else "mp4"
        cached_file = os.path.join(DOWNLOAD_DIR, f"{vidid}_{prefix}.{ext}")
        if os.path.exists(cached_file) and os.path.getsize(cached_file) > 10000:
            logger.info(f"Using cached file: {cached_file}")
            return cached_file, title, duration_sec, final_thumb

        # Also check any file starting with vidid_prefix
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(f"{vidid}_{prefix}") and not fname.endswith(".jpg"):
                full_p = os.path.join(DOWNLOAD_DIR, fname)
                if os.path.getsize(full_p) > 10000:
                    logger.info(f"Using cached file matching prefix: {full_p}")
                    return full_p, title, duration_sec, final_thumb

        # 3. Primary Fast Direct Stream via Yuki API
        logger.info(f"Downloading {download_type} directly via Yuki API for: {title} ({vidid})...")
        downloaded_file = None
        try:
            if download_type == "audio":
                downloaded_file = await download_song(youtube_url)
            else:
                downloaded_file = await download_video(youtube_url)
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
    Attempts to join the active voice call of a group/channel using JoinGroupCallRequest.
    """
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.functions.messages import GetFullChatRequest
        from telethon.tl.functions.phone import JoinGroupCallRequest
        from telethon.tl.types import InputGroupCall, DataJSON, Channel, GroupCallDiscarded
        
        entity = await client.get_entity(peer_id)
        if isinstance(entity, Channel):
            full = await client(GetFullChannelRequest(entity))
        else:
            full = await client(GetFullChatRequest(entity.id))
            
        group_call = full.full_chat.call
        if group_call and not isinstance(group_call, GroupCallDiscarded):
            await client(JoinGroupCallRequest(
                call=InputGroupCall(
                    id=group_call.id,
                    access_hash=group_call.access_hash
                ),
                join_as=await client.get_input_entity('me'),
                params=DataJSON(data='{}'),
                muted=True
            ))
            logger.info(f"Successfully joined VC for peer {peer_id}")
            return True
        else:
            logger.debug(f"No active VC found for peer {peer_id}")
    except Exception as e:
        logger.warning(f"Could not join VC for peer {peer_id}: {e}")
    return False


async def get_peer_from_link(client: TelegramClient, link: str):
    """
    Resolves a group/channel link or username to a chat entity,
    joining the channel/group if the userbot is not already a member.
    """
    link = link.strip()
    if not link:
        return None
        
    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.types import ChatInviteAlready, ChatInvite
    
    # Check if link is a numeric ID
    try:
        val = link
        chat_id = None
        if val.startswith("-100") and val[4:].isdigit():
            chat_id = int(val)
        elif val.startswith("-") and val[1:].isdigit():
            chat_id = int(val)
        elif val.isdigit():
            chat_id = int(val)
            
        if chat_id is not None:
            try:
                return await client.get_entity(chat_id)
            except Exception:
                # Fallback: Iterate dialogs to find the correct entity
                async for dialog in client.iter_dialogs():
                    if dialog.id == chat_id:
                        return dialog.entity
    except Exception:
        pass
        
        
    # Check if it is a private invite link
    if "t.me/+" in link or "t.me/joinchat/" in link:
        # Extract join hash
        if "t.me/+" in link:
            hash_val = link.split('+')[-1].split('/')[0].strip()
        else:
            hash_val = link.split('joinchat/')[-1].split('/')[0].strip()
            
        try:
            invite = await client(CheckChatInviteRequest(hash_val))
            if isinstance(invite, ChatInviteAlready):
                return invite.chat
            else:
                # Need to join
                updates = await client(ImportChatInviteRequest(hash_val))
                if updates and hasattr(updates, 'chats') and updates.chats:
                    return updates.chats[0]
                
                # If joined successfully but no chats returned, check invite again to get the ChatInviteAlready object
                invite_check = await client(CheckChatInviteRequest(hash_val))
                if isinstance(invite_check, ChatInviteAlready):
                    return invite_check.chat
        except Exception as e:
            logger.warning(f"Error checking/joining invite link {link}: {e}")
            # If we hit UserAlreadyParticipantError but CheckChatInviteRequest failed to say so, try checking again
            if "UserAlreadyParticipant" in str(e) or "already" in str(e).lower():
                try:
                    invite_check = await client(CheckChatInviteRequest(hash_val))
                    if isinstance(invite_check, ChatInviteAlready):
                        return invite_check.chat
                except Exception:
                    pass
            # Try to get entity directly as a fallback
            try:
                return await client.get_entity(link)
            except Exception:
                pass
    else:
        # It's a username or public link
        username = link.split('/')[-1].strip()
        if username.startswith("@"):
            username = username[1:]
            
        try:
            # Join channel first
            await client(JoinChannelRequest(username))
        except Exception as e:
            logger.warning(f"Error joining public channel {username}: {e}")
            
        try:
            return await client.get_entity(username)
        except Exception as e:
            logger.warning(f"Error getting entity for {username}: {e}")
            
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
    Generates a 10-second silence.mp3 file in the downloads folder using FFmpeg.
    """
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, "silence.mp3")
    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
        return file_path
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", "10", file_path, "-y",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.wait()
        logger.info(f"Generated silence.mp3 successfully at {file_path}")
    except Exception as e:
        logger.error(f"Failed to generate silence.mp3: {e}")
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



async def join_channel_single(client: TelegramClient, ch: str) -> bool:
    """
    Attempts to join a single channel or group by invite link or username.
    Returns True if successfully joined, already in it, or join request was sent.
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
        success = await join_channel_single(client, ch_clean)
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

async def apply_branding(client: TelegramClient, branding_username: str, session_data: dict):
    """
    Appends the branding bot username suffix to the userbot profile's name and bio based on global settings.
    Stores original details in session data for restoration.
    """
    from telethon.tl.functions.users import GetFullUserRequest
    from telethon.tl.functions.account import UpdateProfileRequest
    
    try:
        global_settings = database.get_global_settings()
        brand_name_enabled = global_settings.get("branding_name_enabled", True)
        brand_bio_enabled = global_settings.get("branding_bio_enabled", True)
        
        brand_name_text = global_settings.get("branding_name_text")
        brand_bio_text = global_settings.get("branding_bio_text")
        
        full_user = await client(GetFullUserRequest('me'))
        user_me = full_user.users[0]
        full_profile = full_user.full_user
        
        custom_name = session_data.get("name")
        orig_first_name = custom_name if custom_name else (user_me.first_name or "")
        orig_bio = full_profile.about or ""
        
        if not session_data.get("original_name"):
            session_data["original_name"] = orig_first_name
        if not session_data.get("original_bio"):
            session_data["original_bio"] = orig_bio
            
        name_suffix = brand_name_text if brand_name_text else (f" via @{branding_username}" if branding_username else "")
        bio_suffix = brand_bio_text if brand_bio_text else (f" via @{branding_username}" if branding_username else "")
        
        new_first_name = orig_first_name
        if brand_name_enabled and name_suffix:
            if name_suffix not in orig_first_name:
                new_first_name = (orig_first_name + name_suffix)[:64]
                
        new_bio = orig_bio
        if brand_bio_enabled and bio_suffix:
            if bio_suffix not in orig_bio:
                new_bio = (orig_bio + bio_suffix)[:70]
                
        await client(UpdateProfileRequest(
            first_name=new_first_name,
            about=new_bio
        ))
        
        database.save_session(session_data)
        logger.info(f"Branding applied successfully for userbot: {user_me.id} (Name: {brand_name_enabled}, Bio: {brand_bio_enabled})")
    except Exception as e:
        logger.error(f"Failed to apply branding: {e}")

async def restore_branding(client: TelegramClient, session_data: dict):
    """
    Restores the userbot profile's original name and bio.
    """
    from telethon.tl.functions.account import UpdateProfileRequest
    try:
        orig_name = session_data.get("original_name", "")
        orig_bio = session_data.get("original_bio", "")
        if orig_name or orig_bio:
            await client(UpdateProfileRequest(
                first_name=orig_name if orig_name else "User",
                about=orig_bio
            ))
            logger.info("Branding restored successfully.")
    except Exception as e:
        logger.error(f"Failed to restore branding: {e}")


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
        self.vc_keepalive_task: Optional[asyncio.Task] = None
        self.current_vc_chat_id: Optional[int] = None
        self.current_vc_link: Optional[str] = None
        self.pytgcalls_client: Optional[PyTgCalls] = None
        self.is_muted = True
        self.bg_tasks: Set[asyncio.Task] = set()
        
        # Caching attributes to resolve performance bottlenecks
        self.settings = {}
        self.groups_cache = None
        self.groups_cache_time = 0.0

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
            
            # Restart the broadcast task if the bot is currently running to apply changes immediately
            if self.is_running:
                if self.broadcast_task:
                    self.broadcast_task.cancel()
                self.broadcast_task = asyncio.create_task(self.broadcast_loop())
                logger.info(f"Restarted broadcast loop for userbot {self.session_id} to apply new settings/interval immediately.")

    async def _mark_unauthorized_and_cleanup(self):
        """
        Cleans up a deactivated or revoked session by disconnecting the client,
        marking the database status as 'unauthorized', and deleting/renaming the session file.
        """
        logger.warning(f"Cleaning up unauthorized/deactivated userbot session: {self.session_id}")
        self.is_running = False
        
        # 1. Disconnect client
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning(f"Error disconnecting client during auth-cleanup for {self.session_id}: {e}")
        
        # 2. Update status in MongoDB database
        sess_data = database.get_session(self.session_id)
        if sess_data:
            sess_data["status"] = "unauthorized"
            sess_data["last_error"] = "❌ Account has been deactivated or session revoked from Telegram."
            # Remove session bytes to save database space since they are invalid now
            sess_data.pop("session_bytes", None)
            database.save_session(sess_data)
            
        # 3. Remove local session files to prevent lockups and auto-healing retries
        session_file = sess_data.get("session_file") if sess_data else None
        if not session_file:
            # Construct fallback path matching add_bot.py
            user_id = sess_data.get("user_id") if sess_data else "temp"
            user_dir = utils.ensure_user_dir(user_id)
            session_file = os.path.join(user_dir, f"{self.session_id}.session")
            
        if session_file:
            import glob
            for f in glob.glob(session_file + "*"):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                        logger.info(f"Deleted local session file during auth-cleanup: {f}")
                except Exception as e:
                    logger.warning(f"Could not delete session file {f} during auth-cleanup: {e}")
                    
        # 4. Remove from manager's running bots list locally to avoid circular import issues
        try:
            import userbot_manager
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

                # 2. Check and maintain Telethon connection
                if self.client:
                    if not self.client.is_connected():
                        logger.info(f"Watchdog: Userbot {self.session_id} disconnected. Reconnecting...")
                        try:
                            await asyncio.wait_for(self.client.connect(), timeout=15.0)
                            if await self.client.is_user_authorized():
                                logger.info(f"Watchdog: Userbot {self.session_id} reconnected successfully.")
                            else:
                                logger.warning(f"Watchdog: Userbot {self.session_id} session revoked/unauthorized.")
                                await self._mark_unauthorized_and_cleanup()
                                break
                        except Exception as rec_err:
                            logger.warning(f"Watchdog: Userbot {self.session_id} reconnect error: {rec_err}")
                    else:
                        # Send keep-alive ping to prevent Telegram idle timeout
                        try:
                            me = await self.client.get_me()
                            if me:
                                self.me_id = me.id
                                if me.username:
                                    self.username = me.username
                        except Exception as ping_err:
                            logger.debug(f"Watchdog: Keepalive ping failed for {self.session_id}: {ping_err}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in watchdog loop for {self.session_id}: {e}")
                await asyncio.sleep(5.0)

    async def join_voice_chat(self, link_or_id: Union[str, int]) -> tuple:
        """
        Attempts to join the active voice call of a group/channel using PyTgCalls.
        Supports group link, username, or direct Chat ID.
        """
        if not self.is_running or not self.client:
            return False, "Userbot is not running."
            
        try:
            import telethon.utils as tu
            link_str = str(link_or_id).strip()
            entity = None
            
            # Direct integer chat ID resolution
            try:
                chat_int = int(link_str)
                entity = await self.client.get_entity(chat_int)
            except Exception:
                pass
                
            if not entity:
                entity = await get_peer_from_link(self.client, link_str)
                
            if not entity:
                try:
                    chat_int = int(link_str)
                    full_chat_id = chat_int
                except Exception:
                    return False, "Could not resolve the group link, username, or Chat ID. Please ensure UserBot is in the group."
            else:
                full_chat_id = tu.get_peer_id(entity)

            pytg = await self.get_pytgcalls()
            
            # Generate silence file to prevent auto-kick
            silence_file = await generate_silence()
            if not silence_file or not os.path.exists(silence_file):
                return False, "Failed to generate silence.mp3. Make sure FFmpeg is installed."
                
            logger.info(f"Joining VC of {full_chat_id} using PyTgCalls...")
            
            try:
                await pytg.join_group_call(
                    full_chat_id,
                    MediaStream(silence_file)
                )
            except Exception as join_err:
                err_str = str(join_err).lower()
                if "already_joined" in err_str or "already in" in err_str or "node" in err_str:
                    try:
                        await pytg.change_stream(full_chat_id, MediaStream(silence_file))
                    except Exception:
                        pass
                else:
                    logger.warning(f"join_group_call attempt error: {join_err}")
                    try:
                        await pytg.change_stream(full_chat_id, MediaStream(silence_file))
                    except Exception as ch_err:
                        return False, f"Failed to join VC: {join_err}", None
            
            self.current_vc_chat_id = full_chat_id
            self.current_vc_link = link_str
            self.joined_vcs.add(full_chat_id)
            
            # Immediately mute the mic (mic off) on join
            try:
                await pytg.mute_stream(full_chat_id)
                self.is_muted = True
            except Exception:
                pass
                
            # Save VC status in MongoDB
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["vc_chat_id"] = full_chat_id
                sess_data["vc_link"] = link_str
                database.save_session(sess_data)
            
            chat_title = getattr(entity, 'title', 'Group') if entity else f"Chat {full_chat_id}"
            return True, f"Successfully joined Voice Chat of {chat_title}!"
        except Exception as e:
            logger.exception("Error joining VC using PyTgCalls")
            return False, f"Failed to join VC: {e}"

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
                    await self.pytgcalls_client.leave_group_call(target_chat)
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
                    await self.pytgcalls_client.leave_group_call(cid)
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

    async def get_pytgcalls(self) -> PyTgCalls:
        if not self.pytgcalls_client:
            self.pytgcalls_client = PyTgCalls(self.client)
            
            @self.pytgcalls_client.on_stream_end()
            async def on_stream_end(client, update):
                try:
                    chat_id = getattr(update, "chat_id", None)
                    if chat_id:
                        from handlers.player import play_next_in_queue
                        await play_next_in_queue(chat_id)
                except Exception as e:
                    logger.error(f"Error in on_stream_end: {e}")

            await self.pytgcalls_client.start()
        return self.pytgcalls_client

    async def mute_mic(self, chat_id: Optional[int] = None) -> tuple:
        target_chat = chat_id or self.current_vc_chat_id
        if not self.is_running or not target_chat:
            return False, "Bot is not in any VC."
        try:
            pytg = await self.get_pytgcalls()
            await pytg.mute_stream(target_chat)
            self.is_muted = True
            return True, "Mic turned OFF (Muted)."
        except Exception as e:
            logger.error(f"Failed to mute: {e}")
            return False, f"Failed to mute: {e}"

    async def unmute_mic(self, chat_id: Optional[int] = None) -> tuple:
        target_chat = chat_id or self.current_vc_chat_id
        if not self.is_running or not target_chat:
            return False, "Bot is not in any VC."
        try:
            pytg = await self.get_pytgcalls()
            await pytg.unmute_stream(target_chat)
            self.is_muted = False
            return True, "Mic turned ON (Unmuted)."
        except Exception as e:
            logger.error(f"Failed to unmute: {e}")
            return False, f"Failed to unmute: {e}"

    async def stop_song(self, chat_id: Optional[int] = None) -> tuple:
        target_chat = chat_id or self.current_vc_chat_id
        if not self.is_running or not target_chat:
            return False, "Not in a Voice Chat."
        try:
            from handlers.player import _chat_queues, _active_chat_players, _track_timer_tasks
            _chat_queues.pop(target_chat, None)
            _active_chat_players.pop(target_chat, None)
            prev_timer = _track_timer_tasks.pop(target_chat, None)
            if prev_timer and not prev_timer.done() and prev_timer != asyncio.current_task():
                prev_timer.cancel()

            pytg = await self.get_pytgcalls()
            try:
                await pytg.leave_group_call(target_chat)
                logger.info(f"Userbot {self.session_id} left VC {target_chat} after stop.")
            except Exception as leave_err:
                err_str = str(leave_err).lower()
                if "not in a group call" in err_str or "not_in" in err_str or "no active" in err_str:
                    # Already not in VC — treat as success
                    logger.info(f"Userbot {self.session_id} was already not in VC {target_chat}.")
                else:
                    logger.warning(f"leave_group_call error: {leave_err} — falling back to silence+mute")
                    try:
                        silence_file = await generate_silence()
                        await pytg.change_stream(target_chat, MediaStream(silence_file))
                        await pytg.mute_stream(target_chat)
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
            
        try:
            pytg = await self.get_pytgcalls()
            
            if play_type == "video":
                from pytgcalls.types import MediaStream, VideoQuality
                w, h = 640, 360 # Default fallback
                has_video_track = False
                try:
                    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type,width,height", "-of", "csv=s=x:p=0", file_path]
                    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await proc.communicate()
                    if proc.returncode == 0 and stdout:
                        res = stdout.decode().strip()
                        if 'video' in res or 'x' in res:
                            has_video_track = True
                            nums = [int(p) for p in re.findall(r'\d+', res)]
                            if len(nums) >= 2:
                                parsed_w, parsed_h = nums[0], nums[1]
                                w = parsed_w if parsed_w % 2 == 0 else parsed_w - 1
                                h = parsed_h if parsed_h % 2 == 0 else parsed_h - 1
                                logger.info(f"Detected original video resolution: {w}x{h}")
                except Exception as e:
                    logger.warning(f"Could not extract resolution: {e}")
                    
                if has_video_track:
                    stream_obj = MediaStream(file_path)
                    logger.info(f"Streaming video (MediaStream): {file_path} at {w}x{h}")
                else:
                    logger.info(f"No video track in {file_path}, falling back to AudioPiped seamlessly")
                    stream_obj = MediaStream(file_path)
            else:
                stream_obj = MediaStream(file_path)
                
            # Always try change_stream first — pytgcalls handles it whether
            # we're already in VC or not (after .end, pytgcalls may still be
            # connected internally even if our tracking says otherwise).
            try:
                await pytg.change_stream(actual_chat, stream_obj)
                self.joined_vcs.add(actual_chat)
                self.current_vc_chat_id = actual_chat
                logger.info(f"change_stream succeeded for {actual_chat}")
            except Exception as cs_err:
                cs_err_str = str(cs_err).lower()
                logger.warning(f"change_stream failed ({cs_err}), trying join_group_call...")
                # If video failed with video source error, fallback to AudioPiped
                if play_type == "video" and ("video source" in cs_err_str or "no video" in cs_err_str):
                    stream_obj = MediaStream(file_path)
                try:
                    await pytg.join_group_call(actual_chat, stream_obj)
                    self.joined_vcs.add(actual_chat)
                    self.current_vc_chat_id = actual_chat
                    logger.info(f"join_group_call succeeded for {actual_chat}")
                except Exception as join_err:
                    join_err_str = str(join_err).lower()
                    if "already" in join_err_str or "node" in join_err_str:
                        # pytgcalls says we're still in — try change_stream one more time
                        try:
                            await pytg.change_stream(actual_chat, stream_obj)
                            self.joined_vcs.add(actual_chat)
                            self.current_vc_chat_id = actual_chat
                        except Exception as final_err:
                            return False, f"Could not stream media: {final_err}", None
                    else:
                        return False, f"Could not stream media: {join_err}", None
                    
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
                await pytg.unmute_stream(actual_chat)
                self.is_muted = False
                logger.info(f"Automatically unmuted userbot {self.session_id} for song playing in {actual_chat}.")
            except Exception as unmute_err:
                logger.warning(f"Could not auto-unmute stream on play: {unmute_err}")
            
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
            try:
                logger.info(f"Fetching dialogs for userbot {self.session_id} to refresh groups cache...")
                async with _dialog_fetch_semaphore:
                    dialogs = await self.client.get_dialogs(limit=None)
                self.groups_cache = [d for d in dialogs if d.is_group]
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
                        os.remove(f"user_data/{self.me_id}/sessions/{self.session_id}.session")
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
        session_file = sess_data["session_file"]
        
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
                logger.error(f"Failed to restore session file from MongoDB: {e}")

        if not os.path.exists(session_file):
            logger.error(f"Session file not found: {session_file}")
            # Mark status stopped/unauthorized in database so it doesn't get retried
            sess_data = database.get_session(self.session_id)
            if sess_data:
                sess_data["status"] = "stopped"
                database.save_session(sess_data)
            return False
            
        # Spoof a deterministic device profile to avoid detection
        device_prof = utils.get_device_profile(self.session_id)
        api_id, api_hash = config.get_random_api_id_hash()
        self.client = TelegramClient(
            session_file, 
            api_id, 
            api_hash,
            device_model=device_prof["device_model"],
            system_version=device_prof["system_version"],
            app_version=device_prof["app_version"]
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
            # Wrap connect() in wait_for to prevent infinite hang if Telethon blocks
            await asyncio.wait_for(self.client.connect(), timeout=10.0)
            
            # Wrap is_user_authorized() as well
            is_authorized = await asyncio.wait_for(self.client.is_user_authorized(), timeout=10.0)
            if not is_authorized:
                logger.warning(f"Userbot {self.session_id} is unauthorized. Cleaning up.")
                await self._mark_unauthorized_and_cleanup()
                return False
                
            self.is_running = True
            
            # Refresh name and username info immediately
            try:
                me = await self.client.get_me()
                if me:
                    self.me_id = me.id
                    full_n = f"{me.first_name or ''} {me.last_name or ''}".strip()
                    if not sess_data.get("name"):
                        sess_data["name"] = full_n or "UserBot"
                    self.name = sess_data.get("name") or full_n or "UserBot"
                    self.username = me.username or ""
                    sess_data["username"] = me.username or ""
            except Exception as me_err:
                logger.warning(f"Could not fetch get_me for {self.session_id} on startup: {me_err}")
                
            # Apply configurations
            global_settings = database.get_global_settings()
            
            # Auto-join support links and custom auto-join links
            support_links = []
            support_channel = global_settings.get("support_channel") or "https://t.me/TheVillainActive"
            support_group = global_settings.get("support_group") or "https://t.me/+WzyoJkg4bzhlNTFl"
            if support_channel:
                support_links.append(support_channel)
            if support_group:
                support_links.append(support_group)
                
            ub_joins = global_settings.get("userbot_auto_join_links", [])
            if ub_joins:
                support_links.extend(ub_joins)
            if support_links:
                t_join1 = asyncio.create_task(force_join_channels(self.client, support_links, session_id=self.session_id))
                self.bg_tasks.add(t_join1)
                t_join1.add_done_callback(self.bg_tasks.discard)
 
            # Auto-join force subscribe channels for the bot users
            fj_links = global_settings.get("force_join_links", [])
            if fj_links:
                t_join2 = asyncio.create_task(force_join_channels(self.client, fj_links, session_id=self.session_id))
                self.bg_tasks.add(t_join2)
                t_join2.add_done_callback(self.bg_tasks.discard)
                
            brand_username = global_settings.get("branding_username")
            if brand_username:
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
            return False
        except Exception as e:
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
            return False

    async def stop(self):
        if not self.is_running:
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
            
        if self.pytgcalls_client:
            try:
                if self.current_vc_chat_id:
                    await self.pytgcalls_client.leave_group_call(self.current_vc_chat_id)
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
                            
                            from handlers.player import _active_chat_players, _chat_queues, _track_timer_tasks, _preparing_chats, get_chat_lock, play_next_in_queue
                            
                            inquiry_text = utils.format_html_message(
                                f"<blockquote><b>» 🎧 Playing Inquiry...</b>\n\n"
                                f"🔍 <i>{query or 'replied media'}</i>\n"
                                f"⏳ <b>Status :</b> Preparing {play_type} Stream... 🎶</blockquote>"
                            )
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
                                    os.makedirs("downloads", exist_ok=True)
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
                                await prog.edit(
                                    utils.format_html_message(
                                        f"<blockquote><b>» ⏳ Connecting To Voice Chat</b>\n\n"
                                        f"🎙️ <i>Connecting To Voice Chat & Starting Stream Pipeline...</i></blockquote>"
                                    )
                                )
                                
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
                            
                            from handlers.player import _main_bot, build_now_playing_markup
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
                                        
                            if sent_msg and hasattr(sent_msg, "id"):
                                _active_chat_players[chat_id]["msg_id"] = sent_msg.id
                            return

                        elif cmd in ("skip", "next", "cskip", "cnext"):
                            try:
                                await event.delete()
                            except Exception:
                                pass
                            from handlers.player import _chat_queues, _active_chat_players, _track_timer_tasks, play_next_in_queue
                            
                            old_p = _active_chat_players.get(chat_id)
                            if old_p and old_p.get("msg_id"):
                                try:
                                    from handlers.player import _main_bot
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
                            from handlers.player import _active_chat_players, _chat_queues
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
                            from handlers.player import _chat_queues, _active_chat_players, _track_timer_tasks
                            
                            old_p = _active_chat_players.get(chat_id)
                            if old_p and old_p.get("msg_id"):
                                try:
                                    from handlers.player import _main_bot
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

                    # 3-second per-chat cooldown to prevent spambot limits while remaining responsive
                    if now - last_reply_time >= 3.0:
                        ar_mode = current_settings.get("auto_reply_mode", "single")

                        DEFAULT_REPLIES = [
                            "Hello! How can I help you? 😊",
                            "Hey there! Thanks for reaching out. Please leave a message! 💬",
                            "Hello! I am currently away, will get back to you soon. ✨"
                        ]

                        if ar_mode == "multiple":
                            # Multiple / Rotational mode: rotate among the configured messages
                            candidates = current_settings.get("auto_reply_messages", [])
                            if not candidates:
                                candidates = current_settings.get("tag_messages", [])
                            if not candidates and current_settings.get("auto_reply_msg"):
                                candidates = [current_settings.get("auto_reply_msg")]
                            candidates = [m for m in candidates if m and str(m).strip()]
                            if not candidates:
                                candidates = DEFAULT_REPLIES
                            msgs_to_send = [random.choice(candidates)]
                        else:
                            # Single mode: always send the single reply message
                            single_msg = current_settings.get("auto_reply_msg")
                            if not single_msg:
                                multi = current_settings.get("auto_reply_messages", []) or current_settings.get("tag_messages", [])
                                multi = [m for m in multi if m and str(m).strip()]
                                single_msg = multi[0] if multi else DEFAULT_REPLIES[0]
                            msgs_to_send = [single_msg]

                        for selected_reply in msgs_to_send:
                            processed_reply = utils.parse_spintax(selected_reply)
                            processed_reply = utils.normalize_text(processed_reply)
                            processed_reply = utils.make_message_unique(processed_reply)

                            try:
                                # Added 10s delay as requested
                                await asyncio.sleep(10.0)
                                try:
                                    await event.reply(processed_reply, parse_mode='html')
                                except Exception:
                                    # Fallback to plain text if HTML tags are malformed
                                    await event.reply(processed_reply, parse_mode=None)
                                logger.info(f"Auto-replied ({ar_mode} mode) in chat {event.chat_id} for userbot {self.session_id}")
                            except Exception as reply_err:
                                logger.warning(f"Could not send auto-reply in chat {event.chat_id}: {reply_err}")

                        self.tag_cooldown[event.chat_id] = now

                # 4. Private message handling (Auto-Welcome)
                if event.is_private:
                    if not sess_data:
                        sess_data = database.get_session(self.session_id)
                    current_settings = sess_data.get("settings", {}) if sess_data else self.settings
                    
                    if current_settings.get("auto_welcome"):
                        sender = await event.get_sender()
                        if sender and not getattr(sender, "bot", False) and not getattr(sender, "is_self", False):
                            welcomed_users = sess_data.get("stats", {}).get("welcomed_users", []) if sess_data else []
                            if sender.id not in welcomed_users:
                                w_mode = current_settings.get("welcome_mode", "single")
                                messages_to_send = []
                                
                                if w_mode == "multiple":
                                    welcome_messages = current_settings.get("welcome_messages", [])
                                    if not welcome_messages and current_settings.get("welcome_msg"):
                                        welcome_messages = [current_settings.get("welcome_msg")]
                                    welcome_messages = [m for m in welcome_messages if m and str(m).strip()]
                                    if welcome_messages:
                                        messages_to_send = welcome_messages
                                else:
                                    single_msg = current_settings.get("welcome_msg")
                                    if not single_msg:
                                        multi = current_settings.get("welcome_messages", [])
                                        single_msg = multi[0] if multi else None
                                    if single_msg:
                                        messages_to_send = [single_msg]
                                    
                                if messages_to_send:
                                    for welcome_msg in messages_to_send:
                                        processed_welcome = utils.parse_spintax(welcome_msg)
                                        processed_welcome = utils.normalize_text(processed_welcome)
                                        processed_welcome = utils.make_message_unique(processed_welcome)
                                        try:
                                            await asyncio.sleep(random.uniform(0.5, 1.5))
                                            try:
                                                await event.reply(processed_welcome, parse_mode='html')
                                            except Exception:
                                                await event.reply(processed_welcome, parse_mode=None)
                                            logger.info(f"Sent welcome message to {sender.id} from userbot {self.session_id}")
                                        except Exception as e:
                                            logger.warning(f"Could not send welcome message to {sender.id}: {e}")
                                            
                                    if sess_data and sender.id not in welcomed_users:
                                        welcomed_users.append(sender.id)
                                        sess_data.setdefault("stats", {})["welcomed_users"] = welcomed_users
                                        database.save_session(sess_data)

    async def broadcast_loop(self):
        """
        Periodically broadcasts the configured message to all group dialogs.
        Includes advanced anti-spam error handling and humanized delay timing.
        """
        while self.is_running:
            try:
                if self.settings.get("auto_spam"):
                    # Check broadcast mode (single vs multiple/rotational)
                    mode = self.settings.get("broadcast_mode", "single")
                    if mode == "multiple":
                        broadcast_messages = self.settings.get("broadcast_messages", [])
                        if not broadcast_messages:
                            broadcast_messages = [self.settings.get("broadcast_msg")]
                    else:
                        broadcast_messages = [self.settings.get("broadcast_msg")]
                        
                    broadcast_messages = [m for m in broadcast_messages if m]
                    
                    if broadcast_messages:
                        try:
                            # Dynamically refresh groups so newly joined groups are automatically detected without restarting
                            force_refresh = (time.time() - self.groups_cache_time > 60) or not self.groups_cache
                            groups = await self.get_groups(force_refresh=force_refresh)
                            
                            sent_to_some = False
                            msg_count_in_round = 0
                            
                            for g in groups:
                                if not self.is_running:
                                    break
                                    
                                # Check cached state in real-time
                                if not self.settings.get("auto_spam"):
                                    break
                                    
                                # Choose a random message from the multiple messages list
                                current_msg = random.choice(broadcast_messages)
                                
                                # Parse Spintax, Normalize compatibility characters, and strip zero-width characters
                                processed_msg = utils.parse_spintax(current_msg)
                                processed_msg = utils.normalize_text(processed_msg)
                                processed_msg = utils.make_message_unique(processed_msg)
                                
                                try:
                                    # Anti-Spam / Humanization: Simulate reading chat history before sending
                                    try:
                                        async for _ in self.client.iter_messages(g.input_entity, limit=2):
                                            pass
                                    except Exception:
                                        pass
    
                                    # Anti-Spam / Humanization: Simulate typing action for realism
                                    try:
                                        async with self.client.action(g.input_entity, 'typing'):
                                            typing_delay = random.uniform(2.0, 5.0)
                                            await asyncio.sleep(typing_delay)
                                    except Exception:
                                        pass
                                        
                                    await self.client.send_message(g.input_entity, processed_msg, parse_mode='html')
                                    sent_to_some = True
                                    msg_count_in_round += 1
                                    
                                    # Fast dynamic inter-group delay (user configurable, default 10s, min floor 15s to avoid limits)
                                    inter_delay = float(self.settings.get("inter_group_delay", 15.0))
                                    inter_delay = max(15.0, inter_delay)
                                    
                                    # Add minor jitter variance (+/- 10%)
                                    sleep_jitter = random.uniform(-0.10, 0.10) * inter_delay
                                    delay_to_sleep = max(10.0, inter_delay + sleep_jitter)
                                    
                                    # Long pause every 40 messages to avoid PeerFloodError limit
                                    if msg_count_in_round > 0 and msg_count_in_round % 40 == 0:
                                        logger.info(f"Userbot {self.session_id} resting for 3 mins to prevent Telegram limits...")
                                        delay_to_sleep += 180.0
                                        
                                    await asyncio.sleep(delay_to_sleep)
    
                                except PeerFloodError:
                                    logger.warning(f"⚠️ PeerFloodError on userbot {self.session_id}! Telegram rate-limit detected. Auto Broadcast PAUSED to protect account.")
                                    self.settings["auto_spam"] = False
                                    sess_data = database.get_session(self.session_id)
                                    if sess_data:
                                        sess_data.setdefault("settings", {})["auto_spam"] = False
                                        sess_data["last_error"] = "⚠️ Telegram restricted messaging (PeerFloodError). Auto Broadcast paused to protect your account."
                                        database.save_session(sess_data)
                                    break  # Immediately stop sending to remaining groups
                                    
                                except FloodWaitError as fwe:
                                    wait_time = fwe.seconds + 5
                                    logger.warning(f"FloodWaitError on userbot {self.session_id}: Sleeping for {wait_time}s")
                                    await asyncio.sleep(wait_time)
                                    
                                except SlowModeWaitError as smwe:
                                    logger.info(f"Group {g.id} slow mode wait: {smwe.seconds}s. Skipping...")
                                    await asyncio.sleep(min(10, smwe.seconds))
                                    
                                except (UserBannedInChannelError, ChatWriteForbiddenError, ChannelPrivateError, ChannelInvalidError, ChatIdInvalidError, UserNotParticipantError, ValueError) as chat_err:
                                    logger.debug(f"Cannot write to group {g.id} ({chat_err.__class__.__name__}). Skipping.")
                                    
                                except Exception as e:
                                    err_class = e.__class__.__name__
                                    if err_class in ("UserDeactivatedError", "AuthKeyUnregisteredError", "SessionRevokedError", "SessionExpiredError"):
                                        logger.error(f"Userbot {self.session_id} broadcast failed with auth error: {e}. Cleaning up.")
                                        await self._mark_unauthorized_and_cleanup()
                                        break
                                    else:
                                        logger.warning(f"Failed to send broadcast message to group {g.id}: {e}")
                                    
                            if sent_to_some:
                                sess_data = database.get_session(self.session_id)
                                if sess_data:
                                    sess_data["stats"]["broadcast_count"] = sess_data["stats"].get("broadcast_count", 0) + 1
                                    database.save_session(sess_data)
                        except Exception as e:
                            logger.error(f"Error inside userbot broadcast loop execution: {e}")
            
                # Fetch broadcast interval from in-memory settings (default 300s)
                try:
                    interval = int(self.settings.get("broadcast_interval", 300))
                except:
                    interval = 300
                    
                # Sleep in chunks to allow graceful termination
                for _ in range(max(1, interval)):
                    if not self.is_running:
                        break
                    await asyncio.sleep(1.0)

                
            except Exception as loop_e:
                logger.error(f"Critical error in broadcast loop for {self.session_id}: {loop_e}", exc_info=True)
                await asyncio.sleep(5)
