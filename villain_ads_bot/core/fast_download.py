"""
Fast Parallel Media Downloader for Telethon.
Uses multiple concurrent MTProto connections with 512 KB chunks (MAX_CHUNK_SIZE)
to achieve 5x - 10x faster download speeds for Telegram media (videos, audios, voice notes).
Falls back seamlessly to standard client.download_media if needed.
"""
import asyncio
import inspect
import logging
import math
import os
import time
from typing import Optional, Callable, Union

from telethon import utils, helpers, TelegramClient
from telethon.crypto import AuthKey
from telethon.network import MTProtoSender
from telethon.tl import types
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import ExportAuthorizationRequest, ImportAuthorizationRequest
from telethon.tl.functions.upload import GetFileRequest

logger = logging.getLogger("villain_ads_bot.core.fast_download")

CHUNK_SIZE = 512 * 1024  # 512 KB (Maximum allowed by Telegram MTProto)
MIN_PARALLEL_FILE_SIZE = 2 * 1024 * 1024  # 2 MB threshold for parallelization


def _get_extension_from_mime(mime_type: str, default: str = ".bin") -> str:
    """Extract standard file extension from mime type string."""
    if not mime_type:
        return default
    mime = mime_type.lower()
    if "video/mp4" in mime or "video/" in mime:
        return ".mp4"
    if "audio/ogg" in mime or "application/ogg" in mime:
        return ".ogg"
    if "audio/mpeg" in mime or "audio/mp3" in mime or "audio/" in mime:
        return ".mp3"
    if "image/jpeg" in mime or "image/jpg" in mime:
        return ".jpg"
    if "image/png" in mime:
        return ".png"
    return default


def _resolve_target_path(message_or_media, target_dir_or_file: str) -> str:
    """
    Resolves the destination filepath ensuring directories exist and avoiding collisions.
    """
    target = target_dir_or_file or "downloads/"
    is_dir = False
    if target.endswith("/") or target.endswith("\\") or os.path.isdir(target):
        is_dir = True
        directory = target
        os.makedirs(directory, exist_ok=True)
    else:
        directory, filename = os.path.split(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if filename:
            return os.path.abspath(target)
        is_dir = True

    # Extract filename from document attributes or mime_type
    doc = getattr(message_or_media, "document", None) or getattr(message_or_media, "media", None)
    if isinstance(doc, types.MessageMediaDocument):
        doc = doc.document

    suggested_name = None
    ext = ".mp4"

    if isinstance(doc, types.Document):
        ext = _get_extension_from_mime(getattr(doc, "mime_type", ""), default=".mp4")
        if doc.attributes:
            for attr in doc.attributes:
                if isinstance(attr, types.DocumentAttributeFilename) and attr.file_name:
                    suggested_name = os.path.basename(attr.file_name)
                    break

    if not suggested_name:
        doc_id = getattr(doc, "id", None) or getattr(message_or_media, "id", int(time.time()))
        suggested_name = f"media_{doc_id}_{int(time.time())}{ext}"

    target_path = os.path.join(directory, suggested_name)
    base, extension = os.path.splitext(target_path)
    counter = 1
    final_path = target_path
    while os.path.exists(final_path):
        final_path = f"{base}_{counter}{extension}"
        counter += 1

    return os.path.abspath(final_path)


class _ParallelDownloader:
    """
    Manages parallel MTProtoSender connections to fetch chunks concurrently.
    """
    def __init__(self, client: TelegramClient, dc_id: int):
        self.client = client
        self.dc_id = dc_id
        self.loop = client.loop or asyncio.get_event_loop()
        self.auth_key: Optional[AuthKey] = (
            client.session.auth_key if dc_id == client.session.dc_id else None
        )
        self.senders: list[MTProtoSender] = []

    async def create_sender(self) -> MTProtoSender:
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log)
        await sender.connect(self.client._connection(
            dc.ip_address,
            dc.port,
            dc.id,
            loggers=self.client._log,
            proxy=self.client._proxy,
            local_addr=getattr(self.client, "_local_addr", None)
        ))

        if not self.auth_key:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            self.client._init_request.query = ImportAuthorizationRequest(
                id=auth.id,
                bytes=auth.bytes
            )
            req = InvokeWithLayerRequest(LAYER, self.client._init_request)
            await sender.send(req)
            self.auth_key = sender.auth_key

        self.senders.append(sender)
        return sender

    async def cleanup(self):
        if self.senders:
            tasks = [sender.disconnect() for sender in self.senders if sender]
            await asyncio.gather(*tasks, return_exceptions=True)
            self.senders.clear()


async def download_parallel(
    client: TelegramClient,
    location: types.TypeInputFileLocation,
    file_size: int,
    dc_id: int,
    target_path: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    connection_count: Optional[int] = None
) -> str:
    """
    Downloads a Telegram file in parallel chunks using multiple MTProto senders.
    """
    part_size = CHUNK_SIZE
    part_count = math.ceil(file_size / part_size)

    # Determine optimal worker count (4-5 workers maximizes throughput without flood issues)
    if connection_count is None:
        if file_size >= 40 * 1024 * 1024:
            connection_count = 5
        elif file_size >= 10 * 1024 * 1024:
            connection_count = 4
        else:
            connection_count = 3
    connection_count = max(2, min(connection_count, part_count, 6))

    downloader = _ParallelDownloader(client, dc_id)
    temp_path = f"{target_path}.{os.getpid()}_{int(time.time()*1000)}.tmp"

    try:
        # Connect initial sender (handles auth export/import if cross-DC)
        first_sender = await downloader.create_sender()
        senders = [first_sender]
        if connection_count > 1:
            more_senders = await asyncio.gather(
                *[downloader.create_sender() for _ in range(connection_count - 1)],
                return_exceptions=True
            )
            for s in more_senders:
                if isinstance(s, MTProtoSender):
                    senders.append(s)

        queue: asyncio.Queue = asyncio.Queue()
        for idx in range(part_count):
            offset = idx * part_size
            queue.put_nowait((idx, offset))

        downloaded_bytes = 0
        file_lock = asyncio.Lock()

        with open(temp_path, "wb") as out_f:
            out_f.truncate(file_size)

            async def _worker(sender: MTProtoSender):
                nonlocal downloaded_bytes
                while True:
                    try:
                        idx, offset = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    req = GetFileRequest(location=location, offset=offset, limit=part_size)
                    res = await client._call(sender, req)
                    chunk_bytes = getattr(res, "bytes", None)
                    if chunk_bytes is None:
                        raise RuntimeError(f"Unexpected GetFile response: {type(res)}")

                    async with file_lock:
                        out_f.seek(offset)
                        out_f.write(chunk_bytes)
                        downloaded_bytes += len(chunk_bytes)
                        current_total = downloaded_bytes

                    if progress_callback:
                        try:
                            cb_res = progress_callback(min(current_total, file_size), file_size)
                            if inspect.isawaitable(cb_res):
                                await cb_res
                        except Exception:
                            pass

                    queue.task_done()

            worker_tasks = [asyncio.create_task(_worker(s)) for s in senders]
            await asyncio.gather(*worker_tasks)

        if os.path.exists(target_path):
            try:
                os.remove(target_path)
            except Exception:
                pass
        os.replace(temp_path, target_path)

        if progress_callback:
            try:
                cb_res = progress_callback(file_size, file_size)
                if inspect.isawaitable(cb_res):
                    await cb_res
            except Exception:
                pass

        return target_path

    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        raise
    finally:
        await downloader.cleanup()


async def fast_download_media(
    client: TelegramClient,
    message_or_media,
    file: str = "downloads/",
    progress_callback: Optional[Callable[[int, int], None]] = None,
    connection_count: Optional[int] = None
) -> Optional[str]:
    """
    High-speed downloader for Telegram media with parallel MTProto chunks.
    Falls back gracefully to client.download_media if any error or condition is not met.
    """
    if not client or not message_or_media:
        return None

    try:
        info = utils._get_file_info(message_or_media)
    except Exception:
        info = None

    # If file cannot be inspected or size is small (< 2 MB), use standard download_media
    if not info or not info.location or not info.size or info.size < MIN_PARALLEL_FILE_SIZE:
        return await client.download_media(
            message_or_media,
            file=file,
            progress_callback=progress_callback
        )

    # Resolve target file path
    target_path = _resolve_target_path(message_or_media, file)
    dc_id = info.dc_id or client.session.dc_id

    try:
        logger.info(
            f"Initiating fast parallel download: size={info.size / (1024*1024):.2f} MB, "
            f"dc_id={dc_id}, target='{target_path}'"
        )
        return await download_parallel(
            client=client,
            location=info.location,
            file_size=info.size,
            dc_id=dc_id,
            target_path=target_path,
            progress_callback=progress_callback,
            connection_count=connection_count
        )
    except Exception as e:
        logger.warning(
            f"Fast parallel download failed ({e}), falling back to standard client.download_media..."
        )
        return await client.download_media(
            message_or_media,
            file=file,
            progress_callback=progress_callback
        )
