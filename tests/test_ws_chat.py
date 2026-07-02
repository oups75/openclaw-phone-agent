from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.chat_engine import ChatEngine
from app.llm_adapter import LLMTurnResult
from app.models import Decision


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "enable_ari_listener", False)
    monkeypatch.setattr(main.settings, "phone_agent_recordings_dir", tmp_path / "recordings")
    monkeypatch.setattr(main.settings, "tts_output_dir", tmp_path / "tts")
    monkeypatch.setattr(main.settings, "openclaw_state_dir", None)
    monkeypatch.setattr(main.settings, "mobile_api_token", None)
    monkeypatch.setattr(main, "db", MagicMock())

    llm = MagicMock()
    llm.process_turn = MagicMock(
        return_value=LLMTurnResult(
            decision=Decision.ANSWER,
            confidence=0.8,
            spoken_response="Bonjour!",
            continue_dialog=True,
            source="test",
        )
    )
    monkeypatch.setattr(main, "chat_engine", ChatEngine(main.settings, llm))

    with TestClient(main.app) as test_client:
        yield test_client


def test_ws_chat_round_trip(client):
    with client.websocket_connect("/ws/chat?session_id=abc") as ws:
        ready = ws.receive_json()
        assert ready == {"type": "ready", "session_id": "abc"}

        ws.send_json({"type": "user_message", "text": "Salut"})
        thinking = ws.receive_json()
        assert thinking["type"] == "thinking"

        reply = ws.receive_json()
        assert reply["type"] == "assistant_message"
        assert reply["session_id"] == "abc"
        assert reply["text"] == "Bonjour!"
        assert reply["decision"] == "ANSWER"
        assert reply["continue_dialog"] is True


def test_ws_chat_ping_and_reset(client):
    with client.websocket_connect("/ws/chat") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["session_id"]

        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        ws.send_json({"type": "reset"})
        reset = ws.receive_json()
        assert reset["type"] == "reset_ok"


def test_ws_chat_rejects_empty_text(client):
    with client.websocket_connect("/ws/chat") as ws:
        ws.receive_json()
        ws.send_json({"type": "user_message", "text": "   "})
        error = ws.receive_json()
        assert error["type"] == "error"


def test_ws_chat_rejects_bad_token(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "mobile_api_token", "secret")
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/chat?token=wrong") as ws:
            ws.receive_json()


def test_ws_chat_accepts_query_token(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "mobile_api_token", "secret")
    with client.websocket_connect("/ws/chat?token=secret") as ws:
        assert ws.receive_json()["type"] == "ready"


def test_rest_chat_round_trip(client):
    response = client.post("/chat", json={"text": "Salut", "session_id": "rest-1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "Bonjour!"
    assert payload["session_id"] == "rest-1"


def test_rest_chat_generates_session_id(client):
    response = client.post("/chat", json={"text": "Salut"})
    assert response.status_code == 200
    assert response.json()["session_id"]


def test_rest_chat_requires_token_when_configured(client, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main.settings, "mobile_api_token", "secret")
    assert client.post("/chat", json={"text": "Salut"}).status_code == 401
    ok = client.post("/chat", json={"text": "Salut"}, headers={"x-api-token": "secret"})
    assert ok.status_code == 200


def test_rest_chat_reset(client):
    client.post("/chat", json={"text": "Salut", "session_id": "rest-2"})
    response = client.delete("/chat/rest-2")
    assert response.status_code == 200
    assert response.json() == {"session_id": "rest-2", "reset": True}
