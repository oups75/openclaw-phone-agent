# phone-agent

This service is the future bridge between Asterisk ARI and OpenClaw.

Current responsibilities:

- expose a small FastAPI control surface
- expose an MCP control surface for OpenClaw session integration
- subscribe to ARI events
- bootstrap local SQLite storage
- track call state transitions
- hold stub integration points for STT, TTS, and OpenClaw decisioning

Run locally:

```bash
cd /run/media/soloway/workspace/home/soloway/Desktop/openclaw-phone-agent/services/phone-agent
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Useful local endpoints:

- `GET /calls`
- `GET /calls/{call_id}`
- `POST /calls/{call_id}/transfer`
- `POST /calls/{call_id}/hangup`
- `POST /calls/callto`
- `POST /calls/dial`
- `POST /mcp`

The phone-agent subprocess inherits `OPENCLAW_CONFIG_PATH=/tmp/openclaw-phone.json` and `OPENCLAW_STATE_DIR=/tmp/openclaw-phone-state` so the OpenClaw session can see the phone-control MCP server without mutating the default user profile.

`POST /calls/callto` resolves a contact name, local alias, endpoint, or phone number and dials it through the configured rules. Contact names and aliases go to SIP endpoints. Phone numbers are routed through the HT813 PSTN context. `POST /calls/dial` remains the lower-level endpoint-based variant and uses the configured `SOFTPHONE_ENDPOINT` when omitted.
