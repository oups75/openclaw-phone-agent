from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
import websockets
from websockets.client import WebSocketClientProtocol

from app.call_state import CallStateMachine
from app.db import Database
from app.models import CallContext, CallStatus
from app.llm_adapter import LLMAdapter
from app.settings import Settings
from app.stt_adapter import STTAdapter
from app.tts_adapter import TTSAdapter

logger = logging.getLogger(__name__)


class AriClient:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        llm_adapter: LLMAdapter,
        tts_adapter: TTSAdapter,
        stt_adapter: STTAdapter,
        transcription_service=None,
    ):
        self.settings = settings
        self.db = db
        self.llm_adapter = llm_adapter
        self.tts_adapter = tts_adapter
        self.stt_adapter = stt_adapter
        self.transcription_service = transcription_service
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._transcription_tasks: set[str] = set()
        self._active_calls: dict[str, CallContext] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._playback_events: dict[str, asyncio.Event] = {}
        self._recording_events: dict[str, asyncio.Event] = {}
        self._dialog_tasks: set[str] = set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run(), name="ari-listener")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    self._ws_url(),
                    additional_headers=self._ws_headers(),
                ) as websocket:
                    logger.info("Connected to Asterisk ARI WebSocket")
                    await self._listen(websocket)
            except Exception as exc:  # pragma: no cover - network skeleton
                logger.warning("ARI listener error: %s", exc)
                await asyncio.sleep(5)

    def _ws_url(self) -> str:
        parsed = urlparse(self.settings.asterisk_base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urlencode(
            {
                "app": self.settings.asterisk_ari_app,
                "subscribeAll": "true",
            }
        )
        return urlunparse((scheme, parsed.netloc, self.settings.asterisk_ari_ws_path, "", query, ""))

    def _ws_headers(self) -> dict[str, str]:
        token = f"{self.settings.asterisk_ari_username}:{self.settings.asterisk_ari_password}"
        encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}

    async def _listen(self, websocket: WebSocketClientProtocol) -> None:
        async for raw_message in websocket:
            event = json.loads(raw_message)
            await self.handle_event(event)

    async def handle_event(self, event: dict) -> None:
        event_type = event.get("type", "Unknown")
        logger.info("ARI event: %s ch=%s", event_type, event.get("channel", {}).get("id", "n/a"))
        if event_type == "StasisStart":
            await self._handle_stasis_start(event)
        elif event_type == "ChannelStateChange":
            await self._handle_channel_state_change(event)
        elif event_type in {"StasisEnd", "ChannelDestroyed"}:
            await self._handle_stasis_end(event)
        elif event_type == "PlaybackFinished":
            pb_id = event.get("playback", {}).get("id", "")
            if pb_id in self._playback_events:
                self._playback_events[pb_id].set()
        elif event_type in {"RecordingFinished", "RecordingFailed"}:
            rec_name = event.get("recording", {}).get("name", "")
            if rec_name in self._recording_events:
                self._recording_events[rec_name].set()
        elif event_type == "ChannelHangupRequest":
            ch_id = event.get("channel", {}).get("id", "")
            if ch_id in self._cancel_events:
                self._cancel_events[ch_id].set()

    async def _handle_stasis_start(self, event: dict) -> None:
        channel = event.get("channel", {})
        call_id = channel.get("id", "")
        outbound = call_id.startswith("outbound-")
        channel_state = channel.get("state", "")
        call = CallContext(
            call_id=call_id,
            channel_id=call_id,
            caller_number=channel.get("caller", {}).get("number"),
            dialed_number=channel.get("dialplan", {}).get("exten"),
            status=CallStatus.RINGING if not outbound else CallStatus.DIALING,
            metadata={"event": "StasisStart"},
        )
        self._active_calls[call_id] = call
        self.db.upsert_call(call)
        self.db.add_event(call.call_id, "StasisStart", json.dumps(event))
        mixmonitor_recording = self._mixmonitor_recording_path(call.call_id)
        self.db.add_recording(call.call_id, f"{call.call_id}-mixmonitor", str(mixmonitor_recording), "wav")
        if outbound:
            if channel_state == "Up":
                await self._activate_connected_call(call, answer_first=False)
            else:
                logger.info("Outbound call ringing: %s", call.call_id)
            return

        await self._activate_connected_call(call, answer_first=True)

    async def _handle_channel_state_change(self, event: dict) -> None:
        channel = event.get("channel", {})
        call_id = channel.get("id", "")
        payload = json.dumps(event)
        self.db.add_event(call_id, "ChannelStateChange", payload)

        state = str(channel.get("state", ""))
        call = self._active_calls.get(call_id)
        if call is None:
            payload = self.db.get_call(call_id)
            if payload is None:
                return
            call = CallContext(
                call_id=payload["id"],
                channel_id=payload["channel_id"],
                caller_number=payload.get("caller_number"),
                dialed_number=payload.get("dialed_number"),
                status=CallStatus(payload["status"]),
            )
            self._active_calls[call_id] = call

        if state == "Up" and call.status in {CallStatus.DIALING, CallStatus.RINGING}:
            outbound = call.call_id.startswith("outbound-")
            await self._activate_connected_call(call, answer_first=not outbound)

    async def _activate_connected_call(self, call: CallContext, *, answer_first: bool) -> None:
        machine = CallStateMachine(call)
        if answer_first:
            await self.answer_call(call.channel_id)
        machine.transition(CallStatus.ANSWERED)
        self.db.update_call_status(call.call_id, call.status)

        cancel_event = asyncio.Event()
        self._cancel_events[call.call_id] = cancel_event

        greeting_pb_id = f"{call.call_id}-greeting"
        pb_event = self.arm_playback_event(greeting_pb_id)
        await self.play_greeting_with_id(call.channel_id, greeting_pb_id)
        machine.transition(CallStatus.GREETING)
        self.db.update_call_status(call.call_id, call.status)

        if call.call_id not in self._dialog_tasks:
            self._dialog_tasks.add(call.call_id)
            asyncio.create_task(
                self._run_dialog(call, pb_event, cancel_event),
                name=f"dialog-{call.call_id}",
            )

    async def _handle_stasis_end(self, event: dict) -> None:
        channel = event.get("channel", {})
        call_id = channel.get("id", "")
        event_type = event.get("type", "StasisEnd")
        self.db.add_event(call_id, event_type, json.dumps(event))
        self.db.update_call_status(call_id, CallStatus.ENDED)
        if call_id in self._cancel_events:
            self._cancel_events[call_id].set()
        if call_id and call_id not in self._transcription_tasks:
            self._transcription_tasks.add(call_id)
            asyncio.create_task(self._transcribe_call(call_id), name=f"transcribe-{call_id}")

    async def answer_call(self, channel_id: str) -> None:
        await self._post(f"/ari/channels/{channel_id}/answer")

    async def play_greeting(self, channel_id: str) -> None:
        await self._post(
            f"/ari/channels/{channel_id}/play",
            params={"media": f"sound:{self.settings.asterisk_playback_sound}"},
        )

    async def play_greeting_with_id(self, channel_id: str, playback_id: str) -> None:
        await self._post(
            f"/ari/channels/{channel_id}/play/{playback_id}",
            params={"media": f"sound:{self.settings.asterisk_playback_sound}"},
        )

    async def play_tts_file(self, channel_id: str, rendered_path: Path) -> None:
        url = f"{self.settings.phone_agent_public_base_url}/tts/{rendered_path.name}"
        await self._post(
            f"/ari/channels/{channel_id}/play",
            params={"media": f"sound:{url}"},
        )

    async def play_tts_file_with_id(
        self, channel_id: str, rendered_path: Path, playback_id: str
    ) -> None:
        url = f"{self.settings.phone_agent_public_base_url}/tts/{rendered_path.name}"
        await self._post(
            f"/ari/channels/{channel_id}/play/{playback_id}",
            params={"media": f"sound:{url}"},
        )

    async def start_recording(self, channel_id: str, recording_name: str) -> None:
        await self._post(
            f"/ari/channels/{channel_id}/record",
            params={
                "name": recording_name,
                "format": self.settings.asterisk_recording_format,
                "maxDurationSeconds": str(self.settings.asterisk_recording_max_duration),
                "ifExists": "overwrite",
            },
        )

    async def transfer_call(
        self,
        channel_id: str,
        context: str | None = None,
        extension: str | None = None,
        priority: int | None = None,
    ) -> None:
        await self._post(
            f"/ari/channels/{channel_id}/continue",
            params={
                "context": context or self.settings.asterisk_transfer_context,
                "extension": extension or self.settings.asterisk_transfer_extension,
                "priority": str(priority or self.settings.asterisk_transfer_priority),
            },
        )

    async def hangup_call(self, channel_id: str) -> None:
        await self._delete(f"/ari/channels/{channel_id}")

    async def originate_call(
        self,
        endpoint: str,
        *,
        caller_id: str | None = None,
        app_args: str | None = None,
        context: str | None = None,
        extension: str | None = None,
        priority: int | None = None,
    ) -> str:
        channel_id = f"outbound-{uuid.uuid4().hex[:12]}"
        params: dict[str, str] = {
            "endpoint": endpoint,
            "channelId": channel_id,
        }
        if context:
            params["context"] = context
        if extension:
            params["extension"] = extension
        if priority is not None:
            params["priority"] = str(priority)
        else:
            params["priority"] = "1"
        if context is None:
            params["app"] = self.settings.asterisk_ari_app
            if app_args:
                params["appArgs"] = app_args
        elif app_args:
            params["appArgs"] = app_args

        body: dict[str, dict[str, str]] = {}
        variables: dict[str, str] = {}
        if caller_id:
            variables["CALLERID(all)"] = caller_id
        if variables:
            body["variables"] = variables

        await self._post(
            "/ari/channels",
            params=params,
            json_body=body or None,
        )
        return channel_id


    def arm_playback_event(self, playback_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._playback_events[playback_id] = event
        return event

    def arm_recording_event(self, recording_name: str) -> asyncio.Event:
        event = asyncio.Event()
        self._recording_events[recording_name] = event
        return event

    async def _post(
        self,
        path: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> None:
        url = f"{self.settings.asterisk_base_url}{path}"
        auth = (self.settings.asterisk_ari_username, self.settings.asterisk_ari_password)
        async with httpx.AsyncClient(auth=auth, timeout=10.0) as client:
            response = await client.post(url, params=params, json=json_body)
            response.raise_for_status()

    async def _delete(self, path: str, params: dict[str, str] | None = None) -> None:
        url = f"{self.settings.asterisk_base_url}{path}"
        auth = (self.settings.asterisk_ari_username, self.settings.asterisk_ari_password)
        async with httpx.AsyncClient(auth=auth, timeout=10.0) as client:
            response = await client.delete(url, params=params)
            response.raise_for_status()

    def _mixmonitor_recording_path(self, call_id: str) -> Path:
        return self.settings.phone_agent_mixmonitor_dir / f"{call_id}.wav"

    async def _run_dialog(
        self,
        call: CallContext,
        greeting_pb_event: asyncio.Event,
        cancel_event: asyncio.Event,
    ) -> None:
        try:
            try:
                await asyncio.wait_for(greeting_pb_event.wait(), timeout=self.settings.asterisk_playback_timeout)
            except asyncio.TimeoutError:
                pass
            if cancel_event.is_set():
                return

            from app.dialog_engine import DialogEngine  # noqa: PLC0415

            engine = DialogEngine(
                self.settings,
                self.db,
                self,
                self.llm_adapter,
                self.tts_adapter,
                self.stt_adapter,
            )
            await engine.run(call, cancel_event)
        finally:
            self._dialog_tasks.discard(call.call_id)
            self._cancel_events.pop(call.call_id, None)

    async def _transcribe_call(self, call_id: str) -> None:
        try:
            recording = self._mixmonitor_recording_path(call_id)
            for _ in range(30):
                if recording.exists() and recording.stat().st_size > 0:
                    break
                await asyncio.sleep(1)
            if not recording.exists() or recording.stat().st_size == 0:
                self.db.add_event(
                    call_id,
                    "TranscriptSkipped",
                    json.dumps({"reason": "recording-missing", "file_path": str(recording)}),
                )
                return

            if getattr(self.settings, "meetily_enabled", False) and self.transcription_service is not None:
                try:
                    result = await self.transcription_service.transcribe_voice_message(
                        recording, source="pstn"
                    )
                except Exception as exc:
                    logger.warning("Meetily transcription failed for %s: %s", call_id, exc)
                    self.db.add_event(
                        call_id,
                        "TranscriptFailed",
                        json.dumps({"file_path": str(recording), "error": str(exc)}),
                    )
                    return
                self.db.add_transcript(
                    call_id, str(recording), result.text, "", result.engine, result.source
                )
                self.db.add_event(
                    call_id,
                    "MeetilyTranscriptCreated",
                    json.dumps({
                        "file_path": str(recording),
                        "engine": result.engine,
                        "source": result.source,
                        "text": result.text,
                        "meeting_id": result.meeting_id,
                    }),
                )
                return

            if not self.stt_adapter.is_configured():
                self.db.add_event(call_id, "TranscriptSkipped", json.dumps({"reason": "stt-disabled"}))
                return

            try:
                transcript = await asyncio.to_thread(self.stt_adapter.transcribe, recording)
            except Exception as exc:
                logger.warning("STT failed for %s: %s", call_id, exc)
                self.db.add_event(
                    call_id,
                    "TranscriptFailed",
                    json.dumps({"file_path": str(recording), "error": str(exc)}),
                )
                return

            self.db.add_transcript(call_id, str(recording), transcript.text, transcript.language, transcript.model, transcript.source)
            self.db.add_event(
                call_id,
                "TranscriptCreated",
                json.dumps({
                    "file_path": str(recording),
                    "language": transcript.language,
                    "model": transcript.model,
                    "source": transcript.source,
                    "text": transcript.text,
                }),
            )
        finally:
            self._transcription_tasks.discard(call_id)
