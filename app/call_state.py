from __future__ import annotations

from app.models import CallContext, CallStatus


ALLOWED_TRANSITIONS: dict[CallStatus, set[CallStatus]] = {
    CallStatus.NEW: {CallStatus.DIALING, CallStatus.RINGING, CallStatus.ANSWERED, CallStatus.ENDED},
    CallStatus.DIALING: {CallStatus.RINGING, CallStatus.ANSWERED, CallStatus.HUNG_UP, CallStatus.ENDED},
    CallStatus.RINGING: {CallStatus.ANSWERED, CallStatus.HUNG_UP, CallStatus.ENDED},
    CallStatus.ANSWERED: {CallStatus.GREETING, CallStatus.RECORDING, CallStatus.HUNG_UP, CallStatus.ENDED},
    CallStatus.GREETING: {CallStatus.RECORDING, CallStatus.TRANSFER_PENDING, CallStatus.HUNG_UP, CallStatus.ENDED},
    CallStatus.RECORDING: {CallStatus.TRANSFER_PENDING, CallStatus.HUNG_UP, CallStatus.ENDED},
    CallStatus.TRANSFER_PENDING: {CallStatus.HUNG_UP, CallStatus.ENDED},
    CallStatus.HUNG_UP: {CallStatus.ENDED},
    CallStatus.ENDED: set(),
}


class InvalidTransitionError(ValueError):
    pass


class CallStateMachine:
    def __init__(self, call: CallContext):
        self.call = call

    def transition(self, next_status: CallStatus) -> CallStatus:
        current = self.call.status
        if next_status == current:
            return current
        if next_status not in ALLOWED_TRANSITIONS[current]:
            raise InvalidTransitionError(f"invalid transition: {current} -> {next_status}")
        self.call.status = next_status
        return next_status
