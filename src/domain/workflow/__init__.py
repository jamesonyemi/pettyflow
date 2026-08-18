"""
PettyFlow Approval Workflow Domain
Deterministic state-machine driven approval engine with configurable
enterprise policy rules and multi-tier threshold-based approval chains.
"""

from .state_machine import (
    ApprovalState,
    ApprovalEvent,
    ApprovalRequest,
    WorkflowStateMachine,
    InvalidStateTransitionException,
    ApprovalWorkflowException,
)
from .policy_evaluator import (
    ApprovalTier,
    PolicyRule,
    PolicyEvaluationResult,
    ApprovalPolicyEvaluator,
    DEFAULT_PETTYFLOW_POLICY,
)

__all__ = [
    # State Machine
    "ApprovalState",
    "ApprovalEvent",
    "ApprovalRequest",
    "WorkflowStateMachine",
    "InvalidStateTransitionException",
    "ApprovalWorkflowException",
    # Policy Evaluator
    "ApprovalTier",
    "PolicyRule",
    "PolicyEvaluationResult",
    "ApprovalPolicyEvaluator",
    "DEFAULT_PETTYFLOW_POLICY",
]
