import unittest

from app.decision_engine import DecisionEngine
from app.models import CallContext, Decision


def make_call(confidence: float) -> CallContext:
    return CallContext(call_id="call-1", channel_id="chan-1", confidence=confidence)


class DecisionEngineTests(unittest.TestCase):
    def test_returns_transfer_below_threshold(self) -> None:
        engine = DecisionEngine(transfer_threshold=0.7)
        result = engine.decide(make_call(0.69))
        self.assertEqual(result.decision, Decision.TRANSFER_TO_HUMAN)

    def test_returns_answer_at_threshold(self) -> None:
        engine = DecisionEngine(transfer_threshold=0.7)
        result = engine.decide(make_call(0.7))
        self.assertEqual(result.decision, Decision.ANSWER)

    def test_returns_answer_above_threshold(self) -> None:
        engine = DecisionEngine(transfer_threshold=0.7)
        result = engine.decide(make_call(0.95))
        self.assertEqual(result.decision, Decision.ANSWER)

if __name__ == "__main__":
    unittest.main()
