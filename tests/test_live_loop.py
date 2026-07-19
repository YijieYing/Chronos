import json
from datetime import UTC, datetime, timedelta
from unittest import TestCase

from chronos.monitor.live import LiveRecognizer
from chronos.monitor.models import Activity, Observation, ObservationKind, Presence
from chronos.monitor.serialization import estimate_to_json, observation_from_json


class LiveLoopTest(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
        self.recognizer = LiveRecognizer()

    def test_json_observations_produce_current_coding_state(self) -> None:
        foreground = observation_from_json(
            json.dumps(
                {
                    "device_id": "macbook",
                    "kind": "foreground.changed",
                    "observed_at": self.now.isoformat(),
                    "payload": {
                        "app_id": "com.microsoft.VSCode",
                        "app_name": "Visual Studio Code",
                        "window_title": "main.swift — Chronos",
                    },
                }
            )
        )
        input_activity = observation_from_json(
            json.dumps(
                {
                    "device_id": "macbook",
                    "kind": "input.activity",
                    "observed_at": (self.now + timedelta(seconds=10)).isoformat(),
                    "payload": {"key_count": 20, "active_seconds": 8},
                }
            )
        )

        self.assertIsNone(self.recognizer.ingest(foreground))
        estimate = self.recognizer.ingest(input_activity)

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertEqual(estimate.presence, Presence.ACTIVE)
        self.assertEqual(estimate.activity, Activity.CODING)
        self.assertEqual(json.loads(estimate_to_json(estimate))["activity"], "coding")

    def test_screen_sleep_immediately_produces_away_state(self) -> None:
        asleep = Observation(
            device_id="macbook",
            kind=ObservationKind.SCREEN_STATE,
            observed_at=self.now,
            payload={"state": "asleep"},
        )

        estimate = self.recognizer.ingest(asleep)

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertEqual(estimate.presence, Presence.AWAY)

    def test_history_is_limited_by_ttl_and_capacity(self) -> None:
        recognizer = LiveRecognizer(capacity=3, history_ttl=timedelta(minutes=10))
        for minute in (0, 1, 2):
            recognizer.ingest(self._input_at(self.now + timedelta(minutes=minute)))
        self.assertEqual(recognizer.history_depth, 3)

        recognizer.ingest(self._input_at(self.now + timedelta(minutes=11)))

        self.assertEqual(recognizer.history_depth, 3)
        self.assertEqual(recognizer.history_capacity, 3)
        self.assertEqual(recognizer.history_ttl, timedelta(minutes=10))

    def test_delayed_observation_older_than_ttl_is_discarded(self) -> None:
        recognizer = LiveRecognizer(history_ttl=timedelta(minutes=10))
        recognizer.ingest(self._input_at(self.now + timedelta(minutes=20)))

        recognizer.ingest(self._input_at(self.now))

        self.assertEqual(recognizer.history_depth, 1)

    @staticmethod
    def _input_at(observed_at: datetime) -> Observation:
        return Observation(
            device_id="macbook",
            kind=ObservationKind.INPUT_ACTIVITY,
            observed_at=observed_at,
            payload={"key_count": 1, "active_seconds": 0.1},
        )
