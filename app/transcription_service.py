from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.meetily_adapter import MeetilyClient


@dataclass(slots=True)
class VoiceTranscription:
    text: str
    source: str
    engine: str
    meeting_id: str | None = None
    summary_requested: bool = False
    segments: list[dict[str, Any]] = field(default_factory=list)


class TranscriptionService:
    """Channel-agnostic voice-message transcription.

    Accepts audio from any voice channel (PSTN call recording, WhatsApp or
    Telegram voice note, ElevenLabs agent recording, ...), normalizes it to
    WAV, transcribes through the Meetily whisper server when available (local
    whisper CLI as fallback), and archives the result as a Meetily meeting so
    the optional Meetily frontend can browse, search, and summarize it.
    """

    def __init__(self, settings, meetily: MeetilyClient, stt_adapter=None):
        self.settings = settings
        self.meetily = meetily
        self.stt_adapter = stt_adapter

    async def transcribe_voice_message(
        self,
        audio_path: str | Path,
        *,
        source: str = "unknown",
        title: str | None = None,
        summarize: bool = False,
        language: str | None = None,
    ) -> VoiceTranscription:
        path = Path(audio_path).resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        self._check_path_allowed(path)

        wav_path = await asyncio.to_thread(self._ensure_wav, path)

        if self.meetily.is_configured():
            transcription = await self.meetily.transcribe_audio(wav_path, language=language)
            text = transcription.text
            segments = transcription.segments
            engine = "meetily-whisper"
        elif self.stt_adapter is not None and self.stt_adapter.is_configured():
            result = await asyncio.to_thread(self.stt_adapter.transcribe, wav_path)
            text = result.text
            segments = []
            engine = "local-whisper"
        else:
            raise RuntimeError("No transcription backend configured (meetily disabled, local STT unavailable)")

        meeting_id: str | None = None
        summary_requested = False
        if self.meetily.is_configured() and text:
            meeting_title = title or f"[{source}] {path.stem}"
            meeting_id = await self.meetily.save_transcript(
                meeting_title, self._to_meetily_segments(text, segments)
            )
            if summarize:
                await self.meetily.process_transcript(text, meeting_id)
                summary_requested = True

        return VoiceTranscription(
            text=text,
            source=source,
            engine=engine,
            meeting_id=meeting_id,
            summary_requested=summary_requested,
            segments=segments,
        )

    def _allowed_roots(self) -> list[Path]:
        roots = []
        for attr in (
            "voice_inbox_dir",
            "phone_agent_recordings_dir",
            "phone_agent_mixmonitor_dir",
            "asterisk_ari_recording_dir",
        ):
            value = getattr(self.settings, attr, None)
            if value:
                roots.append(Path(value).resolve())
        return roots

    def _check_path_allowed(self, resolved: Path) -> None:
        roots = self._allowed_roots()
        if not any(resolved.is_relative_to(root) for root in roots):
            raise PermissionError(
                f"Audio path {resolved} is outside the allowed recording directories"
            )

    def _ensure_wav(self, path: Path) -> Path:
        if path.suffix.lower() == ".wav":
            return path
        inbox = Path(self.settings.voice_inbox_dir)
        inbox.mkdir(parents=True, exist_ok=True)
        converted = (inbox / f"{path.stem}-{uuid.uuid4().hex}.wav").resolve()
        command = [
            self.settings.ffmpeg_binary,
            "-y",
            "-i",
            str(path),
            "-ar",
            "16000",
            "-ac",
            "1",
            str(converted),
        ]
        subprocess.run(command, check=True, capture_output=True)
        return converted

    def _to_meetily_segments(
        self, text: str, segments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc).isoformat()
        if not segments:
            return [{"id": "seg-0", "text": text, "timestamp": now}]
        normalized = []
        for index, segment in enumerate(segments):
            offsets = segment.get("offsets") or {}
            entry: dict[str, Any] = {
                "id": str(segment.get("id") or f"seg-{index}"),
                "text": str(segment.get("text", "")).strip(),
                "timestamp": now,
            }
            if "from" in offsets and "to" in offsets:
                start = float(offsets["from"]) / 1000.0
                end = float(offsets["to"]) / 1000.0
                entry["audio_start_time"] = start
                entry["audio_end_time"] = end
                entry["duration"] = end - start
            normalized.append(entry)
        return normalized
