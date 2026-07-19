"""Fast local state estimation with an optional semantic inference boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from chronos.monitor.models import Activity, FeatureWindow, Presence, WorkStateEstimate
from chronos.monitor.ports import SemanticInferenceProvider


class RuleBasedStateEstimator:
    _CODING_APPS = {
        "com.microsoft.VSCode",
        "com.apple.dt.Xcode",
        "com.jetbrains.intellij",
        "com.googlecode.iterm2",
        "com.apple.Terminal",
    }
    _BROWSER_APPS = {
        "com.apple.Safari",
        "com.google.Chrome",
        "org.mozilla.firefox",
    }
    _COMMUNICATION_APPS = {
        "com.tinyspeck.slackmacgap",
        "com.tencent.xinWeChat",
        "com.microsoft.teams2",
    }

    def __init__(self, semantic_provider: SemanticInferenceProvider | None = None) -> None:
        self._semantic_provider = semantic_provider

    def estimate(self, features: FeatureWindow) -> WorkStateEstimate:
        duration = (features.end_at - features.start_at).total_seconds()
        activity_ratio = min(features.active_seconds / duration, 1.0)
        has_input = features.key_count + features.click_count > 0 or features.scroll_distance > 0
        unavailable = features.device_state in {"inactive", "unavailable", "sleeping"}
        screen_asleep = features.screen_state in {"asleep", "locked"}
        if unavailable or screen_asleep:
            presence = Presence.AWAY
        else:
            presence = Presence.ACTIVE if has_input or activity_ratio >= 0.1 else Presence.IDLE

        app_id = features.latest_context.app_id if features.latest_context else ""
        activity, confidence = self._classify(app_id, has_input and presence == Presence.ACTIVE)
        switch_penalty = min(features.context_switches / 10.0, 0.5)
        focus_level = max(0.0, min(1.0, activity_ratio * (1.0 - switch_penalty)))

        base = WorkStateEstimate(
            device_id=features.device_id,
            window_start=features.start_at,
            window_end=features.end_at,
            evaluated_at=datetime.now(UTC),
            presence=presence,
            activity=activity,
            confidence=confidence,
            focus_level=focus_level,
        )
        if self._semantic_provider is not None and base.confidence < 0.8:
            return self._semantic_provider.infer(features, base)
        return base

    def _classify(self, app_id: str, has_input: bool) -> tuple[Activity, float]:
        if not has_input:
            return Activity.UNKNOWN, 0.4
        if app_id in self._CODING_APPS:
            return Activity.CODING, 0.82
        if app_id in self._BROWSER_APPS:
            return Activity.RESEARCHING, 0.58
        if app_id in self._COMMUNICATION_APPS:
            return Activity.COMMUNICATING, 0.78
        return Activity.UNKNOWN, 0.3
