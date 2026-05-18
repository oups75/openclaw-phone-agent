from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.llm_adapter import LLMTurnResult
from app.models import CallContext, CallStatus, Decision, DialogState

if TYPE_CHECKING:
    from app.ari_client import AriClient
    from app.db import Database
    from app.llm_adapter import LLMAdapter
    from app.settings import Settings
    from app.stt_adapter import STTAdapter
    from app.tts_adapter import TTSAdapter

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a local-first phone agent assistant. "
    "A caller has reached you. Your job: understand their request, then decide how to handle it. "
    "Return ONLY compact JSON with these keys: "
    "decision (ANSWER|TRANSFER_TO_HUMAN|HANGUP), "
    "confidence (0.0-1.0), "
    "explanation (internal reasoning, not spoken), "
    "spoken_response (short message safe for phone playback, or null), "
    "continue_dialog (true if you need more info from caller, false if ready to act). "
    "If continue_dialog is true, spoken_response must be your next question. "
    "If uncertain after gathering info, choose TRANSFER_TO_HUMAN."
)


class CallCancelledError(Exception):
    pass


class DialogEngine:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        ari: AriClient,
        llm: LLMAdapter,
        tts: TTSAdapter,
        stt: STTAdapter,
    ) -> None:
        self.settings = settings
        self.db = db
        self.ari = ari
        self.llm = llm
        self.tts = tts
        self.stt = stt

    async def run(self, call: CallContext, cancel_event: asyncio.Event) -> None:
        state = DialogState(call_id=call.call_id)
        state.add("system", SYSTEM_PROMPT)

        try:
            for turn_number in range(self.settings.dialog_max_turns):
                recording_name = f"{call.call_id}-turn-{turn_number}"
                rec_event = self.ari.arm_recording_event(recording_name)

                try:
                    await self.ari.start_recording(call.channel_id, recording_name)
                except Exception as exc:
                    logger.warning("start_recording failed for %s turn %d: %s", call.call_id, turn_number, exc)

                timeout = self.settings.asterisk_turn_recording_max_duration + 5
                await self._wait_or_cancel(rec_event, cancel_event, timeout)

                transcript_text = await self._transcribe_turn(call, recording_name, turn_number)
                if transcript_text:
                    state.add("user", transcript_text)
                    self.db.add_event(
                        call.call_id,
                        "TurnTranscript",
                        json.dumps({"turn": turn_number, "text": transcript_text}),
                    )
                else:
                    state.add("user", "(no speech detected)")

                result = await asyncio.to_thread(self.llm.process_turn, state.to_messages(), call)
                self.db.add_decision(
                    call.call_id, result.decision.value, result.confidence, result.explanation
                )
                self.db.add_event(
                    call.call_id,
                    "LLMTurn",
                    json.dumps(
                        {
                            "turn": turn_number,
                            "decision": result.decision.value,
                            "confidence": result.confidence,
                            "continue_dialog": result.continue_dialog,
                            "source": result.source,
                        }
                    ),
                )

                if result.spoken_response:
                    state.add("assistant", result.spoken_response)

                is_last_turn = turn_number == self.settings.dialog_max_turns - 1
                if not result.continue_dialog or is_last_turn:
                    await self._execute_decision(call, result, cancel_event)
                    return

                if result.spoken_response:
                    await self._play_response(
                        call,
                        result.spoken_response,
                        f"{call.call_id}-turn-{turn_number}-resp",
                        cancel_event,
                    )

            await self._do_transfer(call)

        except CallCancelledError:
            logger.info("Dialog cancelled for call %s (caller hung up)", call.call_id)

    async def _transcribe_turn(
        self, call: CallContext, recording_name: str, turn_number: int
    ) -> str | None:
        if not self.stt.is_configured():
            return None
        recording_path = Path(self.settings.asterisk_ari_recording_dir) / f"{recording_name}.wav"
        for _ in range(10):
            if recording_path.exists() and recording_path.stat().st_size > 0:
                break
            await asyncio.sleep(0.5)
        if not recording_path.exists() or recording_path.stat().st_size == 0:
            self.db.add_event(
                call.call_id,
                "TurnTranscriptSkipped",
                json.dumps({"turn": turn_number, "reason": "recording-missing"}),
            )
            return None
        try:
            transcript = await asyncio.to_thread(self.stt.transcribe, recording_path)
            return transcript.text or None
        except Exception as exc:
            logger.warning("Turn STT failed for %s turn %d: %s", call.call_id, turn_number, exc)
            return None

    async def _execute_decision(
        self, call: CallContext, result: LLMTurnResult, cancel_event: asyncio.Event
    ) -> None:
        if result.decision == Decision.TRANSFER_TO_HUMAN:
            if result.spoken_response:
                await self._play_response(
                    call, result.spoken_response, f"{call.call_id}-transfer-msg", cancel_event
                )
            await self._do_transfer(call)
        elif result.decision == Decision.HANGUP:
            if result.spoken_response:
                await self._play_response(
                    call, result.spoken_response, f"{call.call_id}-goodbye", cancel_event
                )
            await self._do_hangup(call)
        else:  # ANSWER
            if result.spoken_response:
                await self._play_response(
                    call, result.spoken_response, f"{call.call_id}-answer", cancel_event
                )
            await self._do_hangup(call)

    async def _do_transfer(self, call: CallContext) -> None:
        try:
            await self.ari.transfer_call(call.channel_id)
            self.db.update_call_status(call.call_id, CallStatus.TRANSFER_PENDING)
        except Exception as exc:
            logger.warning("Transfer failed for %s: %s", call.call_id, exc)

    async def _do_hangup(self, call: CallContext) -> None:
        try:
            await self.ari.hangup_call(call.channel_id)
            self.db.update_call_status(call.call_id, CallStatus.HUNG_UP)
        except Exception as exc:
            logger.warning("Hangup failed for %s: %s", call.call_id, exc)

    async def _play_response(
        self,
        call: CallContext,
        text: str,
        playback_id: str,
        cancel_event: asyncio.Event,
    ) -> None:
        if not self.tts.is_configured():
            return
        try:
            rendered = await asyncio.to_thread(self.tts.synthesize, text, playback_id)
            if rendered is None:
                return
            pb_event = self.ari.arm_playback_event(playback_id)
            await self.ari.play_tts_file_with_id(call.channel_id, rendered, playback_id)
            timeout = self.settings.asterisk_playback_timeout
            await self._wait_or_cancel(pb_event, cancel_event, timeout)
            self.db.add_event(
                call.call_id,
                "TTSPlayed",
                json.dumps({"playback_id": playback_id, "file": str(rendered)}),
            )
        except CallCancelledError:
            raise
        except Exception as exc:
            logger.warning("TTS play failed for %s / %s: %s", call.call_id, playback_id, exc)

    async def _wait_or_cancel(
        self,
        target_event: asyncio.Event,
        cancel_event: asyncio.Event,
        timeout: float,
    ) -> bool:
        """Wait for target_event. Returns True if target fired. Raises CallCancelledError if cancelled."""
        target_task = asyncio.create_task(target_event.wait())
        cancel_task = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait(
            [target_task, cancel_task],
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if cancel_event.is_set():
            raise CallCancelledError()
        return target_task in done
