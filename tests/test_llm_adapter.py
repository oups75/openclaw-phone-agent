from app.models import Decision
from app.llm_adapter import LLMTurnResult, parse_llm_json


def test_parse_llm_json_valid_answer():
    text = '{"decision": "ANSWER", "confidence": 0.9, "explanation": "ok", "spoken_response": "Hello!", "continue_dialog": false}'
    result = parse_llm_json(text, "test")
    assert result.decision == Decision.ANSWER
    assert result.confidence == 0.9
    assert result.spoken_response == "Hello!"
    assert result.continue_dialog is False
    assert result.source == "test"


def test_parse_llm_json_non_json_falls_back_to_transfer():
    result = parse_llm_json("plain text response", "test")
    assert result.decision == Decision.TRANSFER_TO_HUMAN
    assert result.spoken_response == "plain text response"
    assert result.continue_dialog is False


def test_parse_llm_json_continue_dialog():
    text = '{"decision": "ANSWER", "confidence": 0.3, "explanation": "need more info", "spoken_response": "Can you clarify?", "continue_dialog": true}'
    result = parse_llm_json(text, "test")
    assert result.continue_dialog is True
    assert result.spoken_response == "Can you clarify?"
