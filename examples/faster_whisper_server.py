#!/usr/bin/env python3
"""faster-whisper `/inference` server — drop-in for Meetily's whisper.cpp server.

Reuses the existing on-disk faster-whisper stack (the same CTranslate2 models
Hermes/Odysseus already use) instead of building whisper.cpp or downloading a
separate ggml model. Exposes the whisper.cpp-server HTTP contract that both the
Meetily frontend and the phone-agent `MeetilyClient` expect:

    POST /inference   multipart/form-data, field `audio_file`
                      optional `language`, `response_format`
    -> {"text": "...", "segments": [{"text", "offsets": {"from","to"}, ...}]}

Run with the existing whisper venv (has faster_whisper + ctranslate2):

    HF_HOME=/run/media/soloway/workspace/Devel/Tools/ai/hf-cache \
    WHISPER_MODEL=base \
    /home/soloway/.local/share/whisper-venv/bin/python3 \
        examples/faster_whisper_server.py --host 0.0.0.0 --port 8178

Stdlib-only (no fastapi/flask) so it does not mutate the shared whisper venv.
CPU-only by default to stay off the GPU the LLMs use.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

from faster_whisper import WhisperModel  # noqa: E402  (after env setup)

MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
DOWNLOAD_ROOT = os.environ.get("WHISPER_DOWNLOAD_ROOT")  # else HF cache default

_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        print(f"[faster-whisper-server] loading model={MODEL_NAME} device={DEVICE} compute={COMPUTE_TYPE}")
        _model = WhisperModel(
            MODEL_NAME,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
            download_root=DOWNLOAD_ROOT,
        )
    return _model


def _parse_multipart(body: bytes, content_type: str) -> dict[str, tuple[str | None, bytes]]:
    """Return {field_name: (filename, raw_bytes)} for a multipart/form-data body."""
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = BytesParser(policy=default_policy).parsebytes(header + body)
    fields: dict[str, tuple[str | None, bytes]] = {}
    if not message.is_multipart():
        return fields
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name is None:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        fields[name] = (filename, payload)
    return fields


def _timestamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def transcribe(audio_bytes: bytes, suffix: str, language: str | None) -> dict:
    with tempfile.NamedTemporaryFile(suffix=suffix or ".wav", delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        segments, info = get_model().transcribe(
            tmp.name,
            language=language,
            beam_size=int(os.environ.get("WHISPER_BEAM_SIZE", "5")),
            vad_filter=True,
        )
        out_segments = []
        texts = []
        for seg in segments:
            text = seg.text.strip()
            texts.append(text)
            start_ms = int(round(seg.start * 1000))
            end_ms = int(round(seg.end * 1000))
            out_segments.append(
                {
                    "text": text,
                    "t0": start_ms // 10,   # centiseconds, whisper.cpp style
                    "t1": end_ms // 10,
                    "offsets": {"from": start_ms, "to": end_ms},
                    "timestamps": {"from": _timestamp(seg.start), "to": _timestamp(seg.end)},
                }
            )
    return {
        "text": " ".join(t for t in texts if t).strip(),
        "segments": out_segments,
        "language": getattr(info, "language", language) or "",
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quieter default logging
        print(f"[faster-whisper-server] {self.address_string()} {fmt % args}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("", "/health"):
            self._send_json(200, {"status": "ok", "model": MODEL_NAME, "device": DEVICE})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        inference_path = os.environ.get("WHISPER_INFERENCE_PATH", "/inference")
        if self.path.rstrip("/") not in (inference_path.rstrip("/"), "/inference", "/asr"):
            self._send_json(404, {"error": f"unknown path {self.path}"})
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        try:
            if "multipart/form-data" in content_type:
                fields = _parse_multipart(body, content_type)
                audio = fields.get("audio_file") or fields.get("file")
                if audio is None:
                    self._send_json(400, {"error": "missing audio_file field"})
                    return
                filename, audio_bytes = audio
                lang_field = fields.get("language")
                language = lang_field[1].decode().strip() if lang_field else None
                suffix = Path(filename).suffix if filename else ".wav"
            else:
                audio_bytes = body
                language = self.headers.get("X-Language")
                suffix = ".wav"
            if not audio_bytes:
                self._send_json(400, {"error": "empty audio"})
                return
            result = transcribe(audio_bytes, suffix, language or None)
            self._send_json(200, result)
        except Exception as exc:  # surface the error instead of a silent 500
            self._send_json(500, {"error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("WHISPER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WHISPER_PORT", "8178")))
    args = parser.parse_args()

    get_model()  # eager-load so the first request is fast and failures surface now
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[faster-whisper-server] listening on http://{args.host}:{args.port}/inference")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
