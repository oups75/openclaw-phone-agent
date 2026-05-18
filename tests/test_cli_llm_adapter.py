from unittest.mock import MagicMock, patch

from app.models import CallContext, Decision
from app.cli_llm_adapter import GenericCLIAdapter


def _settings(command="echo"):
    s = MagicMock()
    s.cli_llm_command = command
    s.cli_llm_timeout_seconds = 5
    return s


def _call():
    return CallContext(call_id="c1", channel_id="ch1", caller_number="1234")


def test_cli_adapter_returns_transfer_when_no_command():
    adapter = GenericCLIAdapter(_settings(command=""))
    result = adapter.process_turn([], _call())
    assert result.decision == Decision.TRANSFER_TO_HUMAN
    assert "not configured" in result.explanation
