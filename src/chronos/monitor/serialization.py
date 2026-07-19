"""JSON boundary used by platform agents."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from chronos.monitor.models import Observation, ObservationKind, WorkStateEstimate
from chronos.monitor.snapshots import (
    DeviceObservationSnapshot,
    ModuleSnapshot,
    MonitorSnapshot,
    WorkStateSnapshot,
)


def observation_from_json(line: str) -> Observation:
    data: dict[str, Any] = json.loads(line)
    observed_at = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
    observation_id = data.get("observation_id")
    return Observation(
        observation_id=UUID(observation_id) if observation_id else uuid4(),
        device_id=data["device_id"],
        kind=ObservationKind(data["kind"]),
        observed_at=observed_at,
        payload=data.get("payload", {}),
        confidence=float(data.get("confidence", 1.0)),
    )


def estimate_to_json(estimate: WorkStateEstimate) -> str:
    """Legacy flat estimate representation; live output uses MonitorSnapshot."""
    return json.dumps(
        {
            "device_id": estimate.device_id,
            "window_start": estimate.window_start.isoformat(),
            "window_end": estimate.window_end.isoformat(),
            "evaluated_at": estimate.evaluated_at.isoformat(),
            "presence": estimate.presence.value,
            "activity": estimate.activity.value,
            "confidence": round(estimate.confidence, 3),
            "focus_level": round(estimate.focus_level, 3),
            "possible_task_id": estimate.possible_task_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def monitor_snapshot_to_json(snapshot: MonitorSnapshot) -> str:
    return json.dumps(
        {
            "type": "chronos.monitor_snapshot",
            "schema_version": snapshot.schema_version,
            "device_id": snapshot.device_id,
            "generated_at": snapshot.generated_at.isoformat(),
            "observations": _device_snapshot_dict(snapshot.observations),
            "work_state": _work_state_dict(snapshot.work_state),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _device_snapshot_dict(snapshot: DeviceObservationSnapshot) -> dict[str, object]:
    return {
        "schema_version": snapshot.schema_version,
        "generated_at": snapshot.generated_at.isoformat(),
        "modules": {name: _module_dict(module) for name, module in snapshot.modules.items()},
    }


def _work_state_dict(snapshot: WorkStateSnapshot) -> dict[str, object]:
    return {
        "schema_version": snapshot.schema_version,
        "window": {
            "start": snapshot.window_start.isoformat(),
            "end": snapshot.window_end.isoformat(),
        },
        "evaluated_at": snapshot.evaluated_at.isoformat(),
        "modules": {name: _module_dict(module) for name, module in snapshot.modules.items()},
    }


def _module_dict(module: ModuleSnapshot) -> dict[str, object]:
    result: dict[str, object] = {
        "status": module.status.value,
        "observed_at": module.observed_at.isoformat() if module.observed_at else None,
        "schema_version": module.schema_version,
        "data": dict(module.data),
    }
    if module.confidence is not None:
        result["confidence"] = round(module.confidence, 3)
    if module.model_version is not None:
        result["model_version"] = module.model_version
    if module.missing_capabilities:
        result["missing_capabilities"] = list(module.missing_capabilities)
    return result
