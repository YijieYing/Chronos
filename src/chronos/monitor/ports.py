"""Monitor boundaries implemented by platform agents and semantic providers."""

from collections.abc import AsyncIterator
from typing import Protocol

from chronos.monitor.models import FeatureWindow, Observation, WorkStateEstimate


class ObservationCollector(Protocol):
    @property
    def device_id(self) -> str: ...

    def observations(self) -> AsyncIterator[Observation]: ...


class SemanticInferenceProvider(Protocol):
    """Optional LLM/model adapter for ambiguous activity and task semantics."""

    def infer(self, features: FeatureWindow, base: WorkStateEstimate) -> WorkStateEstimate: ...
