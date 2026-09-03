import pytest

from src.domain.wallet.settlement import (
    SettlementState,
    SettlementTransitionError,
    validate_settlement_transition,
)


def test_processing_can_complete():
    assert (
        validate_settlement_transition(
            SettlementState.PROCESSING, SettlementState.COMPLETED
        )
        == SettlementState.COMPLETED
    )


def test_completed_cannot_return_to_processing():
    with pytest.raises(SettlementTransitionError):
        validate_settlement_transition(
            SettlementState.COMPLETED, SettlementState.PROCESSING
        )
