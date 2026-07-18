import asyncio
import unittest
from types import SimpleNamespace

from app.mcp_server import PhoneAgentMcpServer


class FakeTranscriptionService:
    async def transcribe_voice_message(self, audio_path, *, source, title, summarize, language):
        self.last = {
            "audio_path": audio_path,
            "source": source,
            "title": title,
            "summarize": summarize,
            "language": language,
        }
        return SimpleNamespace(
            text="hello world",
            source=source,
            engine="meetily-whisper",
            meeting_id="meeting-9",
            summary_requested=summarize,
            segments=[],
        )


class FakeMeetily:
    def is_configured(self) -> bool:
        return True

    async def get_summary(self, meeting_id):
        return {"status": "completed", "meeting_id": meeting_id, "data": {"MeetingName": "x"}}

    async def search(self, query):
        return [{"id": "meeting-9", "title": "t", "matchContext": query}]


def make_server(transcription_service=None, meetily=None) -> PhoneAgentMcpServer:
    settings = SimpleNamespace(mcp_server_name="test-server")
    return PhoneAgentMcpServer(
        settings,
        db=None,
        ari_client=None,
        transcription_service=transcription_service,
        meetily=meetily,
    )


def call_tool(server, name, arguments):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    return asyncio.run(server.handle_request(payload))


class McpTranscriptionToolTests(unittest.TestCase):
    def test_tools_list_includes_transcription_tools(self) -> None:
        server = make_server()
        response = asyncio.run(
            server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        )
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertIn("transcribe_voice_message", names)
        self.assertIn("get_voice_summary", names)
        self.assertIn("search_voice_transcripts", names)

    def test_transcribe_voice_message_tool(self) -> None:
        service = FakeTranscriptionService()
        server = make_server(transcription_service=service)
        response = call_tool(
            server,
            "transcribe_voice_message",
            {"audio_path": "/tmp/note.ogg", "source": "telegram", "summarize": True},
        )
        content = response["result"]["structuredContent"]
        self.assertEqual(content["text"], "hello world")
        self.assertEqual(content["meeting_id"], "meeting-9")
        self.assertTrue(content["summary_requested"])
        self.assertEqual(service.last["source"], "telegram")

    def test_transcribe_tool_errors_without_service(self) -> None:
        server = make_server()
        response = call_tool(server, "transcribe_voice_message", {"audio_path": "/tmp/x.wav"})
        self.assertIn("error", response)

    def test_get_voice_summary_tool(self) -> None:
        server = make_server(meetily=FakeMeetily())
        response = call_tool(server, "get_voice_summary", {"meeting_id": "meeting-9"})
        self.assertEqual(
            response["result"]["structuredContent"]["summary"]["status"], "completed"
        )

    def test_search_voice_transcripts_tool(self) -> None:
        server = make_server(meetily=FakeMeetily())
        response = call_tool(server, "search_voice_transcripts", {"query": "invoice"})
        results = response["result"]["structuredContent"]["results"]
        self.assertEqual(results[0]["id"], "meeting-9")


if __name__ == "__main__":
    unittest.main()
