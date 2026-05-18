import unittest

from app.call_state import CallStateMachine, InvalidTransitionError
from app.models import CallContext, CallStatus


class CallStateTests(unittest.TestCase):
    def test_valid_transition_path(self) -> None:
        call = CallContext(call_id="call-1", channel_id="chan-1")
        machine = CallStateMachine(call)

        self.assertEqual(machine.transition(CallStatus.RINGING), CallStatus.RINGING)
        self.assertEqual(machine.transition(CallStatus.ANSWERED), CallStatus.ANSWERED)
        self.assertEqual(machine.transition(CallStatus.GREETING), CallStatus.GREETING)
        self.assertEqual(machine.transition(CallStatus.RECORDING), CallStatus.RECORDING)
        self.assertEqual(machine.transition(CallStatus.HUNG_UP), CallStatus.HUNG_UP)
        self.assertEqual(machine.transition(CallStatus.ENDED), CallStatus.ENDED)

    def test_invalid_transition_raises(self) -> None:
        call = CallContext(call_id="call-1", channel_id="chan-1")
        machine = CallStateMachine(call)

        with self.assertRaises(InvalidTransitionError):
            machine.transition(CallStatus.RECORDING)


if __name__ == "__main__":
    unittest.main()
