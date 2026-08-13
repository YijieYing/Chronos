"""Lifecycle rules for independently versioned Agent Operations."""

from chronos.agent.models import OperationState

TERMINAL_STATES = {
    OperationState.COMPLETED,
    OperationState.REJECTED,
    OperationState.CANCELLED,
}

ACTION_REQUIRED_STATES = {
    OperationState.AWAITING_CLARIFICATION,
    OperationState.PROPOSED,
    OperationState.FAILED,
}

_TRANSITIONS = {
    OperationState.INTERPRETING: {
        OperationState.AWAITING_CLARIFICATION,
        OperationState.READY,
        OperationState.COMPLETED,
        OperationState.FAILED,
        OperationState.CANCELLED,
    },
    OperationState.AWAITING_CLARIFICATION: {
        OperationState.INTERPRETING,
        OperationState.READY,
        OperationState.PROPOSED,
        OperationState.FAILED,
        OperationState.STALE,
        OperationState.CANCELLED,
    },
    OperationState.READY: {
        OperationState.PROPOSED,
        OperationState.APPROVED,
        OperationState.EXECUTING,
        OperationState.FAILED,
        OperationState.STALE,
        OperationState.CANCELLED,
    },
    OperationState.PROPOSED: {
        OperationState.APPROVED,
        OperationState.REJECTED,
        OperationState.STALE,
        OperationState.CANCELLED,
    },
    OperationState.APPROVED: {
        OperationState.EXECUTING,
        OperationState.STALE,
        OperationState.CANCELLED,
    },
    OperationState.EXECUTING: {
        OperationState.COMPLETED,
        OperationState.FAILED,
    },
    OperationState.FAILED: {
        OperationState.INTERPRETING,
        OperationState.CANCELLED,
    },
    OperationState.STALE: {
        OperationState.INTERPRETING,
        OperationState.AWAITING_CLARIFICATION,
        OperationState.PROPOSED,
        OperationState.COMPLETED,
        OperationState.FAILED,
        OperationState.CANCELLED,
    },
}

_REFRESHABLE = {
    OperationState.INTERPRETING,
    OperationState.AWAITING_CLARIFICATION,
    OperationState.READY,
    OperationState.PROPOSED,
    OperationState.FAILED,
    OperationState.STALE,
}


def validate_transition(current: OperationState, target: OperationState) -> None:
    if current == target and current in _REFRESHABLE:
        return
    if target not in _TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal Agent Operation transition: {current.value} -> {target.value}")
