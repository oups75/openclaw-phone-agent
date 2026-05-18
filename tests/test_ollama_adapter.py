from unittest.mock import MagicMock, patch

from app.models import CallContext, CallStatus, Decision
from app.ollama_adapter import OllamaAdapter


def _settings():
    s = MagicMock()
    s.ollama_base_url = "http://localhost:11434"
    s.ollama_model = "llama3.2"
    s.ollama_timeout_seconds = 10
    return s


def _call():
    return CallContext(call_id="c1", channel_id="ch1", caller_number="1234")


def test_ollama_adapter_returns_answer_on_valid_response():
    adapter = OllamaAdapter(_settings())
    payload = '{"decision":"ANSWER","confidence":0.9,"explanation":"ok","spoken_response":"Hi!","continue_dialog":false}'
    mock_response = MagicMock()
    mock_response.json.return_value = {"message": {"content": payload}}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_response):
        result = adapter.process_turn([], _call())

    assert result.decision == Decision.ANSWER
    assert result.source == "ollama"
    assert result.continue_dialog is False
