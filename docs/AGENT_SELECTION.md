# Choosing the remote agent backend

The iOS/visionOS app never talks to an LLM directly: it speaks to the
phone-agent bridge (`/ws/chat`), and the bridge forwards turns to a pluggable
LLM adapter (`LLM_BACKEND` in settings). This document compares the candidate
agent backends and explains the recommendation.

## Candidates

### OpenClaw
Personal AI agent gateway (formerly Clawdbot/Moltbot). Multi-channel
(WhatsApp, Telegram, CLI, …), session-based, tool-using, self-hosted.

- **Pros:** already integrated in this repo (`OpenClawAdapter`, MCP surface,
  `openclaw agent --session-id …`); persistent sessions map 1:1 onto the app's
  `session_id`; the phone (Asterisk) channel and the mobile app share the same
  brain, contacts, and tools.
- **Cons:** heavier to operate than a bare model endpoint.

### Hermes (Nous Research)
Open-source, MIT-licensed self-improving agent with persistent memory and a
built-in learning loop; 20+ chat platforms from one gateway; voice via
faster-whisper/Groq/OpenAI Whisper and TTS via xAI/Gemini/Piper.

- **Pros:** strong memory/skills story; very light to run (works on a $5 VPS);
  model-agnostic.
- **Cons:** no integration in this repo today; its voice features target its
  own channels (Telegram/Discord), not an external WebSocket bridge — we would
  still front it with this bridge via its CLI or API.

### Odysseus
Open-source self-hosted AI workspace (launched May 2026), ~77k GitHub stars.
Chat UI, agents, deep research, local model support (Ollama, vLLM, llama.cpp,
OpenRouter), Docker Compose deployment.

- **Pros:** polished ChatGPT-like workspace, local-first, zero telemetry.
- **Cons:** it is a *workspace UI*, not an embeddable agent runtime; young
  project with a fast-moving API surface (~1,400 open issues); duplicating it
  behind a voice bridge adds little over calling the model backend directly.

## Recommendation

**Keep OpenClaw as the remote agent brain.** It is already wired in
(`LLM_BACKEND=openclaw`), it gives the app persistent, tool-using sessions,
and one agent then serves every channel: PSTN calls through Asterisk and voice
conversations from iPhone/Vision Pro.

The other two remain one setting away, with no app changes:

| You want | Set |
| --- | --- |
| OpenClaw sessions & tools (recommended) | `LLM_BACKEND=openclaw`, `OPENCLAW_ENABLED=true` |
| A plain local model (fast, simple) | `LLM_BACKEND=ollama`, `OLLAMA_MODEL=…` |
| Hermes, Odysseus, or anything with a CLI | `LLM_BACKEND=cli`, `CLI_LLM_COMMAND=…` |

Because the app only depends on the `/ws/chat` contract, the agent backend can
be swapped server-side at any time without touching the mobile app.

## Sources

- [Hermes Agent (Nous Research)](https://github.com/nousresearch/hermes-agent) — [docs](https://hermes-agent.nousresearch.com/docs/)
- [Odysseus self-hosted AI workspace](https://odysseusai.dev/) — [overview](https://www.mindstudio.ai/blog/what-is-odysseus-pewdiepie-open-source-ai-workspace)
