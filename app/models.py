from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Decision(StrEnum):
    ANSWER = "ANSWER"
    TRANSFER_TO_HUMAN = "TRANSFER_TO_HUMAN"
    HANGUP = "HANGUP"


class CallStatus(StrEnum):
    NEW = "NEW"
    DIALING = "DIALING"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    GREETING = "GREETING"
    RECORDING = "RECORDING"
    TRANSFER_PENDING = "TRANSFER_PENDING"
    HUNG_UP = "HUNG_UP"
    ENDED = "ENDED"


@dataclass(slots=True)
class CallContext:
    call_id: str
    channel_id: str
    caller_number: str | None = None
    dialed_number: str | None = None
    status: CallStatus = CallStatus.NEW
    confidence: float = 0.0
    explanation: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DialogTurn:
    role: str
    content: str
    timestamp: str = field(default_factory=utc_now)


@dataclass
class DialogState:
    call_id: str
    turns: list[DialogTurn] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.turns.append(DialogTurn(role=role, content=content))

    def to_messages(self) -> list[dict[str, str]]:
        return [{"role": t.role, "content": t.content} for t in self.turns]


@dataclass(slots=True)
class DialogTurn:
    role: str
    content: str
    timestamp: str = field(default_factory=utc_now)


@dataclass
class DialogState:
    call_id: str
    turns: list[DialogTurn] = field(default_factory=list)

    def add(self, role: str, content: str) -> None:
        self.turns.append(DialogTurn(role=role, content=content))

    def to_messages(self) -> list[dict[str, str]]:
        return [{"role": t.role, "content": t.content} for t in self.turns]
