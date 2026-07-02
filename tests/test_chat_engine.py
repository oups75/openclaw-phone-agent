from unittest.mock import MagicMock

import pytest

from app.chat_engine import ChatEngine, VOICE_ASSISTANT_SYSTEM_PROMPT
from app.llm_adapter import LLMTurnResult
from app.models import Decision


def _settings(history_limit: int = 40):
    s = MagicMock()
    s.mobile_chat_history_limit = history_limit
    return s


def _llm(reply: str = "Hello there."):
    llm = MagicMock()
    llm.process_turn = MagicMock(
        return_value=LLMTurnResult(
            decision=Decision.ANSWER,
            confidence=0.9,
            spoken_response=reply,
            continue_dialog=True,
            source="test",
        )
    )
    return llm


@pytest.mark.asyncio
async def test_process_message_creates_session_with_system_prompt():
    llm = _llm()
    engine = ChatEngine(_settings(), llm)

    result = await engine.process_message("s1", "Hi")

    assert result.spoken_response == "Hello there."
    messages = llm.process_turn.call_args.args[0]
    assert messages[0] == {"role": "system", "content": VOICE_ASSISTANT_SYSTEM_PROMPT}
    assert messages[-1] == {"role": "user", "content": "Hi"}


@pytest.mark.asyncio
async def test_process_message_accumulates_history():
    llm = _llm()
    engine = ChatEngine(_settings(), llm)

    await engine.process_message("s1", "First")
    await engine.process_message("s1", "Second")

    messages = llm.process_turn.call_args.args[0]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert messages[2]["content"] == "Hello there."


@pytest.mark.asyncio
async def test_process_message_uses_mobile_channel_context():
    llm = _llm()
    engine = ChatEngine(_settings(), llm)

    await engine.process_message("s1", "Hi")

    context = llm.process_turn.call_args.args[1]
    assert context.call_id == "s1"
    assert context.channel_id == "mobile:s1"
    assert context.metadata["channel"] == "mobile-chat"


@pytest.mark.asyncio
async def test_sessions_are_isolated():
    llm = _llm()
    engine = ChatEngine(_settings(), llm)

    await engine.process_message("s1", "From one")
    await engine.process_message("s2", "From two")

    messages = llm.process_turn.call_args.args[0]
    user_texts = [m["content"] for m in messages if m["role"] == "user"]
    assert user_texts == ["From two"]


@pytest.mark.asyncio
async def test_reset_clears_session():
    llm = _llm()
    engine = ChatEngine(_settings(), llm)

    await engine.process_message("s1", "Hi")
    assert engine.reset("s1") is True
    assert engine.reset("s1") is False
    assert engine.session_info("s1") is None

    await engine.process_message("s1", "Again")
    messages = llm.process_turn.call_args.args[0]
    user_texts = [m["content"] for m in messages if m["role"] == "user"]
    assert user_texts == ["Again"]


@pytest.mark.asyncio
async def test_history_trimmed_to_limit_keeps_system_prompt():
    llm = _llm()
    engine = ChatEngine(_settings(history_limit=5), llm)

    for i in range(6):
        await engine.process_message("s1", f"msg {i}")

    messages = llm.process_turn.call_args.args[0]
    assert len(messages) <= 5
    assert messages[0]["role"] == "system"
    assert messages[-1] == {"role": "user", "content": "msg 5"}


@pytest.mark.asyncio
async def test_no_assistant_turn_recorded_when_reply_empty():
    llm = MagicMock()
    llm.process_turn = MagicMock(
        return_value=LLMTurnResult(
            decision=Decision.ANSWER,
            confidence=0.5,
            spoken_response=None,
            continue_dialog=True,
            source="test",
        )
    )
    engine = ChatEngine(_settings(), llm)

    await engine.process_message("s1", "Hi")
    await engine.process_message("s1", "Anyone there?")

    messages = llm.process_turn.call_args.args[0]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "user"]
