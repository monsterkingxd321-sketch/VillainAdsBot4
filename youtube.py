import asyncio
import os
import re
import sys
from typing import Union
import yt_dlp
import aiohttp

try:
    from pyrogram.enums import MessageEntityType
    from pyrogram.types import Message
except ImportError:
    MessageEntityType = None
    Message = None

try:
    from py_yt import VideosSearch, Playlist
except ImportError:
    try:
        from youtubesearchpython import VideosSearch, Playlist
    except ImportError:
        VideosSearch = None
        Playlist = None

import config

PRIMARY_API_URL = os.environ.get("MEOW_API_URL", getattr(config, "MEOW_API_URL", "https://music.yukiapi.site")).rstrip("/")
BACKUP_API_URL = os.environ.get("MEOW_BACKUP_API_URL", getattr(config, "MEOW_BACKUP_API_URL", "https://play.yukiapi.site")).rstrip("/")
API_URL = PRIMARY_API_URL
API_KEY = os.environ.get("MEOW_API_KEY", getattr(config, "MEOW_API_KEY", "yuki_8641adadf64870f3065dd502c8ee30f1")).strip()
if not API_KEY or API_KEY == "YOUR_API_KEY":
    API_KEY = getattr(config, "MEOW_API_KEY", "yuki_8641adadf64870f3065dd502c8ee30f1")

DOWNLOAD_DIR = "downloads"


def time_to_seconds(time):
    stringt = str(time)
    return sum(int(x) * 60 ** i for i, x in enumerate(reversed(stringt.split(":"))))


def extract_video_id(link: str) -> str:
    if not link:
        return ""
    link = str(link).strip()
    match = re.search(r"(?:v=|\/|vi\/|youtu\.be\/|\/v\/|\/embed\/|\/shorts\/|^)([a-zA-Z0-9_-]{11})", link)
    if match:
        return match.group(1)
    if len(link) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", link):
        return link
    return link.split("v=")[-1].split("&")[0] if "v=" in link else link


def _download_ytdlp_sync(link: str, video_id: str, is_audio: bool = True) -> str:
    prefix = "audio" if is_audio else "video"
    target_url = link if "http" in link else f"https://www.youtube.com/watch?v={video_id}"
    
    # Strategy 1: mweb + tv_embedded + android
    try:
        ydl_opts_1 = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best' if is_audio else 'bestvideo[height<=720]+bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{video_id}_{prefix}.%(ext)s"),
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'extractor_args': {'youtube': {'player_client': ['mweb', 'tv_embedded', 'android']}},
        }
        with yt_dlp.YoutubeDL(ydl_opts_1) as ydl:
            ydl.download([target_url])
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(f"{video_id}_{prefix}") and not fname.endswith((".jpg", ".part")):
                fp = os.path.join(DOWNLOAD_DIR, fname)
                if os.path.getsize(fp) > 5000:
                    return fp
    except Exception:
        pass

    # Strategy 2: android_creator + mweb + android
    try:
        ydl_opts_2 = {
            'format': 'ba/b' if is_audio else 'b[height<=720]/b',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{video_id}_{prefix}.%(ext)s"),
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'extractor_args': {'youtube': {'player_client': ['android_creator', 'mweb', 'android']}},
        }
        with yt_dlp.YoutubeDL(ydl_opts_2) as ydl:
            ydl.download([target_url])
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(f"{video_id}_{prefix}") and not fname.endswith((".jpg", ".part")):
                fp = os.path.join(DOWNLOAD_DIR, fname)
                if os.path.getsize(fp) > 5000:
                    return fp
    except Exception:
        pass

    # Strategy 3: Standard yt-dlp without player_client restrictions
    try:
        ydl_opts_3 = {
            'format': 'bestaudio/best' if is_audio else 'bestvideo[height<=720]+bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{video_id}_{prefix}.%(ext)s"),
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts_3) as ydl:
            ydl.download([target_url])
        for fname in os.listdir(DOWNLOAD_DIR):
            if fname.startswith(f"{video_id}_{prefix}") and not fname.endswith((".jpg", ".part")):
                fp = os.path.join(DOWNLOAD_DIR, fname)
                if os.path.getsize(fp) > 5000:
                    return fp
    except Exception:
        pass

    return None


async def download_song(link: str) -> str:
    video_id = extract_video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    # Check if cached already
    for fname in os.listdir(DOWNLOAD_DIR):
        if (fname.startswith(f"{video_id}_audio") or fname.startswith(f"{video_id}.")) and not fname.endswith((".jpg", ".part")):
            fp = os.path.join(DOWNLOAD_DIR, fname)
            if os.path.getsize(fp) > 10000:
                return fp

    # Try fast Yuki API gateways (Primary and Backup) with fast 3s timeout
    gateways = [g for g in [PRIMARY_API_URL, BACKUP_API_URL] if g]
    for gw in gateways:
        url_candidates = [
            f"{gw}/stream/{video_id}?key={API_KEY}",
            f"{gw}/stream/{video_id}?key={API_KEY}&type=audio&quality=128",
        ]
        for stream_url in url_candidates:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(stream_url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            ctype = resp.headers.get("content-type", "").lower()
                            if "html" in ctype or "text" in ctype:
                                continue
                            file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}_audio.mp3")
                            with open(file_path, "wb") as f:
                                async for chunk in resp.content.iter_chunked(131072):
                                    f.write(chunk)
                            if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
                                with open(file_path, "rb") as check_f:
                                    header = check_f.read(30)
                                    if b"<html" in header.lower() or b"<!doctype" in header.lower():
                                        os.remove(file_path)
                                        continue
                                return file_path
            except Exception:
                continue

    # Seamless fallback to yt-dlp
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _download_ytdlp_sync, link, video_id, True)


async def download_video(link: str) -> str:
    video_id = extract_video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    # Check if cached already
    for fname in os.listdir(DOWNLOAD_DIR):
        if (fname.startswith(f"{video_id}_video") or fname.startswith(f"{video_id}.mp4")) and not fname.endswith((".jpg", ".part")):
            fp = os.path.join(DOWNLOAD_DIR, fname)
            if os.path.getsize(fp) > 10000:
                return fp

    # Try fast Yuki API gateways (Primary and Backup) with fast 4s timeout
    gateways = [g for g in [PRIMARY_API_URL, BACKUP_API_URL] if g]
    for gw in gateways:
        url_candidates = [
            f"{gw}/stream/{video_id}?key={API_KEY}&type=video&quality=480",
            f"{gw}/stream/{video_id}?key={API_KEY}",
        ]
        for stream_url in url_candidates:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(stream_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                        if resp.status == 200:
                            ctype = resp.headers.get("content-type", "").lower()
                            if "html" in ctype or "text" in ctype:
                                continue
                            file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}_video.mp4")
                            with open(file_path, "wb") as f:
                                async for chunk in resp.content.iter_chunked(131072):
                                    f.write(chunk)
                            if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
                                with open(file_path, "rb") as check_f:
                                    header = check_f.read(30)
                                    if b"<html" in header.lower() or b"<!doctype" in header.lower():
                                        os.remove(file_path)
                                        continue
                                return file_path
            except Exception:
                continue

    # Seamless fallback to yt-dlp
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _download_ytdlp_sync, link, video_id, False)


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
                        text = message.text or message.caption
                        return text[entity.offset: entity.offset + entity.length]
            elif getattr(message, "caption_entities", None):
                for entity in message.caption_entities:
                    if MessageEntityType and entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        res_data = await results.next() if hasattr(results, "next") and asyncio.iscoroutinefunction(results.next) else results.result()
        title = "Unknown Track"
        duration_min = "0:00"
        thumbnail = ""
        vidid = ""
        duration_sec = 0
        items = res_data.get("result", []) if isinstance(res_data, dict) else []
        for result in items:
            title = result.get("title", "Unknown Track")
            duration_min = result.get("duration", "0:00")
            thumbs = result.get("thumbnails", [{}])
            thumbnail = thumbs[0].get("url", "").split("?")[0] if thumbs else ""
            vidid = result.get("id", "")
            duration_sec = int(time_to_seconds(duration_min)) if duration_min else 0
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        res_data = await results.next() if hasattr(results, "next") and asyncio.iscoroutinefunction(results.next) else results.result()
        items = res_data.get("result", []) if isinstance(res_data, dict) else []
        for result in items:
            return result.get("title")
        return None

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        res_data = await results.next() if hasattr(results, "next") and asyncio.iscoroutinefunction(results.next) else results.result()
        items = res_data.get("result", []) if isinstance(res_data, dict) else []
        for result in items:
            return result.get("duration")
        return None

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        res_data = await results.next() if hasattr(results, "next") and asyncio.iscoroutinefunction(results.next) else results.result()
        items = res_data.get("result", []) if isinstance(res_data, dict) else []
        for result in items:
            thumbs = result.get("thumbnails", [{}])
            return thumbs[0].get("url", "").split("?")[0] if thumbs else ""
        return None

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
            plist = await Playlist.get(link) if hasattr(Playlist, "get") else {}
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
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        res_data = await results.next() if hasattr(results, "next") and asyncio.iscoroutinefunction(results.next) else results.result()
        title = "Unknown Track"
        duration_min = "0:00"
        vidid = ""
        yturl = link
        thumbnail = ""
        items = res_data.get("result", []) if isinstance(res_data, dict) else []
        for result in items:
            title = result.get("title", title)
            duration_min = result.get("duration", duration_min)
            vidid = result.get("id", vidid)
            yturl = result.get("link", yturl)
            thumbs = result.get("thumbnails", [{}])
            thumbnail = thumbs[0].get("url", "").split("?")[0] if thumbs else ""
        track_details = {
            "title": title,
            "link": yturl,
            "vidid": vidid,
            "duration_min": duration_min,
            "thumb": thumbnail,
        }
        return track_details, vidid

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
        a = VideosSearch(link, limit=10)
        res_data = await a.next() if hasattr(a, "next") and asyncio.iscoroutinefunction(a.next) else a.result()
        items = res_data.get("result", []) if isinstance(res_data, dict) else []
        if items and query_type < len(items):
            result = items[query_type]
            title = result.get("title", "Unknown")
            duration_min = result.get("duration", "0:00")
            vidid = result.get("id", "")
            thumbs = result.get("thumbnails", [{}])
            thumbnail = thumbs[0].get("url", "").split("?")[0] if thumbs else ""
            return title, duration_min, thumbnail, vidid
        return "Unknown", "0:00", "", ""

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
            if video:
                downloaded_file = await download_video(link)
            else:
                downloaded_file = await download_song(link)
            if downloaded_file:
                return downloaded_file, True
            return None, False
        except Exception:
            return None, False


YouTube = YouTubeAPI()
