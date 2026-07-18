import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.transcription_service import TranscriptionService


class FakeMeetily:
    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.transcribed: list[tuple[Path, str | None]] = []
        self.saved: list[tuple[str, list]] = []
        self.processed: list[tuple[str, str]] = []

    def is_configured(self) -> bool:
        return self.configured

    async def transcribe_audio(self, audio_path, language=None):
        self.transcribed.append((Path(audio_path), language))
        return SimpleNamespace(
            text="meetily transcript",
            segments=[{"text": "meetily transcript", "offsets": {"from": 0, "to": 900}}],
            source="meetily-whisper",
        )

    async def save_transcript(self, title, segments):
        self.saved.append((title, segments))
        return "meeting-77"

    async def process_transcript(self, text, meeting_id, provider=None, model=None):
        self.processed.append((text, meeting_id))
        return meeting_id


class FakeSTT:
    def __init__(self, configured: bool = True) -> None:
        self.configured = configured
        self.calls: list[Path] = []

    def is_configured(self) -> bool:
        return self.configured

    def transcribe(self, recording_path):
        self.calls.append(Path(recording_path))
        return SimpleNamespace(text="local transcript", model="medium", language="en")


def make_settings(tmpdir: str) -> SimpleNamespace:
    return SimpleNamespace(ffmpeg_binary="ffmpeg", voice_inbox_dir=Path(tmpdir))


class TranscriptionServiceTests(unittest.TestCase):
    def test_uses_meetily_whisper_saves_meeting_and_summarizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "note.wav"
            audio.write_bytes(b"fake")
            meetily = FakeMeetily()
            service = TranscriptionService(make_settings(tmpdir), meetily, stt_adapter=FakeSTT())

            result = asyncio.run(
                service.transcribe_voice_message(audio, source="telegram", summarize=True)
            )

        self.assertEqual(result.text, "meetily transcript")
        self.assertEqual(result.engine, "meetily-whisper")
        self.assertEqual(result.source, "telegram")
        self.assertEqual(result.meeting_id, "meeting-77")
        self.assertTrue(result.summary_requested)
        self.assertEqual(meetily.transcribed[0][0], audio)
        self.assertIn("[telegram]", meetily.saved[0][0])
        self.assertEqual(meetily.processed[0], ("meetily transcript", "meeting-77"))

    def test_falls_back_to_local_stt_when_meetily_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "note.wav"
            audio.write_bytes(b"fake")
            stt = FakeSTT()
            service = TranscriptionService(make_settings(tmpdir), FakeMeetily(configured=False), stt_adapter=stt)

            result = asyncio.run(service.transcribe_voice_message(audio, source="whatsapp"))

        self.assertEqual(result.text, "local transcript")
        self.assertEqual(result.engine, "local-whisper")
        self.assertIsNone(result.meeting_id)
        self.assertFalse(result.summary_requested)
        self.assertEqual(stt.calls, [audio])

    def test_converts_non_wav_audio_with_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "note.ogg"
            audio.write_bytes(b"fake-ogg")
            meetily = FakeMeetily()
            service = TranscriptionService(make_settings(tmpdir), meetily)
            seen: dict = {}

            def fake_run(command, check, capture_output):
                seen["command"] = command
                Path(command[-1]).write_bytes(b"fake-wav")
                return SimpleNamespace(returncode=0)

            with patch("app.transcription_service.subprocess.run", side_effect=fake_run):
                result = asyncio.run(service.transcribe_voice_message(audio, source="whatsapp"))

        self.assertEqual(seen["command"][0], "ffmpeg")
        self.assertIn(str(audio), seen["command"])
        transcribed_path = meetily.transcribed[0][0]
        self.assertEqual(transcribed_path.suffix, ".wav")
        self.assertEqual(result.text, "meetily transcript")

    def test_rejects_audio_outside_allowed_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as outside:
            audio = Path(outside) / "secret.wav"
            audio.write_bytes(b"fake")
            service = TranscriptionService(make_settings(tmpdir), FakeMeetily())
            with self.assertRaises(PermissionError):
                asyncio.run(service.transcribe_voice_message(audio, source="pstn"))

    def test_raises_when_no_backend_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audio = Path(tmpdir) / "note.wav"
            audio.write_bytes(b"fake")
            service = TranscriptionService(
                make_settings(tmpdir), FakeMeetily(configured=False), stt_adapter=FakeSTT(configured=False)
            )
            with self.assertRaises(RuntimeError):
                asyncio.run(service.transcribe_voice_message(audio))


if __name__ == "__main__":
    unittest.main()
