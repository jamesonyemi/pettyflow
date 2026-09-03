"""Explicit settlement state transition rules."""

from enum import Enum


class SettlementTransitionError(ValueError):
    """Raised when a provider reports an illegal settlement transition."""


class SettlementState(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"
    MANUAL_REVIEW = "manual_review"


_TRANSITIONS = {
    SettlementState.PENDING: {
        SettlementState.PROCESSING,
        SettlementState.FAILED,
        SettlementState.MANUAL_REVIEW,
    },
    SettlementState.PROCESSING: {
        SettlementState.COMPLETED,
        SettlementState.FAILED,
        SettlementState.MANUAL_REVIEW,
    },
    SettlementState.COMPLETED: {SettlementState.REVERSED},
    SettlementState.FAILED: {SettlementState.PENDING, SettlementState.MANUAL_REVIEW},
    SettlementState.REVERSED: set(),
    SettlementState.MANUAL_REVIEW: {
        SettlementState.PENDING,
        SettlementState.COMPLETED,
        SettlementState.FAILED,
        SettlementState.REVERSED,
    },
}


def validate_settlement_transition(
    current: SettlementState, requested: SettlementState
) -> SettlementState:
    if requested not in _TRANSITIONS[current]:
        raise SettlementTransitionError(
            f"illegal settlement transition: {current.value} -> {requested.value}"
        )
    return requested
