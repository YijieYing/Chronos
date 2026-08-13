import { useCallback, useEffect, useState } from "react";
import { loadTimelineProjections } from "../api/projections";
import type { TimelineProjection } from "../types";

export function useProjectionStore() {
  const [projections, setProjections] = useState<TimelineProjection[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setProjections(await loadTimelineProjections());
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { projections, error, refresh };
}
