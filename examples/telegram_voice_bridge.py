"""Telegram voice-note bridge for the phone-agent transcription service.

Long-polls the Telegram Bot API, downloads incoming voice/audio/video-note
messages, forwards them to the phone-agent ``POST /transcriptions`` endpoint
(which transcribes through Meetily and archives the meeting), and replies in
the chat with the transcript.

No dependencies beyond httpx. Run:

    TELEGRAM_BOT_TOKEN=123:abc python3 examples/telegram_voice_bridge.py

Environment:
    TELEGRAM_BOT_TOKEN     required, from @BotFather
    PHONE_AGENT_URL        default http://127.0.0.1:8080
    TELEGRAM_ALLOWED_CHATS optional comma-separated chat ids (allowlist)
    TELEGRAM_SUMMARIZE     set to 1 to request an LLM summary for each note
"""

from __future__ import annotations

import base64
import os
import sys
import time

import httpx

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
PHONE_AGENT_URL = os.environ.get("PHONE_AGENT_URL", "http://127.0.0.1:8080")
ALLOWED_CHATS = {c.strip() for c in os.environ.get("TELEGRAM_ALLOWED_CHATS", "").split(",") if c.strip()}
SUMMARIZE = os.environ.get("TELEGRAM_SUMMARIZE", "0") == "1"
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"


def extract_voice(message: dict) -> dict | None:
    for key in ("voice", "audio", "video_note"):
        if key in message:
            return message[key]
    return None


def handle_message(client: httpx.Client, message: dict) -> None:
    chat_id = str(message.get("chat", {}).get("id", ""))
    if ALLOWED_CHATS and chat_id not in ALLOWED_CHATS:
        return
    media = extract_voice(message)
    if media is None:
        return

    file_info = client.get(f"{API}/getFile", params={"file_id": media["file_id"]}).json()
    file_path = file_info["result"]["file_path"]
    audio_bytes = client.get(f"{FILE_API}/{file_path}").content

    sender = message.get("from", {}).get("first_name", "unknown")
    response = client.post(
        f"{PHONE_AGENT_URL}/transcriptions",
        json={
            "audio_b64": base64.b64encode(audio_bytes).decode(),
            "filename": os.path.basename(file_path),
            "source": "telegram",
            "title": f"[telegram] voice note from {sender}",
            "summarize": SUMMARIZE,
        },
        timeout=600,
    )
    if response.status_code != 200:
        reply = f"Transcription failed: {response.text[:200]}"
    else:
        payload = response.json()
        reply = payload["text"] or "(empty transcript)"
        if payload.get("meeting_id"):
            reply += f"\n\nmeeting_id: {payload['meeting_id']}"

    client.post(
        f"{API}/sendMessage",
        json={"chat_id": chat_id, "text": reply, "reply_to_message_id": message.get("message_id")},
    )


def main() -> None:
    if not BOT_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN is required")
    offset = 0
    with httpx.Client(timeout=65) as client:
        print(f"telegram voice bridge up; forwarding to {PHONE_AGENT_URL}/transcriptions")
        while True:
            try:
                updates = client.get(
                    f"{API}/getUpdates",
                    params={"offset": offset, "timeout": 50, "allowed_updates": '["message"]'},
                ).json()
                for update in updates.get("result", []):
                    offset = update["update_id"] + 1
                    if "message" in update:
                        handle_message(client, update["message"])
            except httpx.HTTPError as exc:
                print(f"telegram poll error: {exc}", file=sys.stderr)
                time.sleep(5)


if __name__ == "__main__":
    main()
