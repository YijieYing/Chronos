"""Materialized monitor views without coupling the underlying collector modules."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from chronos.monitor.models import Observation, ObservationKind, Presence, WorkStateEstimate


class ModuleStatus(StrEnum):
    AVAILABLE = "available"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    PERMISSION_REQUIRED = "permission_required"
    PERMISSION_DENIED = "permission_denied"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ModuleSnapshot:
    status: ModuleStatus
    observed_at: datetime | None
    data: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = 1
    confidence: float | None = None
    model_version: str | None = None
    missing_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", MappingProxyType(dict(self.data)))


@dataclass(frozen=True, slots=True)
class DeviceObservationSnapshot:
    device_id: str
    generated_at: datetime
    modules: Mapping[str, ModuleSnapshot]
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "modules", MappingProxyType(dict(self.modules)))


@dataclass(frozen=True, slots=True)
class WorkStateSnapshot:
    device_id: str
    window_start: datetime
    window_end: datetime
    evaluated_at: datetime
    modules: Mapping[str, ModuleSnapshot]
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "modules", MappingProxyType(dict(self.modules)))

    @classmethod
    def from_estimate(cls, estimate: WorkStateEstimate) -> WorkStateSnapshot:
        presence_confidence = {
            Presence.AWAY: 0.98,
            Presence.ACTIVE: 0.9,
            Presence.IDLE: 0.65,
            Presence.PASSIVE: 0.55,
            Presence.UNKNOWN: 0.3,
        }[estimate.presence]
        return cls(
            device_id=estimate.device_id,
            window_start=estimate.window_start,
            window_end=estimate.window_end,
            evaluated_at=estimate.evaluated_at,
            modules={
                "presence": ModuleSnapshot(
                    status=ModuleStatus.AVAILABLE,
                    observed_at=estimate.evaluated_at,
                    data={"state": estimate.presence.value},
                    confidence=presence_confidence,
                    model_version="presence-rules-v1",
                ),
                "activity": ModuleSnapshot(
                    status=ModuleStatus.AVAILABLE,
                    observed_at=estimate.evaluated_at,
                    data={
                        "category": estimate.activity.value,
                        "possible_task_id": estimate.possible_task_id,
                    },
                    confidence=estimate.confidence,
                    model_version="activity-rules-v1",
                ),
                "engagement": ModuleSnapshot(
                    status=ModuleStatus.AVAILABLE,
                    observed_at=estimate.evaluated_at,
                    data={
                        "level": estimate.focus_level,
                        "interpretation": "interaction_proxy",
                    },
                    confidence=0.55,
                    model_version="interaction-v1",
                ),
            },
        )


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    device_id: str
    generated_at: datetime
    observations: DeviceObservationSnapshot
    work_state: WorkStateSnapshot
    schema_version: int = 1


class SnapshotAssembler:
    """Maintain one materialized view while collectors continue emitting independently."""

    _MODULES = ("input_activity", "foreground_context", "session_state")

    def __init__(self, *, input_stale_after: timedelta = timedelta(seconds=30)) -> None:
        if input_stale_after.total_seconds() <= 0:
            raise ValueError("input_stale_after must be positive")
        self._input_stale_after = input_stale_after
        self._modules: dict[str, dict[str, ModuleSnapshot]] = {}

    def ingest(self, observation: Observation) -> None:
        modules = self._modules.setdefault(observation.device_id, {})
        if observation.kind == ObservationKind.INPUT_ACTIVITY:
            modules["input_activity"] = self._with_data(
                modules.get("input_activity"), observation
            )
        elif observation.kind == ObservationKind.FOREGROUND_CHANGED:
            modules["foreground_context"] = self._with_data(
                modules.get("foreground_context"), observation
            )
        elif observation.kind in {ObservationKind.SCREEN_STATE, ObservationKind.DEVICE_PRESENCE}:
            previous = modules.get("session_state")
            data = dict(previous.data) if previous else {}
            key = "screen_state" if observation.kind == ObservationKind.SCREEN_STATE else "device_state"
            data[key] = observation.payload.get("state")
            if reason := observation.payload.get("reason"):
                data["reason"] = reason
            modules["session_state"] = ModuleSnapshot(
                status=ModuleStatus.AVAILABLE,
                observed_at=observation.observed_at,
                data=data,
            )
        elif observation.kind == ObservationKind.COLLECTOR_STATUS:
            self._update_status(modules, observation)

    def snapshot(self, device_id: str, *, generated_at: datetime) -> DeviceObservationSnapshot:
        current = self._modules.get(device_id, {})
        modules: dict[str, ModuleSnapshot] = {}
        for name in self._MODULES:
            module = current.get(name)
            if module is None:
                modules[name] = ModuleSnapshot(
                    status=ModuleStatus.UNAVAILABLE,
                    observed_at=None,
                )
            elif (
                name == "input_activity"
                and module.observed_at is not None
                and generated_at - module.observed_at > self._input_stale_after
            ):
                modules[name] = replace(module, status=ModuleStatus.STALE)
            else:
                modules[name] = module
        return DeviceObservationSnapshot(
            device_id=device_id,
            generated_at=generated_at,
            modules=modules,
        )

    @staticmethod
    def _with_data(
        previous: ModuleSnapshot | None, observation: Observation
    ) -> ModuleSnapshot:
        status = ModuleStatus.AVAILABLE
        missing_capabilities: tuple[str, ...] = ()
        if previous and previous.status not in {
            ModuleStatus.AVAILABLE,
            ModuleStatus.STALE,
            ModuleStatus.UNAVAILABLE,
        }:
            status = previous.status
            missing_capabilities = previous.missing_capabilities
        return ModuleSnapshot(
            status=status,
            observed_at=observation.observed_at,
            data=observation.payload,
            missing_capabilities=missing_capabilities,
        )

    @staticmethod
    def _update_status(
        modules: dict[str, ModuleSnapshot], observation: Observation
    ) -> None:
        name = str(observation.payload.get("module", ""))
        if not name:
            return
        try:
            status = ModuleStatus(str(observation.payload.get("status", "failed")))
        except ValueError:
            status = ModuleStatus.FAILED
        previous = modules.get(name)
        modules[name] = ModuleSnapshot(
            status=status,
            observed_at=observation.observed_at,
            data=previous.data if previous else {},
            missing_capabilities=tuple(
                str(value) for value in observation.payload.get("missing_capabilities", [])
            ),
        )
