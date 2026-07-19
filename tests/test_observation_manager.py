from datetime import UTC, datetime, timedelta
from unittest import TestCase

from chronos.models import Observation, ObservationKind
from chronos.observation import ObservationManager


class ObservationManagerTest(TestCase):
    def test_queue_is_bounded_and_reports_eviction(self) -> None:
        manager = ObservationManager(capacity=2)
        now = datetime.now(UTC)
        observations = [
            Observation(
                device_id="macbook",
                kind=ObservationKind.INPUT_ACTIVITY,
                observed_at=now + timedelta(seconds=index),
            )
            for index in range(3)
        ]

        for observation in observations:
            manager.ingest(observation)

        self.assertEqual(manager.depth, 2)
        self.assertEqual(manager.evicted_count, 1)
        self.assertEqual(manager.drain(), observations[1:])

    def test_duplicate_does_not_consume_capacity(self) -> None:
        manager = ObservationManager(capacity=2)
        observation = Observation(
            device_id="macbook",
            kind=ObservationKind.SCREEN_STATE,
            observed_at=datetime.now(UTC),
        )

        self.assertTrue(manager.ingest(observation).accepted)
        result = manager.ingest(observation)

        self.assertFalse(result.accepted)
        self.assertTrue(result.duplicate)
        self.assertEqual(manager.depth, 1)
        self.assertEqual(manager.duplicate_count, 1)

