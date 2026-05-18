import unittest
from types import SimpleNamespace

from app.models import CallContext, Decision
from app.openclaw_adapter import OpenClawAdapter


class OpenClawAdapterTests(unittest.TestCase):
    def test_stub_mode_returns_transfer(self) -> None:
        settings = SimpleNamespace(
            openclaw_enabled=False,
            openclaw_command="openclaw",
            openclaw_timeout_seconds=45,
            openclaw_use_local_agent=False,
            openclaw_agent_id=None,
            openclaw_session_id=None,
            openclaw_model=None,
            openclaw_config_path=None,
            openclaw_state_dir=None,
        )
        adapter = OpenClawAdapter(settings)

        result = adapter.process_call_context(CallContext(call_id="call-1", channel_id="chan-1"))

        self.assertEqual(result.source, "stub")
        self.assertEqual(result.decision, Decision.TRANSFER_TO_HUMAN)
        self.assertIsNotNone(result.spoken_response)

    def test_extract_json_payload_from_wrapped_output(self) -> None:
        settings = SimpleNamespace(
            openclaw_enabled=False,
            openclaw_command="openclaw",
            openclaw_timeout_seconds=45,
            openclaw_use_local_agent=False,
            openclaw_agent_id=None,
            openclaw_session_id=None,
            openclaw_model=None,
            openclaw_config_path=None,
            openclaw_state_dir=None,
        )
        adapter = OpenClawAdapter(settings)

        payload = adapter._extract_json_payload(  # noqa: SLF001 - targeted unit test
            'prefix {"decision":"ANSWER","confidence":0.9,"explanation":"ok","spoken_response":"hello"} suffix'
        )

        self.assertEqual(payload["decision"], "ANSWER")
        self.assertEqual(payload["spoken_response"], "hello")

    def test_session_id_expands_call_id_placeholder(self) -> None:
        settings = SimpleNamespace(
            openclaw_enabled=False,
            openclaw_command="openclaw",
            openclaw_timeout_seconds=45,
            openclaw_use_local_agent=False,
            openclaw_agent_id=None,
            openclaw_session_id="phone-assistance-{call_id}",
            openclaw_model=None,
            openclaw_config_path=None,
            openclaw_state_dir=None,
        )
        adapter = OpenClawAdapter(settings)

        session_id = adapter._session_id(CallContext(call_id="call-42", channel_id="chan-1"))  # noqa: SLF001

        self.assertEqual(session_id, "phone-assistance-call-42")

    def test_process_call_context_passes_openclaw_env(self) -> None:
        settings = SimpleNamespace(
            openclaw_enabled=True,
            openclaw_command="openclaw",
            openclaw_timeout_seconds=45,
            openclaw_use_local_agent=False,
            openclaw_agent_id="main",
            openclaw_session_id="phone-assistance-{call_id}",
            openclaw_model="codex/gpt-5.5",
            openclaw_config_path="/tmp/openclaw-phone.json",
            openclaw_state_dir="/tmp/openclaw-phone-state",
        )
        adapter = OpenClawAdapter(settings)

        class Completed:
            stdout = '{"decision":"ANSWER","confidence":0.9,"explanation":"ok","spoken_response":"hello"}'

        captured: dict[str, object] = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs.get("env")
            return Completed()

        with unittest.mock.patch("app.openclaw_adapter.subprocess.run", side_effect=fake_run):
            result = adapter.process_call_context(CallContext(call_id="call-42", channel_id="chan-1"))

        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertEqual(captured["env"]["OPENCLAW_CONFIG_PATH"], "/tmp/openclaw-phone.json")
        self.assertEqual(captured["env"]["OPENCLAW_STATE_DIR"], "/tmp/openclaw-phone-state")
        self.assertIn("--session-id", captured["command"])


if __name__ == "__main__":
    unittest.main()
