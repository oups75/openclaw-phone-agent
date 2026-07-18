from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


@dataclass(slots=True)
class MeetilyTranscription:
    text: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    source: str = "meetily-whisper"


class MeetilyClient:
    """HTTP client for a headless Meetily deployment.

    Talks to two independent services:
    - the whisper.cpp transcription server (``/inference``, multipart audio)
    - the Meetily FastAPI backend (meetings, transcripts, LLM summaries, search)

    Both run without the Tauri frontend; the frontend is an optional viewer on
    top of the same backend, so anything stored here shows up in it.
    """

    def __init__(self, settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport

    def is_configured(self) -> bool:
        return bool(
            getattr(self.settings, "meetily_enabled", False)
            and self.settings.meetily_backend_url
        )

    def _client(self, base_url: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url,
            timeout=self.settings.meetily_timeout_seconds,
            transport=self._transport,
        )

    async def transcribe_audio(
        self, audio_path: str | Path, language: str | None = None
    ) -> MeetilyTranscription:
        path = Path(audio_path)
        files = {"audio_file": (path.name, path.read_bytes(), "audio/wav")}
        data = {"response_format": "json"}
        if language:
            data["language"] = language
        async with self._client(self.settings.meetily_whisper_url) as client:
            response = await client.post("/inference", files=files, data=data)
            response.raise_for_status()
            payload = response.json()
        segments = payload.get("segments") or payload.get("transcription") or []
        return MeetilyTranscription(
            text=str(payload.get("text", "")).strip(),
            segments=list(segments),
        )

    async def save_transcript(self, title: str, segments: list[dict[str, Any]]) -> str:
        body = {"meeting_title": title, "transcripts": segments}
        async with self._client(self.settings.meetily_backend_url) as client:
            response = await client.post("/save-transcript", json=body)
            response.raise_for_status()
            payload = response.json()
        return str(payload["meeting_id"])

    async def process_transcript(
        self,
        text: str,
        meeting_id: str,
        provider: str | None = None,
        model: str | None = None,
        chunk_size: int | None = None,
        overlap: int | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "text": text,
            "model": provider or self.settings.meetily_summary_provider,
            "model_name": model or self.settings.meetily_summary_model,
            "meeting_id": meeting_id,
        }
        if chunk_size is not None:
            body["chunk_size"] = chunk_size
        if overlap is not None:
            body["overlap"] = overlap
        async with self._client(self.settings.meetily_backend_url) as client:
            response = await client.post("/process-transcript", json=body)
            response.raise_for_status()
            payload = response.json()
        return str(payload["process_id"])

    async def get_summary(self, meeting_id: str) -> dict[str, Any]:
        async with self._client(self.settings.meetily_backend_url) as client:
            response = await client.get(f"/get-summary/{meeting_id}")
            if response.status_code not in (200, 202):
                response.raise_for_status()
            return response.json()

    async def search(self, query: str) -> list[dict[str, Any]]:
        async with self._client(self.settings.meetily_backend_url) as client:
            response = await client.post("/search-transcripts", json={"query": query})
            response.raise_for_status()
            return response.json()
