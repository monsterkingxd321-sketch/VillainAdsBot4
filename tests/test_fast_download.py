import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import types


class TestFastDownload(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()

    async def asyncTearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extension_resolution(self):
        from villain_ads_bot.core.fast_download import _get_extension_from_mime
        self.assertEqual(_get_extension_from_mime("video/mp4"), ".mp4")
        self.assertEqual(_get_extension_from_mime("audio/ogg"), ".ogg")
        self.assertEqual(_get_extension_from_mime("audio/mpeg"), ".mp3")
        self.assertEqual(_get_extension_from_mime("image/jpeg"), ".jpg")
        self.assertEqual(_get_extension_from_mime("unknown/xyz", default=".dat"), ".dat")

    def test_resolve_target_path(self):
        from villain_ads_bot.core.fast_download import _resolve_target_path
        # Directory path
        doc = types.Document(
            id=12345,
            access_hash=67890,
            file_reference=b"",
            date=None,
            mime_type="video/mp4",
            size=1024,
            dc_id=2,
            attributes=[types.DocumentAttributeFilename(file_name="awesome_video.mp4")]
        )
        msg = types.Message(id=99, peer_id=types.PeerUser(1), media=types.MessageMediaDocument(document=doc), message="", date=None)
        
        path = _resolve_target_path(msg, self.test_dir + "/")
        self.assertTrue(path.endswith("awesome_video.mp4"))
        self.assertTrue(os.path.dirname(path).startswith(os.path.abspath(self.test_dir)))

    async def test_download_parallel_mock(self):
        from villain_ads_bot.core.fast_download import download_parallel, CHUNK_SIZE

        # Create 1.5 MB test data (3 chunks of 512KB)
        total_size = int(CHUNK_SIZE * 2.5)  # 2 full chunks + 1 half chunk = 1,310,720 bytes
        test_bytes = os.urandom(total_size)

        mock_client = MagicMock()
        mock_client.loop = asyncio.get_event_loop()
        mock_client.session.dc_id = 2
        mock_client.session.auth_key = MagicMock()
        mock_client._log = MagicMock()
        mock_client._connection = MagicMock()
        mock_client._proxy = None
        mock_client._local_addr = None

        mock_sender = MagicMock()
        mock_sender.disconnect = AsyncMock()

        async def fake_call(sender, req):
            offset = req.offset
            limit = req.limit
            chunk = test_bytes[offset:offset + limit]
            res = MagicMock()
            res.bytes = chunk
            return res

        mock_client._call = AsyncMock(side_effect=fake_call)

        target_file = os.path.join(self.test_dir, "output.mp4")
        mock_location = types.InputDocumentFileLocation(id=1, access_hash=2, file_reference=b"", thumb_size="")

        progress_calls = []
        def on_progress(current, total):
            progress_calls.append((current, total))

        with patch("villain_ads_bot.core.fast_download._ParallelDownloader.create_sender", AsyncMock(return_value=mock_sender)):
            res_path = await download_parallel(
                mock_client,
                mock_location,
                total_size,
                dc_id=2,
                target_path=target_file,
                progress_callback=on_progress,
                connection_count=3
            )

        self.assertEqual(res_path, target_file)
        self.assertTrue(os.path.exists(target_file))
        self.assertEqual(os.path.getsize(target_file), total_size)

        with open(target_file, "rb") as f:
            read_bytes = f.read()
        self.assertEqual(read_bytes, test_bytes)
        self.assertTrue(len(progress_calls) > 0)
        self.assertEqual(progress_calls[-1], (total_size, total_size))
        print("MOCK PARALLEL DOWNLOAD VERIFIED 100% BIT-EXACT!")

    async def test_fast_download_media_fallback_small_file(self):
        from villain_ads_bot.core.fast_download import fast_download_media

        mock_client = MagicMock()
        mock_client.download_media = AsyncMock(return_value="/downloads/small.mp3")

        # Small 500 KB document
        small_doc = types.Document(
            id=111,
            access_hash=222,
            file_reference=b"",
            date=None,
            mime_type="audio/mpeg",
            size=500 * 1024,
            dc_id=2,
            attributes=[]
        )
        small_msg = types.Message(id=1, peer_id=types.PeerUser(1), media=types.MessageMediaDocument(document=small_doc), message="", date=None)

        result = await fast_download_media(mock_client, small_msg, file="downloads/")
        self.assertEqual(result, "/downloads/small.mp3")
        mock_client.download_media.assert_awaited_once()

    async def test_fast_download_media_fallback_on_exception(self):
        from villain_ads_bot.core.fast_download import fast_download_media

        mock_client = MagicMock()
        mock_client.session.dc_id = 2
        mock_client.download_media = AsyncMock(return_value="/downloads/fallback.mp4")

        # Large 50 MB document
        large_doc = types.Document(
            id=999,
            access_hash=888,
            file_reference=b"",
            date=None,
            mime_type="video/mp4",
            size=50 * 1024 * 1024,
            dc_id=2,
            attributes=[]
        )
        large_msg = types.Message(id=2, peer_id=types.PeerUser(1), media=types.MessageMediaDocument(document=large_doc), message="", date=None)

        with patch("villain_ads_bot.core.fast_download.download_parallel", AsyncMock(side_effect=RuntimeError("Network error"))):
            result = await fast_download_media(mock_client, large_msg, file="downloads/")
            self.assertEqual(result, "/downloads/fallback.mp4")
            mock_client.download_media.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
