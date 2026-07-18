# Meetily integration (transcription + meeting archive for all voice channels)

The phone-agent can use [Meetily](https://github.com/Zackriya-Solutions/meetily)
as its transcription and meeting-minutes engine. Meetily runs headless — two
services, no Tauri frontend required:

| Service | Port | Role |
|---|---|---|
| whisper-server (whisper.cpp) | 8178 | `POST /inference` — multipart audio → transcript (supports `--diarize`, `--language`) |
| meetily-backend (FastAPI) | 5167 | meetings DB, transcript storage, LLM summaries (ollama/claude/groq/openai), full-text search |

## Start Meetily headless

One command brings up both services, reusing the machine's existing
faster-whisper (no whisper.cpp build, no ggml model download):

```bash
examples/start_meetily_stack.sh
```

That starts:

- **faster-whisper `/inference` shim on :8178** — `examples/faster_whisper_server.py`,
  a stdlib-only server that speaks the whisper.cpp-server HTTP contract but is
  backed by the already-installed faster-whisper venv + CTranslate2 model (the
  same STT stack Hermes/Odysseus use). Nothing shared is mutated.
- **Meetily FastAPI backend on :5167** — meeting storage, LLM summaries, search.

Local-first, no cloud keys. Summaries go through the running ollama. One gotcha:
Meetily's `ollama` provider builds an OpenAI-compatible client, and the OpenAI
SDK refuses to start without *some* `OPENAI_API_KEY` — set it to any non-empty
value (`OPENAI_API_KEY=ollama`); ollama ignores it.

### Manual / upstream setup

The upstream `meetily/backend` docker-compose (`whisper-server` + `meetily-backend`)
and `build_whisper.sh` build whisper.cpp with a separate ggml model. On a box
that already has faster-whisper, prefer the shim above — it avoids the duplicate
model and the CUDA/glibc build fight. The Python backend pins target 3.11
(`pandas` has no 3.14 wheel; `python3.11 -m venv`).

## Configure the phone-agent

`.env` (or environment):

```bash
MEETILY_ENABLED=true
MEETILY_BACKEND_URL=http://127.0.0.1:5167
MEETILY_WHISPER_URL=http://127.0.0.1:8178
MEETILY_SUMMARY_PROVIDER=ollama     # ollama | claude | groq | openai
MEETILY_SUMMARY_MODEL=llama3.2
```

When `MEETILY_ENABLED=false` the transcription service falls back to the local
whisper CLI (`stt_adapter`) and skips archiving.

## What you get

`audio_path` is restricted to the service-owned recording directories
(`voice_inbox_dir`, `phone_agent_recordings_dir`, the Asterisk recording
dirs); anything else returns 403. Uploads (`audio_b64`) always land in
`voice_inbox_dir`, and ffmpeg conversions are written there under generated
names.

`TranscriptionService` (app/transcription_service.py) is channel-agnostic:
any audio file → ffmpeg-normalized WAV (16 kHz mono) → Meetily whisper →
archived as a Meetily meeting → optional LLM summary. Sources are tagged
(`pstn`, `whatsapp`, `telegram`, `el-agent`, …) in the meeting title.

### HTTP API

```bash
# transcribe a local file (e.g. an Asterisk call recording)
curl -X POST localhost:8080/transcriptions -H 'content-type: application/json' \
  -d '{"audio_path": "/var/spool/asterisk/recording/call-123.wav", "source": "pstn", "summarize": true}'

# transcribe an uploaded voice note (base64, e.g. from a WhatsApp/Telegram bridge)
curl -X POST localhost:8080/transcriptions -H 'content-type: application/json' \
  -d '{"audio_b64": "...", "filename": "note.ogg", "source": "telegram"}'

# poll the LLM summary (202 while processing, 200 when done)
curl localhost:8080/transcriptions/<meeting_id>/summary

# full-text search across every archived voice transcript
curl -X POST localhost:8080/transcriptions/search -H 'content-type: application/json' \
  -d '{"query": "invoice"}'
```

### MCP tools (for the EL agent / OpenClaw)

The `POST /mcp` server now exposes:

- `transcribe_voice_message {audio_path, source?, title?, summarize?, language?}`
- `get_voice_summary {meeting_id}`
- `search_voice_transcripts {query}`

Any MCP-capable agent — the ElevenLabs conversational agent bridge, an
OpenClaw session, Claude Code — can use the phone-agent as its transcription
agent: point it at `http://<host>:8080/mcp` and call the tools above. Typical
EL-agent flow: the Asterisk bridge records the call leg, then the agent calls
`transcribe_voice_message` with the recording path and `source: "el-agent"`.

## Voice-channel bridges

- `examples/telegram_voice_bridge.py` — long-polls the Telegram Bot API,
  forwards voice notes/audio/video-notes to `/transcriptions`, replies with
  the transcript. httpx only. `TELEGRAM_BOT_TOKEN=... python3 examples/telegram_voice_bridge.py`
- `examples/whatsapp_voice_webhook.py` — WhatsApp Business Cloud API webhook
  (FastAPI): downloads incoming voice messages via the Graph API, transcribes,
  replies in-chat. `uvicorn examples.whatsapp_voice_webhook:app --port 8090`
  behind a TLS tunnel.

Other channels (Signal, Matrix, plain email attachments…) follow the same
pattern: download the audio, `POST /transcriptions` with a `source` tag.

## Optional frontend

The Meetily Tauri desktop app is purely a viewer over the same backend
(port 5167). Install it and every voice message the phone-agent archives —
PSTN calls, WhatsApp/Telegram notes, EL-agent calls — appears there with
playback-synced segments, summaries, and search. No phone-agent changes
needed; it is optional by design.
