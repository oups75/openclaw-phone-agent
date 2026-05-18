from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.models import CallContext, Decision
from app.llm_adapter import LLMTurnResult, DIALOG_SYSTEM_PROMPT, parse_llm_json

if TYPE_CHECKING:
    from app.settings import Settings


@dataclass(slots=True)
class OpenClawResult:
    decision: Decision
    confidence: float
    explanation: str
    spoken_response: str | None = None
    source: str = "stub"


class OpenClawAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def process_call_context(self, call_context: CallContext) -> OpenClawResult:
        if not self.settings.openclaw_enabled:
            return self._stub_result()

        command = [self.settings.openclaw_command, "agent", "--message", self._build_prompt(call_context)]
        if self.settings.openclaw_use_local_agent:
            command.append("--local")
        if self.settings.openclaw_agent_id:
            command.extend(["--agent", self.settings.openclaw_agent_id])
        session_id = self._session_id(call_context)
        if session_id:
            command.extend(["--session-id", session_id])
        if self.settings.openclaw_model:
            command.extend(["--model", self.settings.openclaw_model])
        command.extend(["--timeout", str(self.settings.openclaw_timeout_seconds)])

        try:
            env = os.environ.copy()
            if self.settings.openclaw_config_path is not None:
                env["OPENCLAW_CONFIG_PATH"] = str(self.settings.openclaw_config_path)
            if self.settings.openclaw_state_dir is not None:
                env["OPENCLAW_STATE_DIR"] = str(self.settings.openclaw_state_dir)
            completed = subprocess.run(
                command,
                capture_output=True,
                check=True,
                text=True,
                env=env,
                timeout=self.settings.openclaw_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return OpenClawResult(
                decision=Decision.TRANSFER_TO_HUMAN,
                confidence=0.0,
                explanation=f"OpenClaw invocation failed: {exc}",
                source="openclaw-error",
            )

        response_text = completed.stdout.strip()
        try:
            payload = self._extract_json_payload(response_text)
        except ValueError:
            return OpenClawResult(
                decision=Decision.ANSWER,
                confidence=0.5,
                explanation="OpenClaw returned non-JSON output; using raw reply as spoken text.",
                spoken_response=response_text or None,
                source="openclaw-raw",
            )

        decision_value = str(payload.get("decision", Decision.TRANSFER_TO_HUMAN))
        try:
            decision = Decision(decision_value)
        except ValueError:
            decision = Decision.TRANSFER_TO_HUMAN

        confidence = float(payload.get("confidence", 0.0))
        explanation = str(payload.get("explanation", "OpenClaw response processed."))
        spoken_response = payload.get("spoken_response")
        if spoken_response is not None:
            spoken_response = str(spoken_response).strip() or None

        return OpenClawResult(
            decision=decision,
            confidence=max(0.0, min(confidence, 1.0)),
            explanation=explanation,
            spoken_response=spoken_response,
            source="openclaw-cli",
        )

    def process_turn(
        self,
        history: list[dict[str, str]],
        call_context: CallContext,
    ) -> LLMTurnResult:
        if not self.settings.openclaw_enabled:
            return LLMTurnResult(
                decision=Decision.TRANSFER_TO_HUMAN,
                confidence=0.42,
                spoken_response="Hello. The OpenClaw bridge is not enabled yet.",
                continue_dialog=False,
                explanation="Stub: openclaw disabled.",
                source="stub",
            )

        history_with_system = history if any(m.get("role") == "system" for m in history) else [{"role": "system", "content": DIALOG_SYSTEM_PROMPT}] + history
        prompt = "\n".join(f"[{m['role']}]: {m['content']}" for m in history_with_system)
        prompt += f"\nCaller: {call_context.caller_number or 'unknown'}. Call id: {call_context.call_id}."

        command = [self.settings.openclaw_command, "agent", "--message", prompt]
        if self.settings.openclaw_use_local_agent:
            command.append("--local")
        if self.settings.openclaw_agent_id:
            command.extend(["--agent", self.settings.openclaw_agent_id])
        session_id = self._session_id(call_context)
        if session_id:
            command.extend(["--session-id", session_id])
        if self.settings.openclaw_model:
            command.extend(["--model", self.settings.openclaw_model])
        command.extend(["--timeout", str(self.settings.openclaw_timeout_seconds)])

        try:
            import os
            env = os.environ.copy()
            if self.settings.openclaw_config_path is not None:
                env["OPENCLAW_CONFIG_PATH"] = str(self.settings.openclaw_config_path)
            if self.settings.openclaw_state_dir is not None:
                env["OPENCLAW_STATE_DIR"] = str(self.settings.openclaw_state_dir)
            completed = subprocess.run(
                command,
                capture_output=True,
                check=True,
                text=True,
                env=env,
                timeout=self.settings.openclaw_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return LLMTurnResult(
                decision=Decision.TRANSFER_TO_HUMAN,
                confidence=0.0,
                spoken_response=None,
                continue_dialog=False,
                explanation=f"OpenClaw invocation failed: {exc}",
                source="openclaw-error",
            )

        return parse_llm_json(completed.stdout.strip(), "openclaw-cli")

    def _stub_result(self) -> OpenClawResult:
        return OpenClawResult(
            decision=Decision.TRANSFER_TO_HUMAN,
            confidence=0.42,
            explanation="Stub adapter: future OpenClaw orchestration not implemented yet.",
            spoken_response="Hello. The OpenClaw bridge is not enabled yet.",
            source="stub",
        )

    def _build_prompt(self, call_context: CallContext) -> str:
        caller = call_context.caller_number or "unknown"
        dialed = call_context.dialed_number or "unknown"
        return (
            "You are an orchestration layer for a local-first phone agent. "
            "Return ONLY compact JSON with keys decision, confidence, explanation, spoken_response. "
            "decision must be one of ANSWER, TRANSFER_TO_HUMAN, HANGUP. "
            "spoken_response must be short and safe for phone playback. "
            f"Caller: {caller}. Dialed: {dialed}. "
            f"Call id: {call_context.call_id}. "
            "If uncertain, choose TRANSFER_TO_HUMAN."
        )

    def _session_id(self, call_context: CallContext) -> str | None:
        if not self.settings.openclaw_session_id:
            return None
        return self.settings.openclaw_session_id.replace("{call_id}", call_context.call_id)

    def _extract_json_payload(self, text: str) -> dict:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise ValueError("no json object in response")
        return json.loads(text[start : end + 1])
