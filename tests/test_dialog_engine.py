import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.dialog_engine import DialogEngine, CallCancelledError
from app.llm_adapter import LLMTurnResult
from app.models import CallContext, CallStatus, Decision


def _settings():
    s = MagicMock()
    s.dialog_max_turns = 2
    s.asterisk_turn_recording_max_duration = 5
    s.asterisk_ari_recording_dir = "/tmp"
    s.asterisk_playback_timeout = 5
    return s


def _call():
    return CallContext(call_id="c1", channel_id="ch1", caller_number="1234")


def _ari():
    ari = MagicMock()
    ari.arm_recording_event = MagicMock(return_value=asyncio.Event())
    ari.arm_playback_event = MagicMock(return_value=asyncio.Event())
    ari.start_recording = AsyncMock()
    ari.play_tts_file_with_id = AsyncMock()
    ari.transfer_call = AsyncMock()
    ari.hangup_call = AsyncMock()
    return ari


@pytest.mark.asyncio
async def test_dialog_engine_executes_transfer_decision():
    ari = _ari()
    db = MagicMock()
    llm = MagicMock()
    llm.process_turn = MagicMock(return_value=LLMTurnResult(
        decision=Decision.TRANSFER_TO_HUMAN,
        confidence=0.9,
        spoken_response=None,
        continue_dialog=False,
    ))
    tts = MagicMock()
    tts.is_configured = MagicMock(return_value=False)
    stt = MagicMock()
    stt.is_configured = MagicMock(return_value=False)

    engine = DialogEngine(_settings(), db, ari, llm, tts, stt)
    cancel = asyncio.Event()

    rec_event = asyncio.Event()
    rec_event.set()
    ari.arm_recording_event = MagicMock(return_value=rec_event)

    await engine.run(_call(), cancel)

    ari.transfer_call.assert_awaited_once()
