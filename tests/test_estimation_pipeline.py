from datetime import UTC, datetime, timedelta
from unittest import TestCase

from chronos.estimation import FeatureAggregator, RuleBasedStateEstimator, SegmentBuilder
from chronos.models import (
    Activity,
    Observation,
    ObservationKind,
    Presence,
    SegmentStatus,
    WorkStateEstimate,
)


class EstimationPipelineTest(TestCase):
    def setUp(self) -> None:
        self.start = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
        self.end = self.start + timedelta(minutes=5)

    def test_aggregate_and_estimate_coding(self) -> None:
        observations = [
            Observation(
                device_id="macbook",
                kind=ObservationKind.FOREGROUND_CHANGED,
                observed_at=self.start,
                payload={
                    "app_id": "com.microsoft.VSCode",
                    "app_name": "Visual Studio Code",
                    "window_title": "estimator.py — Chronos",
                },
            ),
            Observation(
                device_id="macbook",
                kind=ObservationKind.INPUT_ACTIVITY,
                observed_at=self.start + timedelta(seconds=30),
                payload={"key_count": 80, "click_count": 3, "active_seconds": 24},
            ),
        ]

        features = FeatureAggregator().aggregate(
            observations,
            device_id="macbook",
            start_at=self.start,
            end_at=self.end,
        )
        estimate = RuleBasedStateEstimator().estimate(features)

        self.assertEqual(features.key_count, 80)
        self.assertEqual(features.app_seconds["com.microsoft.VSCode"], 300)
        self.assertEqual(estimate.presence, Presence.ACTIVE)
        self.assertEqual(estimate.activity, Activity.CODING)
        self.assertGreaterEqual(estimate.confidence, 0.8)

    def test_segment_switch_requires_repeated_estimates(self) -> None:
        builder = SegmentBuilder(switch_confirmation_count=2)
        coding = self._estimate(0, Activity.CODING)
        researching_one = self._estimate(1, Activity.RESEARCHING)
        researching_two = self._estimate(2, Activity.RESEARCHING)

        self.assertEqual(builder.add(coding), [])
        self.assertEqual(builder.add(researching_one), [])
        finalized = builder.add(researching_two)

        self.assertEqual(len(finalized), 1)
        self.assertEqual(finalized[0].activity, Activity.CODING)
        self.assertEqual(finalized[0].status, SegmentStatus.FINALIZED)
        self.assertEqual(finalized[0].end_at, researching_one.window_start)

        current = builder.flush()
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.activity, Activity.RESEARCHING)
        self.assertEqual(current.estimate_count, 2)

    def test_one_window_activity_change_is_absorbed_as_noise(self) -> None:
        builder = SegmentBuilder(switch_confirmation_count=2)
        builder.add(self._estimate(0, Activity.CODING))
        builder.add(self._estimate(1, Activity.RESEARCHING))
        builder.add(self._estimate(2, Activity.CODING))

        segment = builder.flush()

        self.assertIsNotNone(segment)
        assert segment is not None
        self.assertEqual(segment.activity, Activity.CODING)
        self.assertEqual(segment.end_at, self.start + timedelta(minutes=15))

    def _estimate(self, index: int, activity: Activity) -> WorkStateEstimate:
        start = self.start + timedelta(minutes=5 * index)
        return WorkStateEstimate(
            device_id="macbook",
            window_start=start,
            window_end=start + timedelta(minutes=5),
            evaluated_at=start + timedelta(minutes=5),
            presence=Presence.ACTIVE,
            activity=activity,
            confidence=0.8,
            focus_level=0.7,
        )

