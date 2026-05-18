from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.models import CallContext, Decision

if TYPE_CHECKING:
    from app.settings import Settings

DIALOG_SYSTEM_PROMPT = (
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


@dataclass(slots=True)
class LLMTurnResult:
    decision: Decision
    confidence: float
    spoken_response: str | None
    continue_dialog: bool
    explanation: str = ""
    source: str = "unknown"


class LLMAdapter(Protocol):
    def process_turn(
        self,
        history: list[dict[str, str]],
        call_context: CallContext,
    ) -> LLMTurnResult: ...


def parse_llm_json(text: str, source: str) -> LLMTurnResult:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or start >= end:
        return LLMTurnResult(
            decision=Decision.TRANSFER_TO_HUMAN,
            confidence=0.0,
            spoken_response=text.strip() or None,
            continue_dialog=False,
            explanation="Non-JSON response; forwarding as spoken text.",
            source=source,
        )
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return LLMTurnResult(
            decision=Decision.TRANSFER_TO_HUMAN,
            confidence=0.0,
            spoken_response=None,
            continue_dialog=False,
            explanation=f"JSON parse error in response: {text[:120]}",
            source=source,
        )

    raw_decision = str(payload.get("decision", Decision.TRANSFER_TO_HUMAN))
    try:
        decision = Decision(raw_decision)
    except ValueError:
        decision = Decision.TRANSFER_TO_HUMAN

    confidence = max(0.0, min(float(payload.get("confidence", 0.0)), 1.0))
    explanation = str(payload.get("explanation", ""))
    spoken = payload.get("spoken_response")
    spoken_response = str(spoken).strip() or None if spoken is not None else None
    continue_dialog = bool(payload.get("continue_dialog", False))

    return LLMTurnResult(
        decision=decision,
        confidence=confidence,
        spoken_response=spoken_response,
        continue_dialog=continue_dialog,
        explanation=explanation,
        source=source,
    )


def make_llm_adapter(settings: Settings) -> LLMAdapter:
    backend = settings.llm_backend.lower()
    if backend == "ollama":
        from app.ollama_adapter import OllamaAdapter  # noqa: PLC0415
        return OllamaAdapter(settings)
    if backend == "cli":
        from app.cli_llm_adapter import GenericCLIAdapter  # noqa: PLC0415
        return GenericCLIAdapter(settings)
    from app.openclaw_adapter import OpenClawAdapter  # noqa: PLC0415
    return OpenClawAdapter(settings)
