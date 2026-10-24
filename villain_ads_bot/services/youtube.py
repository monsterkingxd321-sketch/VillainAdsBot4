import asyncio
import os
import re
from typing import Union
import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from py_yt import VideosSearch, Playlist
import aiohttp

# Bootstrap: make sure the values from .env are present in os.environ even
# when this module is imported before config.py. Without this the API key
# would fall back to the "YOUR_API_KEY" placeholder and no song would play.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # pragma: no cover
    pass

API_URL = os.environ.get("MEOW_API_URL", "https://music.yukiapi.site")
API_KEY = os.environ.get("MEOW_API_KEY", "yuki_8641adadf64870f3065dd502c8ee30f1")

DOWNLOAD_DIR = "downloads"

# Resolve the download folder to an absolute path so the media always ends up
# inside the project directory - no matter which working directory the bot
# was started from. (The value above is kept as the fallback.)
try:
    from villain_ads_bot import config as _config
    if os.path.isdir(getattr(_config, "DOWNLOADS_DIR", "")):
        DOWNLOAD_DIR = _config.DOWNLOADS_DIR
except Exception:
    pass


def _get_credentials():
    try:
        from villain_ads_bot.core import music as _music
        from villain_ads_bot import config as _config
        url, key, backup = _music.get_configured_credentials()
        active_key = key or getattr(_config, "YUKI_API_KEY", None) or API_KEY
        primary = url or API_URL
        backup_url = backup or getattr(_config, "YUKI_BACKUP_API_URL", "https://play.yukiapi.site")
        return primary.rstrip("/"), active_key, backup_url.rstrip("/")
    except Exception:
        return API_URL, API_KEY, "https://play.yukiapi.site"


def _is_valid_video_file(fpath: str) -> bool:
    if not os.path.exists(fpath) or os.path.getsize(fpath) < 10000:
        return False
    try:
        import subprocess
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_type,width,height", "-of", "csv=s=x:p=0", fpath]
        res = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=5).decode().strip()
        return ("video" in res or "x" in res) and len(res) > 0
    except Exception:
        return False


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
    return link.split("v=")[-1].split("&")[0].split("?")[0].strip()


async def download_song(link: str) -> str:
    video_id = extract_video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp3")

    if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
        return file_path

    primary, key, backup = _get_credentials()
    gateways = [primary]
    if backup and backup not in gateways:
        gateways.append(backup)

    for gateway in gateways:
        try:
            timeout = aiohttp.ClientTimeout(total=25, connect=5)
            async with aiohttp.ClientSession(headers={"Accept-Encoding": "gzip, deflate"}) as session:
                stream_url = f"{gateway}/stream/{video_id}?key={key}&type=audio"
                async with session.get(stream_url, timeout=timeout) as resp:
                    if resp.status == 200:
                        with open(file_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(131072):
                                f.write(chunk)
                        if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
                            return file_path
        except Exception:
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except Exception: pass
            continue

    # Non-blocking async yt-dlp fallback
    def _dl_ytdlp():
        try:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': file_path,
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'geo_bypass': True,
                'extractor_args': {'youtube': {'player_client': ['mweb', 'android']}},
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            if os.path.exists(file_path) and os.path.getsize(file_path) > 10000:
                return file_path
        except Exception:
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except Exception: pass
        return None

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _dl_ytdlp)
    except Exception:
        return _dl_ytdlp()


async def download_video(link: str) -> str:
    video_id = extract_video_id(link)
    if not video_id or len(video_id) < 3:
        return None

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    file_path = os.path.join(DOWNLOAD_DIR, f"{video_id}.mp4")

    if _is_valid_video_file(file_path):
        return file_path

    primary, key, backup = _get_credentials()
    gateways = [primary]
    if backup and backup not in gateways:
        gateways.append(backup)

    # 1. Try Yuki API stream endpoint
    for gateway in gateways:
        try:
            timeout = aiohttp.ClientTimeout(total=45, connect=6)
            async with aiohttp.ClientSession(headers={"Accept-Encoding": "gzip, deflate"}) as session:
                stream_url = f"{gateway}/stream/{video_id}?key={key}&type=video"
                async with session.get(stream_url, timeout=timeout) as resp:
                    if resp.status == 200:
                        with open(file_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(131072):
                                f.write(chunk)
                        if _is_valid_video_file(file_path):
                            return file_path
                        # Discard if not valid video
                        if os.path.exists(file_path):
                            try: os.remove(file_path)
                            except Exception: pass
        except Exception:
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except Exception: pass
            continue

    # 2. Resilient non-blocking fallback to yt-dlp to download real 720p/360p video
    def _dl_video_ytdlp():
        try:
            ydl_opts = {
                'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
                'outtmpl': file_path,
                'merge_output_format': 'mp4',
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'geo_bypass': True,
                'extractor_args': {'youtube': {'player_client': ['mweb', 'android']}},
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            if _is_valid_video_file(file_path):
                return file_path
        except Exception:
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except Exception: pass
        return None

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _dl_video_ytdlp)
    except Exception:
        return _dl_video_ytdlp()


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
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        for message in messages:
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        return text[entity.offset: entity.offset + entity.length]
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            vidid = result["id"]
            duration_sec = int(time_to_seconds(duration_min)) if duration_min else 0
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["title"]

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["duration"]

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["thumbnails"][0]["url"].split("?")[0]

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
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            vidid = result["id"]
            yturl = result["link"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
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
            for format in r["formats"]:
                try:
                    if "dash" not in str(format["format"]).lower():
                        formats_available.append(
                            {
                                "format": format["format"],
                                "filesize": format.get("filesize"),
                                "format_id": format["format_id"],
                                "ext": format["ext"],
                                "format_note": format["format_note"],
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
        result = (await a.next()).get("result")
        title = result[query_type]["title"]
        duration_min = result[query_type]["duration"]
        vidid = result[query_type]["id"]
        thumbnail = result[query_type]["thumbnails"][0]["url"].split("?")[0]
        return title, duration_min, thumbnail, vidid

    async def download(
        self,
        link: str,
        mystic,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> str:
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
