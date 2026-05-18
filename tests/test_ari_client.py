import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.ari_client import AriClient
from app.models import CallContext, CallStatus, Decision
from app.stt_adapter import TranscriptResult


class FakeDb:
    def upsert_call(self, call: CallContext) -> None:
        self.call = call

    def add_event(self, *args, **kwargs) -> None:
        return None

    def update_call_status(self, *args, **kwargs) -> None:
        return None

    def add_decision(self, *args, **kwargs) -> None:
        return None

    def add_recording(self, *args, **kwargs) -> None:
        return None

    def add_transcript(self, *args, **kwargs) -> None:
        return None


class ImmediatePromptBeforeOpenClawTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_plays_before_openclaw_is_invoked(self) -> None:
        settings = SimpleNamespace(
            asterisk_base_url="http://127.0.0.1:8088",
            asterisk_ari_app="openclaw-phone-agent",
            asterisk_ari_ws_path="/ari/events",
            asterisk_ari_username="openclaw_agent",
            asterisk_ari_password="secret",
            asterisk_playback_sound="openclaw-wait",
            asterisk_recording_format="wav",
            asterisk_recording_max_duration=60,
            phone_agent_recordings_dir="/tmp",
            phone_agent_mixmonitor_dir=Path("/tmp"),
            phone_agent_public_base_url="http://127.0.0.1:8080",
            asterisk_playback_timeout=30,
            dialog_max_turns=2,
        )
        events: list[str] = []

        class FakeLLMAdapter:
            def process_turn(self, history, call):
                return SimpleNamespace(
                    decision=Decision.TRANSFER_TO_HUMAN,
                    confidence=0.4,
                    explanation="stub",
                    spoken_response=None,
                    continue_dialog=False,
                    source="stub",
                )

        client = AriClient(settings, FakeDb(), FakeLLMAdapter(), SimpleNamespace(), SimpleNamespace(is_configured=lambda: False))

        async def fake_answer_call(channel_id: str) -> None:
            events.append("answer")

        async def fake_play_greeting_with_id(channel_id: str, playback_id: str) -> None:
            events.append("prompt")

        client.answer_call = fake_answer_call  # type: ignore[assignment]
        client.play_greeting_with_id = fake_play_greeting_with_id  # type: ignore[assignment]

        async def fake_to_thread(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch("app.ari_client.asyncio.to_thread", new=fake_to_thread):
            def fake_create_task(coro, *args, **kwargs):
                coro.close()
                return SimpleNamespace()

            with patch("app.ari_client.asyncio.create_task", side_effect=fake_create_task) as create_task:
                await client._handle_stasis_start(
                    {
                        "channel": {
                            "id": "chan-1",
                            "caller": {"number": "123"},
                            "dialplan": {"exten": "s"},
                        }
                    }
                )
                self.assertTrue(create_task.called)

        self.assertLess(events.index("answer"), events.index("prompt"))

    async def test_transcript_is_stored_after_stasis_end(self) -> None:
        settings = SimpleNamespace(
            phone_agent_mixmonitor_dir=Path("/tmp"),
            stt_enabled=True,
            stt_whisper_binary="/home/soloway/.local/bin/whisper",
            stt_whisper_model="medium",
            stt_whisper_model_dir=Path("/home/soloway/.cache/whisper"),
            stt_whisper_language="en",
            stt_ld_library_path="/media/soloway/workspace/Devel/Tools/ai/xtts/venv/lib/python3.14/site-packages/nvidia/cusparselt/lib",
        )
        transcript_events: list[tuple] = []

        class FakeSttAdapter:
            def is_configured(self) -> bool:
                return True

            def transcribe(self, recording_path):
                transcript_events.append(("transcribe", str(recording_path)))
                return TranscriptResult(text="hello world", model="medium", language="en")

        class TranscriptDb(FakeDb):
            def add_transcript(self, *args, **kwargs) -> None:
                transcript_events.append(("store", args))

        client = AriClient(settings, TranscriptDb(), SimpleNamespace(), SimpleNamespace(), FakeSttAdapter())

        with patch.object(client, "_mixmonitor_recording_path", return_value=Path("/tmp/call-1.wav")):
            path = Path("/tmp/call-1.wav")
            path.write_bytes(b"fake-audio")
            async def fake_to_thread(func, *args, **kwargs):
                return func(*args, **kwargs)

            with patch("app.ari_client.asyncio.to_thread", new=fake_to_thread):
                await client._transcribe_call("call-1")

        self.assertTrue(any(event[0] == "transcribe" for event in transcript_events))
        self.assertTrue(any(event[0] == "store" for event in transcript_events))

    async def test_channel_destroyed_triggers_transcription(self) -> None:
        settings = SimpleNamespace(
            phone_agent_mixmonitor_dir=Path("/tmp"),
            stt_enabled=True,
            stt_whisper_binary="/home/soloway/.local/bin/whisper",
            stt_whisper_model="medium",
            stt_whisper_model_dir=Path("/home/soloway/.cache/whisper"),
            stt_whisper_language="en",
            stt_ld_library_path="",
        )
        transcript_events: list[str] = []

        class FakeSttAdapter:
            def is_configured(self) -> bool:
                return True

            def transcribe(self, recording_path):
                transcript_events.append(str(recording_path))
                return TranscriptResult(text="hello world", model="medium", language="en")

        class TranscriptDb(FakeDb):
            def add_transcript(self, *args, **kwargs) -> None:
                return None

        client = AriClient(settings, TranscriptDb(), SimpleNamespace(), SimpleNamespace(), FakeSttAdapter())

        with patch.object(client, "_mixmonitor_recording_path", return_value=Path("/tmp/call-1.wav")):
            path = Path("/tmp/call-1.wav")
            path.write_bytes(b"fake-audio")

            async def fake_to_thread(func, *args, **kwargs):
                return func(*args, **kwargs)

            with patch("app.ari_client.asyncio.to_thread", new=fake_to_thread):
                def fake_create_task(coro, *args, **kwargs):
                    coro.close()
                    return SimpleNamespace()

                with patch("app.ari_client.asyncio.create_task", side_effect=fake_create_task) as create_task:
                    await client.handle_event(
                        {
                            "type": "ChannelDestroyed",
                            "channel": {"id": "call-1"},
                        }
                    )
                    self.assertTrue(create_task.called)
                    self.assertEqual(create_task.call_count, 1)

        self.assertEqual(transcript_events, [])

    async def test_transfer_call_uses_continue_endpoint(self) -> None:
        settings = SimpleNamespace(
            asterisk_transfer_context="call-human-softphone",
            asterisk_transfer_extension="700",
            asterisk_transfer_priority=1,
        )
        client = AriClient(settings, FakeDb(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
        requests: list[tuple[str, dict[str, str]]] = []

        async def fake_post(path: str, params=None) -> None:
            requests.append((path, params or {}))

        client._post = fake_post  # type: ignore[assignment]
        await client.transfer_call("chan-1")

        self.assertEqual(
            requests,
            [
                (
                    "/ari/channels/chan-1/continue",
                    {"context": "call-human-softphone", "extension": "700", "priority": "1"},
                )
            ],
        )

    async def test_hangup_call_uses_delete_endpoint(self) -> None:
        client = AriClient(SimpleNamespace(), FakeDb(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
        requests: list[tuple[str, dict | None]] = []

        async def fake_delete(path: str, params=None) -> None:
            requests.append((path, params))

        client._delete = fake_delete  # type: ignore[assignment]
        await client.hangup_call("chan-9")

        self.assertEqual(requests, [("/ari/channels/chan-9", None)])

    async def test_originate_call_uses_create_endpoint(self) -> None:
        settings = SimpleNamespace(
            asterisk_base_url="http://127.0.0.1:8088",
            asterisk_ari_app="openclaw-phone-agent",
            asterisk_ari_ws_path="/ari/events",
            asterisk_ari_username="openclaw_agent",
            asterisk_ari_password="secret",
        )
        client = AriClient(settings, FakeDb(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
        requests: list[tuple[str, dict[str, str], dict | None]] = []

        async def fake_post(path: str, params=None, json_body=None) -> None:
            requests.append((path, params or {}, json_body))

        client._post = fake_post  # type: ignore[assignment]
        channel_id = await client.originate_call("PJSIP/human-softphone", caller_id="OpenClaw <1000>", app_args="outbound")

        self.assertTrue(channel_id.startswith("outbound-"))
        self.assertEqual(
            requests,
            [
                (
                    "/ari/channels/create",
                    {
                        "endpoint": "PJSIP/human-softphone",
                        "app": "openclaw-phone-agent",
                        "channelId": channel_id,
                        "appArgs": "outbound",
                        "priority": "1",
                    },
                    {"variables": {"CALLERID(all)": "OpenClaw <1000>"}},
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
