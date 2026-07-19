"""Turn normalized observations into deterministic feature windows."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from chronos.monitor.models import AppContext, FeatureWindow, Observation, ObservationKind


class FeatureAggregator:
    def aggregate(
        self,
        observations: list[Observation],
        *,
        device_id: str,
        start_at: datetime,
        end_at: datetime,
        initial_context: AppContext | None = None,
        initial_screen_state: str | None = None,
        initial_device_state: str | None = None,
    ) -> FeatureWindow:
        relevant = sorted(
            (
                item
                for item in observations
                if item.device_id == device_id and start_at <= item.observed_at < end_at
            ),
            key=lambda item: item.observed_at,
        )

        key_count = click_count = 0
        pointer_distance = scroll_distance = active_seconds = 0.0
        context_events: list[tuple[datetime, AppContext]] = []
        screen_state = initial_screen_state
        device_state = initial_device_state

        for item in relevant:
            if item.kind == ObservationKind.INPUT_ACTIVITY:
                key_count += int(item.payload.get("key_count", 0))
                click_count += int(item.payload.get("click_count", 0))
                pointer_distance += float(item.payload.get("pointer_distance", 0.0))
                scroll_distance += float(item.payload.get("scroll_distance", 0.0))
                active_seconds += float(item.payload.get("active_seconds", 0.0))
            elif item.kind == ObservationKind.FOREGROUND_CHANGED:
                context_events.append(
                    (
                        item.observed_at,
                        AppContext(
                            app_id=str(item.payload.get("app_id", "")),
                            app_name=str(item.payload.get("app_name", "")),
                            window_title=_optional_string(item.payload.get("window_title")),
                        ),
                    )
                )
            elif item.kind == ObservationKind.SCREEN_STATE:
                screen_state = _optional_string(item.payload.get("state"))
            elif item.kind == ObservationKind.DEVICE_PRESENCE:
                device_state = _optional_string(item.payload.get("state"))

        app_seconds: defaultdict[str, float] = defaultdict(float)
        cursor = start_at
        current = initial_context
        switches = 0
        for changed_at, new_context in context_events:
            if current is not None:
                app_seconds[current.app_id] += (changed_at - cursor).total_seconds()
            if current is not None and current.app_id != new_context.app_id:
                switches += 1
            current = new_context
            cursor = changed_at
        if current is not None:
            app_seconds[current.app_id] += (end_at - cursor).total_seconds()

        duration = (end_at - start_at).total_seconds()
        return FeatureWindow(
            device_id=device_id,
            start_at=start_at,
            end_at=end_at,
            key_count=key_count,
            click_count=click_count,
            pointer_distance=pointer_distance,
            scroll_distance=scroll_distance,
            active_seconds=min(active_seconds, duration),
            context_switches=switches,
            app_seconds=app_seconds,
            latest_context=current,
            screen_state=screen_state,
            device_state=device_state,
            observation_count=len(relevant),
        )


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
