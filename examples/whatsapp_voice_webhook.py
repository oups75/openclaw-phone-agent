"""WhatsApp voice-note webhook for the phone-agent transcription service.

Receives WhatsApp Business Cloud API webhooks, downloads incoming audio/voice
messages via the Graph API, forwards them to the phone-agent
``POST /transcriptions`` endpoint (Meetily transcription + archive), and
replies in the chat with the transcript.

Run behind a public HTTPS endpoint (Meta requires TLS — use a reverse proxy
or tunnel), then register the webhook URL + verify token in the Meta app:

    WHATSAPP_TOKEN=EAAG... WHATSAPP_VERIFY_TOKEN=my-secret \
        uvicorn examples.whatsapp_voice_webhook:app --port 8090

Environment:
    WHATSAPP_TOKEN         required, Cloud API access token
    WHATSAPP_VERIFY_TOKEN  required, webhook verify token you choose
    PHONE_AGENT_URL        default http://127.0.0.1:8080
    WHATSAPP_SUMMARIZE     set to 1 to request an LLM summary for each note
"""

from __future__ import annotations

import base64
import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
PHONE_AGENT_URL = os.environ.get("PHONE_AGENT_URL", "http://127.0.0.1:8080")
SUMMARIZE = os.environ.get("WHATSAPP_SUMMARIZE", "0") == "1"
GRAPH_API = "https://graph.facebook.com/v21.0"

app = FastAPI(title="WhatsApp voice bridge")


@app.get("/webhook")
async def verify(request: Request) -> PlainTextResponse:
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def receive(request: Request) -> dict:
    payload = await request.json()
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, timeout=600
    ) as client:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                phone_number_id = value.get("metadata", {}).get("phone_number_id")
                for message in value.get("messages", []):
                    if message.get("type") not in ("audio", "voice"):
                        continue
                    await handle_audio_message(client, phone_number_id, message)
    return {"status": "ok"}


async def handle_audio_message(client: httpx.AsyncClient, phone_number_id: str, message: dict) -> None:
    media_id = message["audio"]["id"]
    media_info = (await client.get(f"{GRAPH_API}/{media_id}")).json()
    audio_bytes = (await client.get(media_info["url"])).content
    sender = message.get("from", "")

    response = await client.post(
        f"{PHONE_AGENT_URL}/transcriptions",
        headers={},
        json={
            "audio_b64": base64.b64encode(audio_bytes).decode(),
            "filename": f"whatsapp-{media_id}.ogg",
            "source": "whatsapp",
            "title": f"[whatsapp] voice note from {sender}",
            "summarize": SUMMARIZE,
        },
    )
    if response.status_code != 200:
        reply = f"Transcription failed: {response.text[:200]}"
    else:
        result = response.json()
        reply = result["text"] or "(empty transcript)"
        if result.get("meeting_id"):
            reply += f"\n\nmeeting_id: {result['meeting_id']}"

    await client.post(
        f"{GRAPH_API}/{phone_number_id}/messages",
        json={
            "messaging_product": "whatsapp",
            "to": sender,
            "type": "text",
            "text": {"body": reply},
        },
    )
