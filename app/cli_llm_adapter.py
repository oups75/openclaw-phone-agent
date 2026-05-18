from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from app.llm_adapter import DIALOG_SYSTEM_PROMPT, LLMTurnResult, parse_llm_json
from app.models import CallContext, Decision

if TYPE_CHECKING:
    from app.settings import Settings


class GenericCLIAdapter:
    """Subprocess-based LLM adapter.

    Passes conversation history as JSON on stdin, reads LLMTurnResult JSON from stdout.
    Command template: any binary that reads {"history": [...], "call_id": "..."} from stdin
    and writes {"decision": ..., "confidence": ..., "spoken_response": ...,
                "continue_dialog": ..., "explanation": ...} to stdout.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def process_turn(
        self,
        history: list[dict[str, str]],
        call_context: CallContext,
    ) -> LLMTurnResult:
        if not self.settings.cli_llm_command:
            return LLMTurnResult(
                decision=Decision.TRANSFER_TO_HUMAN,
                confidence=0.0,
                spoken_response=None,
                continue_dialog=False,
                explanation="CLI_LLM_COMMAND not configured.",
                source="cli-error",
            )

        messages = (
            history
            if any(m.get("role") == "system" for m in history)
            else [{"role": "system", "content": DIALOG_SYSTEM_PROMPT}] + history
        )
        stdin_payload = json.dumps(
            {
                "history": messages,
                "call_id": call_context.call_id,
                "caller_number": call_context.caller_number,
                "dialed_number": call_context.dialed_number,
            }
        )

        import shlex

        try:
            result = subprocess.run(
                shlex.split(self.settings.cli_llm_command),
                input=stdin_payload,
                capture_output=True,
                text=True,
                timeout=self.settings.cli_llm_timeout_seconds,
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return LLMTurnResult(
                decision=Decision.TRANSFER_TO_HUMAN,
                confidence=0.0,
                spoken_response=None,
                continue_dialog=False,
                explanation=f"CLI LLM failed: {exc}",
                source="cli-error",
            )

        return parse_llm_json(result.stdout.strip(), "cli-llm")
