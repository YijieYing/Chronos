"""Hard-constraint validation and free-time calculation."""

from __future__ import annotations

from datetime import datetime

from chronos.schedule.models import AvailabilityWindow, FixedBlock, ScheduleBlock


def calculate_free_windows(
    availability: AvailabilityWindow,
    fixed_blocks: list[FixedBlock],
) -> list[tuple[datetime, datetime]]:
    windows = [(availability.start_at, availability.end_at)]
    for fixed in sorted(fixed_blocks, key=lambda item: item.start_at):
        next_windows: list[tuple[datetime, datetime]] = []
        for start_at, end_at in windows:
            if fixed.end_at <= start_at or fixed.start_at >= end_at:
                next_windows.append((start_at, end_at))
                continue
            if fixed.start_at > start_at:
                next_windows.append((start_at, min(fixed.start_at, end_at)))
            if fixed.end_at < end_at:
                next_windows.append((max(fixed.end_at, start_at), end_at))
        windows = next_windows
    return windows


def validate_plan(
    blocks: tuple[ScheduleBlock, ...],
    availability: AvailabilityWindow,
    fixed_blocks: list[FixedBlock],
) -> None:
    ordered = sorted(blocks, key=lambda item: item.start_at)
    for block in ordered:
        if block.start_at < availability.start_at or block.end_at > availability.end_at:
            raise ValueError(f"block {block.block_id} is outside availability")
        for fixed in fixed_blocks:
            if _overlaps(block.start_at, block.end_at, fixed.start_at, fixed.end_at):
                raise ValueError(f"block {block.block_id} overlaps fixed block {fixed.block_id}")
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if previous.end_at > current.start_at:
            raise ValueError(f"blocks {previous.block_id} and {current.block_id} overlap")


def _overlaps(
    left_start: datetime,
    left_end: datetime,
    right_start: datetime,
    right_end: datetime,
) -> bool:
    return left_start < right_end and right_start < left_end
