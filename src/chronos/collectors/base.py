"""Boundary implemented by the future macOS, Linux, and Windows agents."""

from collections.abc import AsyncIterator
from typing import Protocol

from chronos.models import Observation


class ObservationCollector(Protocol):
    @property
    def device_id(self) -> str: ...

    def observations(self) -> AsyncIterator[Observation]: ...

