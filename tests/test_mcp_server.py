import unittest
from pathlib import Path
from types import SimpleNamespace
import json

from app.mcp_server import PhoneAgentMcpServer


class FakeDb:
    def __init__(self) -> None:
        self.calls = {
            "call-1": {
                "id": "call-1",
                "channel_id": "chan-1",
                "caller_number": "123",
                "dialed_number": "ht813",
                "status": "ANSWERED",
                "created_at": "2026-05-16T14:00:00+00:00",
                "updated_at": "2026-05-16T14:00:05+00:00",
                "events": [],
                "recordings": [],
                "decisions": [],
                "transcripts": [],
            }
        }
        self.events: list[tuple[str, str, str]] = []
        self.status_updates: list[tuple[str, object]] = []

    def upsert_call(self, call) -> None:
        self.calls[call.call_id] = {
            "id": call.call_id,
            "channel_id": call.channel_id,
            "caller_number": call.caller_number,
            "dialed_number": call.dialed_number,
            "status": call.status.value if hasattr(call.status, "value") else call.status,
            "created_at": "2026-05-16T14:00:00+00:00",
            "updated_at": "2026-05-16T14:00:00+00:00",
            "events": [],
            "recordings": [],
            "decisions": [],
            "transcripts": [],
        }

    def list_calls(self) -> list[dict]:
        return [
            {key: value for key, value in call.items() if key not in {"events", "recordings", "decisions", "transcripts"}}
            for call in self.calls.values()
        ]

    def get_call(self, call_id: str) -> dict | None:
        return self.calls.get(call_id)

    def add_event(self, call_id: str, event_type: str, payload: str) -> None:
        self.events.append((call_id, event_type, payload))

    def update_call_status(self, call_id: str, status) -> None:
        self.status_updates.append((call_id, status))


class FakeAriClient:
    def __init__(self) -> None:
        self.transfer_requests: list[tuple[str, str | None, str | None, int | None]] = []
        self.hangup_requests: list[str] = []
        self.originate_requests: list[tuple[str, str | None, str | None, str | None, str | None, int | None]] = []

    async def transfer_call(self, channel_id: str, context=None, extension=None, priority=None) -> None:
        self.transfer_requests.append((channel_id, context, extension, priority))

    async def hangup_call(self, channel_id: str) -> None:
        self.hangup_requests.append(channel_id)

    async def originate_call(
        self,
        endpoint: str,
        *,
        caller_id=None,
        app_args=None,
        context=None,
        extension=None,
        priority=None,
    ) -> str:
        self.originate_requests.append((endpoint, caller_id, app_args, context, extension, priority))
        return "outbound-1234"


class McpServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.phonebook_path = Path("/tmp/openclaw-phonebook-test.json")
        self.phonebook_path.write_text(json.dumps({"contacts": {"alice": "PJSIP/alice"}}), encoding="utf-8")
        self.settings = SimpleNamespace(
            mcp_server_name="openclaw-phone-agent",
            phone_agent_public_base_url="http://127.0.0.1:8080",
            softphone_endpoint="PJSIP/human-softphone",
            phonebook_path=self.phonebook_path,
            outbound_pstn_context="call-ht813-pstn",
            outbound_pstn_endpoint="PJSIP/ht813-fxo",
            outbound_number_template="Local/{number}@{context}",
            asterisk_transfer_context="call-human-softphone",
            asterisk_transfer_extension="700",
            asterisk_transfer_priority=1,
        )
        self.db = FakeDb()
        self.ari_client = FakeAriClient()
        self.server = PhoneAgentMcpServer(self.settings, self.db, self.ari_client)

    async def test_initialize_reports_capabilities(self) -> None:
        response = await self.server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(response["result"]["serverInfo"]["name"], "openclaw-phone-agent")
        self.assertIn("tools", response["result"]["capabilities"])

    async def test_tools_list_contains_call_control_tools(self) -> None:
        response = await self.server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool_names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(
            tool_names,
            ["list_calls", "get_call", "transfer_call", "hangup_call", "callto", "dial_outbound"],
        )

    async def test_list_calls_returns_structured_content(self) -> None:
        response = await self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_calls", "arguments": {}},
            }
        )
        self.assertEqual(response["result"]["structuredContent"]["calls"][0]["id"], "call-1")

    async def test_transfer_call_invokes_ari_and_updates_state(self) -> None:
        response = await self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "transfer_call", "arguments": {"call_id": "call-1"}},
            }
        )
        self.assertEqual(self.ari_client.transfer_requests, [("chan-1", None, None, None)])
        self.assertEqual(response["result"]["structuredContent"]["status"], "TRANSFER_PENDING")
        self.assertEqual(self.db.events[0][1], "ManualTransferRequested")

    async def test_hangup_call_invokes_ari(self) -> None:
        response = await self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "hangup_call", "arguments": {"call_id": "call-1"}},
            }
        )
        self.assertEqual(self.ari_client.hangup_requests, ["chan-1"])
        self.assertEqual(response["result"]["structuredContent"]["status"], "HUNG_UP")

    async def test_dial_outbound_invokes_ari_and_records_call(self) -> None:
        response = await self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "dial_outbound",
                    "arguments": {
                        "endpoint": "PJSIP/human-softphone",
                        "caller_id": "OpenClaw <1000>",
                    },
                },
            }
        )
        self.assertEqual(
            self.ari_client.originate_requests,
            [("PJSIP/human-softphone", "OpenClaw <1000>", None, None, None, None)],
        )
        self.assertEqual(response["result"]["structuredContent"]["status"], "DIALING")
        self.assertEqual(self.db.events[0][1], "OutboundOriginateRequested")

    async def test_dial_outbound_defaults_to_softphone_endpoint(self) -> None:
        response = await self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "dial_outbound", "arguments": {}},
            }
        )
        self.assertEqual(
            self.ari_client.originate_requests,
            [("PJSIP/human-softphone", None, None, None, None, None)],
        )
        self.assertEqual(response["result"]["structuredContent"]["endpoint"], "PJSIP/human-softphone")

    async def test_callto_resolves_contact_name_from_phonebook(self) -> None:
        response = await self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "callto", "arguments": {"target": "alice"}},
            }
        )
        self.assertEqual(self.ari_client.originate_requests, [("PJSIP/alice", None, None, None, None, None)])
        self.assertEqual(response["result"]["structuredContent"]["target"], "alice")
        self.assertEqual(response["result"]["structuredContent"]["endpoint"], "PJSIP/alice")

    async def test_callto_resolves_phone_number_via_trunk(self) -> None:
        response = await self.server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "callto", "arguments": {"target": "0612345678"}},
            }
        )
        self.assertEqual(
            self.ari_client.originate_requests,
            [("PJSIP/0612345678@ht813-fxo", None, None, None, None, None)],
        )
        self.assertEqual(response["result"]["structuredContent"]["endpoint"], "PJSIP/0612345678@ht813-fxo")


if __name__ == "__main__":
    unittest.main()
