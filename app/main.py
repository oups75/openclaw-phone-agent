from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.ari_client import AriClient
from app.db import Database
from app.dialing import resolve_outbound_endpoint
from app.decision_engine import DecisionEngine
from app.mcp_server import PhoneAgentMcpServer
from app.models import CallContext, CallStatus
from app.llm_adapter import make_llm_adapter
from app.meetily_adapter import MeetilyClient
from app.settings import settings
from app.stt_adapter import STTAdapter
from app.transcription_service import TranscriptionService
from app.tts_adapter import TTSAdapter
from app.orpheus_tts_adapter import OrpheusTTSAdapter

logging.basicConfig(level=getattr(logging, settings.phone_agent_log_level.upper(), logging.INFO))

app = FastAPI(title="OpenClaw Phone Agent", version="0.1.0")
db = Database(settings.phone_agent_db_path)
decision_engine = DecisionEngine(transfer_threshold=settings.transfer_confidence_threshold)
llm_adapter = make_llm_adapter(settings)
tts_adapter = OrpheusTTSAdapter(settings) if settings.tts_backend == "orpheus" else TTSAdapter(settings)
stt_adapter = STTAdapter(settings)
ari_client = AriClient(settings, db, llm_adapter, tts_adapter, stt_adapter)
meetily_client = MeetilyClient(settings)
transcription_service = TranscriptionService(settings, meetily_client, stt_adapter=stt_adapter)
mcp_server = PhoneAgentMcpServer(
    settings,
    db,
    ari_client,
    transcription_service=transcription_service,
    meetily=meetily_client,
)


class DecisionRequest(BaseModel):
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    caller_number: str | None = None
    dialed_number: str | None = None


class TransferRequest(BaseModel):
    context: str | None = None
    extension: str | None = None
    priority: int | None = Field(default=None, ge=1)


class DialOutboundRequest(BaseModel):
    endpoint: str | None = None
    caller_id: str | None = None
    app_args: str | None = None


class CallToRequest(BaseModel):
    target: str
    caller_id: str | None = None
    app_args: str | None = None


class TranscribeVoiceRequest(BaseModel):
    audio_path: str | None = None
    audio_b64: str | None = None
    filename: str | None = None
    source: str = "unknown"
    title: str | None = None
    summarize: bool = False
    language: str | None = None


class SearchTranscriptsRequest(BaseModel):
    query: str


@app.on_event("startup")
async def startup() -> None:
    db.initialize()
    settings.phone_agent_recordings_dir.mkdir(parents=True, exist_ok=True)
    settings.tts_output_dir.mkdir(parents=True, exist_ok=True)
    if settings.openclaw_state_dir is not None:
        settings.openclaw_state_dir.mkdir(parents=True, exist_ok=True)
    if settings.enable_ari_listener:
        ari_client.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    if settings.enable_ari_listener:
        await ari_client.stop()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.phone_agent_env}


@app.get("/calls")
async def list_calls() -> list[dict]:
    return db.list_calls()


@app.get("/calls/{call_id}")
async def get_call(call_id: str) -> dict:
    payload = db.get_call(call_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Call not found")
    return payload


@app.get("/calls/{call_id}/transcript")
async def get_call_transcript(call_id: str) -> dict:
    payload = db.get_call(call_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Call not found")
    transcripts = payload.get("transcripts", [])
    if not transcripts:
        raise HTTPException(status_code=404, detail="Transcript not found")
    return {"call_id": call_id, "transcript": transcripts[-1]}


@app.get("/tts/{filename}")
async def get_tts_file(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    if not safe_name.endswith(".wav"):
        raise HTTPException(status_code=404, detail="TTS file not found")

    path = settings.tts_output_dir / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="TTS file not found")

    return FileResponse(path, media_type="audio/wav", filename=safe_name)


@app.post("/calls/{call_id}/decision")
async def decide_call(call_id: str, request: DecisionRequest) -> dict:
    existing_call = db.get_call(call_id)
    if existing_call is None:
        raise HTTPException(status_code=404, detail="Call not found")

    call_context = CallContext(
        call_id=call_id,
        channel_id=existing_call["channel_id"],
        caller_number=request.caller_number or existing_call.get("caller_number"),
        dialed_number=request.dialed_number or existing_call.get("dialed_number"),
        confidence=request.confidence,
    )
    adapter_result = llm_adapter.process_turn([], call_context)
    decision = decision_engine.decide(call_context)
    db.add_decision(call_id, decision.decision.value, decision.confidence, decision.explanation)
    return {
        "call_id": call_id,
        "llm_result": {
            "source": adapter_result.source,
            "decision": adapter_result.decision.value,
            "confidence": adapter_result.confidence,
            "explanation": adapter_result.explanation,
            "spoken_response": adapter_result.spoken_response,
        },
        "decision": decision.decision.value,
        "confidence": decision.confidence,
        "explanation": decision.explanation,
    }


@app.post("/calls/{call_id}/transfer")
async def transfer_call(call_id: str, request: TransferRequest) -> dict:
    existing_call = db.get_call(call_id)
    if existing_call is None:
        raise HTTPException(status_code=404, detail="Call not found")

    await ari_client.transfer_call(
        existing_call["channel_id"],
        context=request.context,
        extension=request.extension,
        priority=request.priority,
    )
    db.add_event(
        call_id,
        "ManualTransferRequested",
        json.dumps(
            {
                "context": request.context or settings.asterisk_transfer_context,
                "extension": request.extension or settings.asterisk_transfer_extension,
                "priority": request.priority or settings.asterisk_transfer_priority,
            }
        ),
    )
    db.update_call_status(call_id, CallStatus.TRANSFER_PENDING)
    return {
        "call_id": call_id,
        "status": CallStatus.TRANSFER_PENDING.value,
        "context": request.context or settings.asterisk_transfer_context,
        "extension": request.extension or settings.asterisk_transfer_extension,
        "priority": request.priority or settings.asterisk_transfer_priority,
    }


@app.post("/calls/{call_id}/hangup")
async def hangup_call(call_id: str) -> dict:
    existing_call = db.get_call(call_id)
    if existing_call is None:
        raise HTTPException(status_code=404, detail="Call not found")

    await ari_client.hangup_call(existing_call["channel_id"])
    db.add_event(call_id, "ManualHangupRequested", "{}")
    db.update_call_status(call_id, CallStatus.HUNG_UP)
    return {"call_id": call_id, "status": CallStatus.HUNG_UP.value}


@app.post("/calls/dial")
async def dial_outbound(request: DialOutboundRequest) -> dict:
    endpoint = request.endpoint or settings.softphone_endpoint
    return await _originate_outbound(endpoint, request.caller_id, request.app_args, target=endpoint)


@app.post("/calls/callto")
async def call_to(request: CallToRequest) -> dict:
    endpoint = resolve_outbound_endpoint(request.target, settings)
    return await _originate_outbound(endpoint, request.caller_id, request.app_args, target=request.target)


@app.post("/transcriptions")
async def transcribe_voice(request: TranscribeVoiceRequest) -> dict:
    if request.audio_b64:
        if not request.filename:
            raise HTTPException(status_code=400, detail="filename is required with audio_b64")
        settings.voice_inbox_dir.mkdir(parents=True, exist_ok=True)
        audio_path = settings.voice_inbox_dir / Path(request.filename).name
        audio_path.write_bytes(base64.b64decode(request.audio_b64))
    elif request.audio_path:
        audio_path = Path(request.audio_path)
    else:
        raise HTTPException(status_code=400, detail="audio_path or audio_b64 is required")

    try:
        result = await transcription_service.transcribe_voice_message(
            audio_path,
            source=request.source,
            title=request.title,
            summarize=request.summarize,
            language=request.language,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Audio file not found: {audio_path}")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return {
        "text": result.text,
        "source": result.source,
        "engine": result.engine,
        "meeting_id": result.meeting_id,
        "summary_requested": result.summary_requested,
    }


@app.get("/transcriptions/{meeting_id}/summary")
async def get_transcription_summary(meeting_id: str) -> dict:
    if not meetily_client.is_configured():
        raise HTTPException(status_code=503, detail="Meetily backend is not configured")
    return await meetily_client.get_summary(meeting_id)


@app.post("/transcriptions/search")
async def search_transcriptions(request: SearchTranscriptsRequest) -> list[dict]:
    if not meetily_client.is_configured():
        raise HTTPException(status_code=503, detail="Meetily backend is not configured")
    return await meetily_client.search(request.query)


@app.options("/mcp")
async def mcp_options() -> Response:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type, mcp-session-id",
        "Access-Control-Expose-Headers": "Mcp-Session-Id",
    }
    return Response(status_code=204, headers=headers)


@app.post("/mcp")
async def handle_mcp(request: Request) -> Response:
    payload = await request.json()
    response_payload = await mcp_server.handle_request(payload)
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Expose-Headers": "Mcp-Session-Id",
    }
    return Response(
        content=json.dumps(response_payload),
        media_type="application/json",
        headers=headers,
    )


async def _originate_outbound(
    endpoint: str,
    caller_id: str | None,
    app_args: str | None,
    *,
    target: str,
) -> dict:
    channel_id = await ari_client.originate_call(
        endpoint,
        caller_id=caller_id,
        app_args=app_args,
    )
    db.upsert_call(
        CallContext(
            call_id=channel_id,
            channel_id=channel_id,
            caller_number=caller_id,
            dialed_number=target,
            status=CallStatus.DIALING,
            metadata={
                "direction": "outbound",
                "endpoint": endpoint,
                "app_args": app_args,
            },
        )
    )
    db.add_event(
        channel_id,
        "OutboundOriginateRequested",
        json.dumps(
            {
                "target": target,
                "endpoint": endpoint,
                "caller_id": caller_id,
                "app_args": app_args,
            }
        ),
    )
    return {
        "call_id": channel_id,
        "target": target,
        "endpoint": endpoint,
        "status": CallStatus.DIALING.value,
    }
