"""Autonomy policy presets and the single direct-execution decision gate."""

from __future__ import annotations

from dataclasses import dataclass

from chronos.agent.models import AgentOperation, AutonomyPolicy, OperationState


@dataclass(frozen=True, slots=True)
class AutonomyDecision:
    execute: bool
    reason: str


def policy_for_level(level: int) -> AutonomyPolicy:
    presets = {
        0: AutonomyPolicy(0, 0, 0, 0),
        1: AutonomyPolicy(1, 0.15, 0.15, 0.2),
        2: AutonomyPolicy(2, 0.35, 0.2, 0.5),
        3: AutonomyPolicy(3, 0.65, 0.3, 0.8),
    }
    if level not in presets:
        raise ValueError("autonomy level must be from 0 to 3")
    return presets[level]


def evaluate_autonomy(
    operation: AgentOperation, policy: AutonomyPolicy
) -> AutonomyDecision:
    if operation.state != OperationState.PROPOSED:
        return AutonomyDecision(False, "operation is not executable")
    if policy.level == 0:
        return AutonomyDecision(False, "Suggest Only requires confirmation")
    checks = (
        (operation.required_autonomy_level <= policy.level, "autonomy level is too low"),
        (operation.risk <= policy.max_risk, "risk exceeds policy threshold"),
        (operation.ambiguity <= policy.max_ambiguity, "ambiguity exceeds policy threshold"),
        (operation.impact <= policy.max_impact, "impact exceeds policy threshold"),
        (not policy.require_reversible or operation.reversible, "operation is not reversible"),
    )
    for allowed, reason in checks:
        if not allowed:
            return AutonomyDecision(False, reason)
    return AutonomyDecision(True, "within autonomy policy")
