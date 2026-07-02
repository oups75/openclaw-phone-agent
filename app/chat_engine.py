from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.llm_adapter import LLMTurnResult
from app.models import CallContext, DialogState, utc_now

if TYPE_CHECKING:
    from app.llm_adapter import LLMAdapter
    from app.settings import Settings

logger = logging.getLogger(__name__)

VOICE_ASSISTANT_SYSTEM_PROMPT = (
    "You are OpenClaw Voice, a personal voice assistant reached from a mobile or "
    "spatial-computing app. The user speaks; their words arrive as text. "
    "Return ONLY compact JSON with these keys: "
    "decision (ANSWER|TRANSFER_TO_HUMAN|HANGUP), "
    "confidence (0.0-1.0), "
    "explanation (internal reasoning, not spoken), "
    "spoken_response (a short conversational reply, safe to read aloud with TTS), "
    "continue_dialog (true to keep the conversation going, false only if the user says goodbye). "
    "Keep spoken_response brief and natural: no markdown, no lists, no URLs. "
    "decision is almost always ANSWER for this channel."
)


@dataclass(slots=True)
class ChatSession:
    session_id: str
    state: DialogState
    created_at: str = field(default_factory=utc_now)
    last_active_at: str = field(default_factory=utc_now)
    turn_count: int = 0


class ChatEngine:
    """Session-based conversation engine for remote clients (iOS/visionOS app).

    Reuses the same pluggable LLM adapters as the phone dialog engine, but keeps
    per-session history in memory instead of driving an Asterisk channel.
    """

    def __init__(self, settings: Settings, llm: LLMAdapter) -> None:
        self.settings = settings
        self.llm = llm
        self._sessions: dict[str, ChatSession] = {}
        self._lock = asyncio.Lock()

    async def process_message(self, session_id: str, text: str) -> LLMTurnResult:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                state = DialogState(call_id=session_id)
                state.add("system", VOICE_ASSISTANT_SYSTEM_PROMPT)
                session = ChatSession(session_id=session_id, state=state)
                self._sessions[session_id] = session
            session.state.add("user", text)
            session.turn_count += 1
            session.last_active_at = utc_now()
            self._trim_history(session)
            messages = session.state.to_messages()

        context = CallContext(
            call_id=session_id,
            channel_id=f"mobile:{session_id}",
            metadata={"channel": "mobile-chat"},
        )
        result = await asyncio.to_thread(self.llm.process_turn, messages, context)

        if result.spoken_response:
            async with self._lock:
                current = self._sessions.get(session_id)
                if current is not None:
                    current.state.add("assistant", result.spoken_response)
        return result

    def reset(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def session_info(self, session_id: str) -> ChatSession | None:
        return self._sessions.get(session_id)

    def _trim_history(self, session: ChatSession) -> None:
        limit = self.settings.mobile_chat_history_limit
        turns = session.state.turns
        if limit <= 0 or len(turns) <= limit:
            return
        # Always keep the system prompt at index 0; drop the oldest turns after it.
        system_turns = [t for t in turns if t.role == "system"][:1]
        dialogue = [t for t in turns if t.role != "system"]
        session.state.turns = system_turns + dialogue[-(limit - len(system_turns)) :]
