from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.settings import Settings


@dataclass(slots=True)
class TranscriptResult:
    text: str
    model: str
    language: str
    source: str = "whisper-cli"


class STTAdapter:
    """Local offline speech-to-text adapter.

    This shells out to the host `whisper` CLI and keeps all audio local.
    The current machine already has cached Whisper models, so no cloud call is
    needed. The LD_LIBRARY_PATH override points at the local torch/CUDA runtime
    that makes the bundled whisper environment work here.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, recording_path: str | Path) -> TranscriptResult:
        if not self.is_configured():
            raise RuntimeError("STT is disabled or not configured")

        recording = Path(recording_path)
        if not recording.exists():
            raise FileNotFoundError(recording)

        output_dir = recording.parent / ".stt"
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            self.settings.stt_whisper_binary,
            str(recording),
            "--model",
            self.settings.stt_whisper_model,
            "--model_dir",
            str(self.settings.stt_whisper_model_dir),
            "--device",
            "cpu",
            "--output_dir",
            str(output_dir),
            "--output_format",
            "json",
            "--language",
            self.settings.stt_whisper_language,
            "--fp16",
            "False",
            "--verbose",
            "False",
        ]
        env = os.environ.copy()
        if self.settings.stt_ld_library_path:
            env["LD_LIBRARY_PATH"] = self.settings.stt_ld_library_path + (
                f":{env['LD_LIBRARY_PATH']}" if env.get("LD_LIBRARY_PATH") else ""
            )

        subprocess.run(command, check=True, capture_output=True, text=True, env=env)
        result_path = output_dir / f"{recording.stem}.json"
        payload = json.loads(result_path.read_text())
        text = str(payload.get("text", "")).strip()
        return TranscriptResult(
            text=text,
            model=self.settings.stt_whisper_model,
            language=self.settings.stt_whisper_language,
        )

    def is_configured(self) -> bool:
        return bool(
            self.settings.stt_enabled
            and Path(self.settings.stt_whisper_binary).exists()
            and Path(self.settings.stt_whisper_model_dir).exists()
        )
