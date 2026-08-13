import { apiRequest } from "./client";
import type { TimelineProjection, TimelineReference } from "../types";

interface ProjectionPayload {
  id: string;
  operation_id: string;
  type: TimelineProjection["type"];
  target: TimelineReference;
  visual_state: TimelineProjection["visualState"];
  start: number | null;
  end: number | null;
  metadata: Record<string, unknown>;
}

export async function loadTimelineProjections(): Promise<TimelineProjection[]> {
  const result = await apiRequest<{ projections: ProjectionPayload[] }>(
    "/api/v1/timeline-projections",
  );
  return result.projections.map((projection) => ({
    id: projection.id,
    operationId: projection.operation_id,
    type: projection.type,
    target: projection.target,
    visualState: projection.visual_state,
    start: projection.start ?? undefined,
    end: projection.end ?? undefined,
    metadata: projection.metadata,
  }));
}
