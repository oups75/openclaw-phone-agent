import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.stt_adapter import STTAdapter


class STTAdapterTests(unittest.TestCase):
    def test_transcribe_reads_whisper_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            recording = tmp_path / "call.wav"
            recording.write_bytes(b"fake-audio")
            output_dir = tmp_path / ".stt"
            output_dir.mkdir()
            result_file = output_dir / "call.json"

            def fake_run(command, check, capture_output, text, env):
                result_file.write_text(json.dumps({"text": "hello there"}))
                return SimpleNamespace(returncode=0)

            settings = SimpleNamespace(
                stt_enabled=True,
                stt_whisper_binary="/home/soloway/.local/bin/whisper",
                stt_whisper_model="medium",
                stt_whisper_model_dir=Path("/home/soloway/.cache/whisper"),
                stt_whisper_language="en",
                stt_ld_library_path="/media/soloway/workspace/Devel/Tools/ai/xtts/venv/lib/python3.14/site-packages/nvidia/cusparselt/lib",
            )
            adapter = STTAdapter(settings)

            with patch("app.stt_adapter.subprocess.run", side_effect=fake_run):
                transcript = adapter.transcribe(recording)

            self.assertEqual(transcript.text, "hello there")
            self.assertEqual(transcript.model, "medium")
            self.assertEqual(transcript.language, "en")


if __name__ == "__main__":
    unittest.main()
