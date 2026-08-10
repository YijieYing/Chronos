from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from chronos.infrastructure.sqlite_cognitive_state import SQLiteCognitiveStateRepository
from chronos.monitor.cognitive import CognitiveStateEstimator, RecoveryState
from chronos.monitor.models import Activity, Presence, WorkStateEstimate


class CognitiveStateEstimatorTest(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)

    def test_work_and_recovery_keep_load_and_fatigue_separate(self) -> None:
        estimator = CognitiveStateEstimator()
        first = estimator.ingest(self._estimate(self.now, active=True))
        later = first
        for index in range(1, 13):
            later = estimator.ingest(
                self._estimate(self.now + timedelta(minutes=5 * index), active=True)
            )
        recovering = estimator.ingest(
            self._estimate(self.now + timedelta(minutes=65), active=False)
        )

        self.assertEqual(first.recovery_state, RecoveryState.WORKING)
        self.assertGreater(later.mental_fatigue, first.mental_fatigue)
        self.assertEqual(recovering.recovery_state, RecoveryState.RECOVERING)
        self.assertLess(recovering.cognitive_load, later.cognitive_load)

    def test_repository_upserts_one_point_per_bucket(self) -> None:
        with TemporaryDirectory() as temporary:
            repository = SQLiteCognitiveStateRepository(
                Path(temporary) / "chronos.sqlite3"
            )
            estimator = CognitiveStateEstimator()
            first = estimator.ingest(self._estimate(self.now, active=True))
            revised = estimator.ingest(
                self._estimate(self.now + timedelta(minutes=1), active=True)
            )
            repository.upsert(first)
            repository.upsert(revised)

            points = repository.between(
                self.now - timedelta(minutes=1),
                self.now + timedelta(minutes=5),
            )

            self.assertEqual(len(points), 1)
            self.assertEqual(points[0].revision, revised.revision)

    def _estimate(self, at: datetime, *, active: bool) -> WorkStateEstimate:
        return WorkStateEstimate(
            device_id="macbook",
            window_start=at - timedelta(seconds=30),
            window_end=at,
            evaluated_at=at,
            presence=Presence.ACTIVE if active else Presence.IDLE,
            activity=Activity.CODING if active else Activity.UNKNOWN,
            confidence=0.85,
            focus_level=0.8 if active else 0.15,
        )
