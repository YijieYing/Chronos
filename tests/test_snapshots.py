import json
from datetime import UTC, datetime, timedelta
from unittest import TestCase

from chronos.monitor.models import (
    Activity,
    Observation,
    ObservationKind,
    Presence,
    WorkStateEstimate,
)
from chronos.monitor.serialization import monitor_snapshot_to_json
from chronos.monitor.snapshots import (
    ModuleStatus,
    MonitorSnapshot,
    SnapshotAssembler,
    WorkStateSnapshot,
)


class SnapshotAssemblerTest(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
        self.assembler = SnapshotAssembler(input_stale_after=timedelta(seconds=30))

    def test_independent_observations_are_assembled_into_modules(self) -> None:
        self.assembler.ingest(
            self._observation(
                ObservationKind.INPUT_ACTIVITY,
                {"key_count": 12, "active_seconds": 4.0},
            )
        )
        self.assembler.ingest(
            self._observation(
                ObservationKind.FOREGROUND_CHANGED,
                {"app_id": "com.microsoft.VSCode", "app_name": "Visual Studio Code"},
            )
        )
        self.assembler.ingest(
            self._observation(ObservationKind.SCREEN_STATE, {"state": "awake"})
        )
        self.assembler.ingest(
            self._observation(ObservationKind.DEVICE_PRESENCE, {"state": "active"})
        )

        snapshot = self.assembler.snapshot("macbook", generated_at=self.now)

        self.assertEqual(snapshot.modules["input_activity"].status, ModuleStatus.AVAILABLE)
        self.assertEqual(
            snapshot.modules["foreground_context"].data["app_id"],
            "com.microsoft.VSCode",
        )
        self.assertEqual(snapshot.modules["session_state"].data["screen_state"], "awake")
        self.assertEqual(snapshot.modules["session_state"].data["device_state"], "active")

    def test_status_and_staleness_are_visible_without_merging_collectors(self) -> None:
        self.assembler.ingest(
            self._observation(
                ObservationKind.COLLECTOR_STATUS,
                {
                    "module": "foreground_context",
                    "status": "degraded",
                    "missing_capabilities": ["window_title"],
                },
            )
        )
        self.assembler.ingest(
            self._observation(
                ObservationKind.FOREGROUND_CHANGED,
                {"app_id": "com.apple.Safari", "app_name": "Safari"},
            )
        )
        self.assembler.ingest(
            self._observation(ObservationKind.INPUT_ACTIVITY, {"key_count": 1})
        )

        snapshot = self.assembler.snapshot(
            "macbook", generated_at=self.now + timedelta(seconds=31)
        )

        self.assertEqual(snapshot.modules["input_activity"].status, ModuleStatus.STALE)
        foreground = snapshot.modules["foreground_context"]
        self.assertEqual(foreground.status, ModuleStatus.DEGRADED)
        self.assertEqual(foreground.missing_capabilities, ("window_title",))

    def test_monitor_snapshot_serializes_both_module_groups(self) -> None:
        estimate = WorkStateEstimate(
            device_id="macbook",
            window_start=self.now - timedelta(seconds=30),
            window_end=self.now,
            evaluated_at=self.now,
            presence=Presence.ACTIVE,
            activity=Activity.CODING,
            confidence=0.82,
            focus_level=0.6,
        )
        observation_snapshot = self.assembler.snapshot("macbook", generated_at=self.now)
        snapshot = MonitorSnapshot(
            device_id="macbook",
            generated_at=self.now,
            observations=observation_snapshot,
            work_state=WorkStateSnapshot.from_estimate(estimate),
        )

        data = json.loads(monitor_snapshot_to_json(snapshot))

        self.assertEqual(data["type"], "chronos.monitor_snapshot")
        self.assertIn("input_activity", data["observations"]["modules"])
        self.assertEqual(
            data["work_state"]["modules"]["activity"]["data"]["category"],
            "coding",
        )
        self.assertEqual(
            data["work_state"]["modules"]["engagement"]["data"]["level"],
            0.6,
        )

    def _observation(
        self, kind: ObservationKind, payload: dict[str, object]
    ) -> Observation:
        return Observation(
            device_id="macbook",
            kind=kind,
            observed_at=self.now,
            payload=payload,
        )
