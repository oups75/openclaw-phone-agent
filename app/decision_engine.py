from dataclasses import dataclass

from app.models import CallContext, Decision


@dataclass(slots=True)
class DecisionResult:
    decision: Decision
    confidence: float
    explanation: str


class DecisionEngine:
    def __init__(self, transfer_threshold: float = 0.7):
        self.transfer_threshold = transfer_threshold

    def decide(self, call_context: CallContext) -> DecisionResult:
        if call_context.confidence < self.transfer_threshold:
            return DecisionResult(
                decision=Decision.TRANSFER_TO_HUMAN,
                confidence=call_context.confidence,
                explanation="Confidence below threshold; route to human operator.",
            )
        return DecisionResult(
            decision=Decision.ANSWER,
            confidence=call_context.confidence,
            explanation="Confidence is sufficient for automated handling.",
        )
