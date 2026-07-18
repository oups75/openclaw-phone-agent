"""TDD tests for automatic call-recording transcription via Meetily."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.ari_client import AriClient


class FakeDb:
    def __init__(self):
        self.transcripts = []
        self.events = []
        self.recordings = []

    def upsert_call(self, call):
        pass

    def add_event(self, call_id, event_type, payload):
        self.events.append((call_id, event_type, payload))

    def update_call_status(self, *args, **kwargs):
        pass

    def add_decision(self, *args, **kwargs):
        pass

    def add_recording(self, *args, **kwargs):
        self.recordings.append(args)

    def add_transcript(self, *args, **kwargs):
        self.transcripts.append(args)

    def get_call(self, call_id):
        return None


class FakeTranscriptionService:
    def __init__(self, meeting_id="meeting-auto-99"):
        self.calls = []
        self._meeting_id = meeting_id

    async def transcribe_voice_message(
        self, audio_path, *, source="unknown", title=None, summarize=False, language=None
    ):
        self.calls.append({"path": audio_path, "source": source})
        return SimpleNamespace(
            text="auto transcript",
            source=source,
            engine="meetily-whisper",
            meeting_id=self._meeting_id,
            summary_requested=False,
            segments=[],
        )


class AutoTranscriptionTests(unittest.IsolatedAsyncioTestCase):
    def _make_client(self, tmpdir, *, meetily_enabled=True, stt=None, ts=None):
        settings = SimpleNamespace(
            meetily_enabled=meetily_enabled,
            phone_agent_mixmonitor_dir=Path(tmpdir),
        )
        stt = stt or SimpleNamespace(is_configured=lambda: False)
        ts = ts or FakeTranscriptionService()
        return AriClient(settings, FakeDb(), SimpleNamespace(), SimpleNamespace(), stt,
                         transcription_service=ts), ts

    async def test_meetily_transcription_used_when_enabled(self):
        """_transcribe_call uses TranscriptionService (not STT) when meetily_enabled=True."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, ts = self._make_client(tmpdir)
            recording = Path(tmpdir) / "call-m.wav"
            recording.write_bytes(b"fake-audio")
            with patch.object(client, "_mixmonitor_recording_path", return_value=recording):
                await client._transcribe_call("call-m")

        self.assertEqual(len(ts.calls), 1)
        self.assertEqual(ts.calls[0]["source"], "pstn")

    async def test_meetily_transcript_stored_in_db(self):
        """Transcript text from TranscriptionService is persisted via db.add_transcript."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, ts = self._make_client(tmpdir)
            db = client.db
            recording = Path(tmpdir) / "call-db.wav"
            recording.write_bytes(b"fake-audio")
            with patch.object(client, "_mixmonitor_recording_path", return_value=recording):
                await client._transcribe_call("call-db")

        self.assertEqual(len(db.transcripts), 1)
        self.assertEqual(db.transcripts[0][0], "call-db")
        self.assertEqual(db.transcripts[0][2], "auto transcript")

    async def test_meetily_meeting_id_emitted_in_event(self):
        """meeting_id returned by TranscriptionService is recorded in a MeetilyTranscriptCreated event."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, ts = self._make_client(tmpdir)
            db = client.db
            recording = Path(tmpdir) / "call-evt.wav"
            recording.write_bytes(b"fake-audio")
            with patch.object(client, "_mixmonitor_recording_path", return_value=recording):
                await client._transcribe_call("call-evt")

        event_types = [e[1] for e in db.events]
        self.assertIn("MeetilyTranscriptCreated", event_types)
        payload = json.loads(next(e[2] for e in db.events if e[1] == "MeetilyTranscriptCreated"))
        self.assertEqual(payload["meeting_id"], "meeting-auto-99")

    async def test_falls_back_to_stt_when_meetily_disabled(self):
        """When meetily_enabled=False, _transcribe_call uses the existing STT path."""
        import tempfile
        from app.stt_adapter import TranscriptResult

        stt_calls = []

        class FakeStt:
            def is_configured(self):
                return True

            def transcribe(self, path):
                stt_calls.append(path)
                return TranscriptResult(text="stt fallback", model="medium", language="en")

        with tempfile.TemporaryDirectory() as tmpdir:
            ts = FakeTranscriptionService()
            client, _ = self._make_client(tmpdir, meetily_enabled=False, stt=FakeStt(), ts=ts)
            recording = Path(tmpdir) / "call-stt.wav"
            recording.write_bytes(b"fake-audio")
            with patch.object(client, "_mixmonitor_recording_path", return_value=recording):
                async def fake_to_thread(func, *args, **kwargs):
                    return func(*args, **kwargs)
                with patch("app.ari_client.asyncio.to_thread", new=fake_to_thread):
                    await client._transcribe_call("call-stt")

        self.assertEqual(len(ts.calls), 0, "TranscriptionService must NOT be called when meetily_enabled=False")
        self.assertEqual(len(stt_calls), 1)

    async def test_skips_transcript_when_recording_missing_meetily_enabled(self):
        """Missing recording emits TranscriptSkipped even when meetily_enabled=True."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            client, ts = self._make_client(tmpdir)
            db = client.db
            missing = Path(tmpdir) / "nonexistent.wav"
            with patch.object(client, "_mixmonitor_recording_path", return_value=missing):
                await client._transcribe_call("call-missing")

        self.assertEqual(len(ts.calls), 0)
        event_types = [e[1] for e in db.events]
        self.assertIn("TranscriptSkipped", event_types)

    async def test_meetily_error_emits_transcript_failed(self):
        """When TranscriptionService raises, a TranscriptFailed event is emitted."""
        import tempfile

        class ErrorTs:
            async def transcribe_voice_message(self, *a, **kw):
                raise RuntimeError("whisper down")

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = SimpleNamespace(
                meetily_enabled=True,
                phone_agent_mixmonitor_dir=Path(tmpdir),
            )
            db = FakeDb()
            client = AriClient(settings, db, SimpleNamespace(), SimpleNamespace(),
                               SimpleNamespace(is_configured=lambda: False),
                               transcription_service=ErrorTs())
            recording = Path(tmpdir) / "call-err.wav"
            recording.write_bytes(b"fake-audio")
            with patch.object(client, "_mixmonitor_recording_path", return_value=recording):
                await client._transcribe_call("call-err")

        event_types = [e[1] for e in db.events]
        self.assertIn("TranscriptFailed", event_types)


if __name__ == "__main__":
    unittest.main()
