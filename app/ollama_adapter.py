from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from app.llm_adapter import DIALOG_SYSTEM_PROMPT, LLMTurnResult, parse_llm_json
from app.models import CallContext, Decision

if TYPE_CHECKING:
    from app.settings import Settings


class OllamaAdapter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def process_turn(
        self,
        history: list[dict[str, str]],
        call_context: CallContext,
    ) -> LLMTurnResult:
        messages = (
            history
            if any(m.get("role") == "system" for m in history)
            else [{"role": "system", "content": DIALOG_SYSTEM_PROMPT}] + history
        )
        if not messages or messages[-1].get("role") != "user":
            messages = messages + [
                {
                    "role": "user",
                    "content": (
                        f"Caller: {call_context.caller_number or 'unknown'}. "
                        f"Call id: {call_context.call_id}. What is your decision?"
                    ),
                }
            ]

        try:
            response = httpx.post(
                f"{self.settings.ollama_base_url}/api/chat",
                json={
                    "model": self.settings.ollama_model,
                    "messages": messages,
                    "stream": False,
                    "format": "json",
                },
                timeout=self.settings.ollama_timeout_seconds,
            )
            response.raise_for_status()
            content = response.json()["message"]["content"]
        except Exception as exc:
            return LLMTurnResult(
                decision=Decision.TRANSFER_TO_HUMAN,
                confidence=0.0,
                spoken_response=None,
                continue_dialog=False,
                explanation=f"Ollama request failed: {exc}",
                source="ollama-error",
            )

        return parse_llm_json(content, "ollama")
