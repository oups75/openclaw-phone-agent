from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from app.settings import Settings


class TTSAdapter:
    """Local TTS helper.

    This prepares a WAV file with Piper when a local model path is configured.
    The generated file is stored locally for inspection, but the current MVP
    does not automatically install it into an Asterisk sounds directory.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def synthesize(self, text: str, utterance_id: str) -> Path | None:
        if not self.is_configured():
            return None

        output_dir = self.settings.tts_output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_output = output_dir / f"{utterance_id}.raw.wav"
        final_output = output_dir / f"{utterance_id}.wav"

        command = [
            self.settings.tts_piper_binary,
            "--model",
            str(self.settings.tts_piper_model_path),
            "--output-file",
            str(raw_output),
        ]
        if self.settings.tts_piper_config_path:
            command.extend(["--config", str(self.settings.tts_piper_config_path)])

        subprocess.run(
            command,
            input=text,
            text=True,
            check=True,
            capture_output=True,
        )

        subprocess.run(
            [
                shutil.which("ffmpeg") or "ffmpeg",
                "-y",
                "-i",
                str(raw_output),
                "-ar",
                "8000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(final_output),
            ],
            check=True,
            capture_output=True,
        )
        raw_output.unlink(missing_ok=True)
        return final_output

    def is_configured(self) -> bool:
        return bool(
            self.settings.tts_enabled
            and Path(self.settings.tts_piper_binary).exists()
            and self.settings.tts_piper_model_path
            and Path(self.settings.tts_piper_model_path).exists()
        )
