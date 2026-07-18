import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.meetily_adapter import MeetilyClient


def make_settings(**overrides) -> SimpleNamespace:
    base = {
        "meetily_enabled": True,
        "meetily_backend_url": "http://meetily:5167",
        "meetily_whisper_url": "http://meetily:8178",
        "meetily_timeout_seconds": 30,
        "meetily_summary_provider": "ollama",
        "meetily_summary_model": "llama3.2",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class MeetilyClientTests(unittest.TestCase):
    def test_is_configured_false_when_disabled(self) -> None:
        client = MeetilyClient(make_settings(meetily_enabled=False))
        self.assertFalse(client.is_configured())

    def test_is_configured_true_when_enabled(self) -> None:
        client = MeetilyClient(make_settings())
        self.assertTrue(client.is_configured())

    def test_transcribe_audio_posts_multipart_to_whisper_inference(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["host"] = request.url.host
            seen["body"] = request.content
            return httpx.Response(
                200,
                json={
                    "text": "  hello from meetily  ",
                    "segments": [{"text": "hello from meetily", "offsets": {"from": 0, "to": 1200}}],
                },
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "note.wav"
            audio.write_bytes(b"fake-audio-bytes")
            client = MeetilyClient(make_settings(), transport=httpx.MockTransport(handler))
            result = asyncio.run(client.transcribe_audio(audio, language="en"))

        self.assertEqual(seen["path"], "/inference")
        self.assertEqual(seen["host"], "meetily")
        self.assertIn(b"fake-audio-bytes", seen["body"])
        self.assertIn(b'name="audio_file"', seen["body"])
        self.assertEqual(result.text, "hello from meetily")
        self.assertEqual(len(result.segments), 1)
        self.assertEqual(result.source, "meetily-whisper")

    def test_save_transcript_returns_meeting_id(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "success", "meeting_id": "meeting-42"})

        client = MeetilyClient(make_settings(), transport=httpx.MockTransport(handler))
        segments = [{"text": "hello", "timestamp": "2026-07-17T10:00:00+00:00"}]
        meeting_id = asyncio.run(client.save_transcript("[telegram] voice note", segments))

        self.assertEqual(seen["path"], "/save-transcript")
        self.assertEqual(seen["json"]["meeting_title"], "[telegram] voice note")
        self.assertEqual(seen["json"]["transcripts"], segments)
        self.assertEqual(meeting_id, "meeting-42")

    def test_process_transcript_uses_settings_defaults(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["json"] = json.loads(request.content)
            return httpx.Response(202, json={"message": "ok", "process_id": "meeting-42"})

        client = MeetilyClient(make_settings(), transport=httpx.MockTransport(handler))
        process_id = asyncio.run(client.process_transcript("full text", "meeting-42"))

        self.assertEqual(seen["path"], "/process-transcript")
        self.assertEqual(seen["json"]["model"], "ollama")
        self.assertEqual(seen["json"]["model_name"], "llama3.2")
        self.assertEqual(seen["json"]["meeting_id"], "meeting-42")
        self.assertEqual(seen["json"]["text"], "full text")
        self.assertEqual(process_id, "meeting-42")

    def test_get_summary_returns_payload_for_processing_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(202, json={"status": "processing", "meeting_id": "meeting-42"})

        client = MeetilyClient(make_settings(), transport=httpx.MockTransport(handler))
        summary = asyncio.run(client.get_summary("meeting-42"))
        self.assertEqual(summary["status"], "processing")

    def test_search_posts_query(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["json"] = json.loads(request.content)
            return httpx.Response(200, json=[{"id": "meeting-42", "title": "t", "matchContext": "x"}])

        client = MeetilyClient(make_settings(), transport=httpx.MockTransport(handler))
        results = asyncio.run(client.search("invoice"))

        self.assertEqual(seen["path"], "/search-transcripts")
        self.assertEqual(seen["json"], {"query": "invoice"})
        self.assertEqual(results[0]["id"], "meeting-42")


if __name__ == "__main__":
    unittest.main()
