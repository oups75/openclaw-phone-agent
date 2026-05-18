from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.ari_client import AriClient
from app.db import Database
from app.dialing import resolve_outbound_endpoint
from app.models import CallContext, CallStatus
from app.settings import Settings

PROTOCOL_VERSION = "2024-11-05"


class PhoneAgentMcpServer:
    def __init__(self, settings: Settings, db: Database, ari_client: AriClient):
        self.settings = settings
        self.db = db
        self.ari_client = ari_client

    async def handle_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        method = payload.get("method")
        params = payload.get("params", {})
        request_id = payload.get("id")

        try:
            if method == "initialize":
                result = self._initialize()
            elif method == "tools/list":
                result = self._tools_list()
            elif method == "tools/call":
                result = await self._tools_call(params)
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
        except HTTPException as exc:
            return self._error(request_id, -32000, exc.detail)
        except Exception as exc:  # pragma: no cover - defensive wrapper
            return self._error(request_id, -32000, str(exc))

        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "serverInfo": {
                "name": self.settings.mcp_server_name,
                "version": "0.1.0",
            },
        }

    def _tools_list(self) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "name": "list_calls",
                    "title": "List calls",
                    "description": "List recent phone calls known to the phone-agent.",
                    "inputSchema": {"type": "object", "properties": {}},
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "get_call",
                    "title": "Get call",
                    "description": "Fetch one call with its events, recordings, decisions, and transcripts.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "call_id": {"type": "string", "description": "Phone-agent call id."},
                        },
                        "required": ["call_id"],
                    },
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "transfer_call",
                    "title": "Transfer call",
                    "description": "Continue the live channel into the human softphone dialplan context.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "call_id": {"type": "string"},
                            "context": {"type": "string"},
                            "extension": {"type": "string"},
                            "priority": {"type": "integer", "minimum": 1},
                        },
                        "required": ["call_id"],
                    },
                },
                {
                    "name": "hangup_call",
                    "title": "Hang up call",
                    "description": "Hang up a live channel in Asterisk.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "call_id": {"type": "string"},
                        },
                        "required": ["call_id"],
                    },
                },
                {
                    "name": "callto",
                    "title": "Call to target",
                    "description": "Resolve a contact name, local alias, endpoint, or phone number and originate a call.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Contact name, local alias, endpoint, or phone number.",
                            },
                            "caller_id": {
                                "type": "string",
                                "description": "Optional caller ID string like Name <number>.",
                            },
                            "app_args": {
                                "type": "string",
                                "description": "Optional app args for the Stasis application.",
                            },
                        },
                        "required": ["target"],
                    },
                },
                {
                    "name": "dial_outbound",
                    "title": "Dial outbound",
                    "description": "Originate an outbound channel and attach it to the phone-agent Stasis app. Defaults to the configured softphone endpoint if endpoint is omitted.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "endpoint": {
                                "type": "string",
                                "description": "Asterisk endpoint such as PJSIP/human-softphone or a trunk destination.",
                            },
                            "caller_id": {
                                "type": "string",
                                "description": "Optional caller ID string like Name <number>.",
                            },
                            "app_args": {
                                "type": "string",
                                "description": "Optional app args for the Stasis application.",
                            },
                        },
                    },
                },
            ]
        }

    async def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})

        if name == "list_calls":
            calls = self.db.list_calls()
            return self._tool_result(
                text=f"Found {len(calls)} calls.",
                structured_content={"calls": calls},
            )
        if name == "get_call":
            call = self._require_call(arguments["call_id"])
            return self._tool_result(
                text=f"Loaded call {arguments['call_id']}.",
                structured_content={"call": call},
            )
        if name == "transfer_call":
            call = self._require_call(arguments["call_id"])
            await self.ari_client.transfer_call(
                call["channel_id"],
                context=arguments.get("context"),
                extension=arguments.get("extension"),
                priority=arguments.get("priority"),
            )
            self.db.add_event(
                call["id"],
                "ManualTransferRequested",
                json.dumps(
                    {
                        "context": arguments.get("context") or self.settings.asterisk_transfer_context,
                        "extension": arguments.get("extension") or self.settings.asterisk_transfer_extension,
                        "priority": arguments.get("priority") or self.settings.asterisk_transfer_priority,
                    }
                ),
            )
            self.db.update_call_status(call["id"], CallStatus.TRANSFER_PENDING)
            return self._tool_result(
                text=f"Transfer requested for call {call['id']}.",
                structured_content={
                    "call_id": call["id"],
                    "status": CallStatus.TRANSFER_PENDING.value,
                },
            )
        if name == "hangup_call":
            call = self._require_call(arguments["call_id"])
            await self.ari_client.hangup_call(call["channel_id"])
            self.db.add_event(call["id"], "ManualHangupRequested", json.dumps({}))
            self.db.update_call_status(call["id"], CallStatus.HUNG_UP)
            return self._tool_result(
                text=f"Hangup requested for call {call['id']}.",
                structured_content={
                    "call_id": call["id"],
                    "status": CallStatus.HUNG_UP.value,
                },
            )
        if name == "callto":
            endpoint = resolve_outbound_endpoint(arguments["target"], self.settings)
            return await self._originate_call(
                endpoint,
                caller_id=arguments.get("caller_id"),
                app_args=arguments.get("app_args"),
                target=arguments["target"],
            )
        if name == "dial_outbound":
            endpoint = arguments.get("endpoint") or self.settings.softphone_endpoint
            return await self._originate_call(
                endpoint,
                caller_id=arguments.get("caller_id"),
                app_args=arguments.get("app_args"),
                target=endpoint,
            )

        raise HTTPException(status_code=404, detail=f"Unknown tool: {name}")

    async def _originate_call(
        self,
        endpoint: str,
        *,
        caller_id: str | None,
        app_args: str | None,
        target: str,
    ) -> dict[str, Any]:
        channel_id = await self.ari_client.originate_call(
            endpoint,
            caller_id=caller_id,
            app_args=app_args,
        )
        self.db.upsert_call(
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
        self.db.add_event(
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
        return self._tool_result(
            text=f"Outbound call originated to {target}.",
            structured_content={
                "call_id": channel_id,
                "target": target,
                "endpoint": endpoint,
                "status": CallStatus.DIALING.value,
            },
        )

    def _require_call(self, call_id: str) -> dict[str, Any]:
        call = self.db.get_call(call_id)
        if call is None:
            raise HTTPException(status_code=404, detail=f"Call not found: {call_id}")
        return call

    def _tool_result(
        self,
        *,
        text: str,
        structured_content: dict[str, Any],
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": structured_content,
            "_meta": meta or {},
        }

    def _error(self, request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
