import type { TimelineTask } from "../types";
import { apiRequest } from "../api/client";
import type { TimelineTaskPayload } from "../api/contracts";

interface TimelineResponse {
  tasks: TimelineTaskPayload[];
  settings: { timezone: string };
}

interface TimelineTaskWritePayload extends TimelineTaskPayload {
  created_at: number;
  updated_at: number;
}

const timestamps = new Map<string, { createdAt: number; updatedAt: number }>();

export async function loadTimelineTasks(): Promise<TimelineTask[]> {
  const response = await apiRequest<TimelineResponse>("/api/v1/schedule/timeline");
  return response.tasks.map(fromPayload);
}

export async function createTimelineTask(task: TimelineTask): Promise<TimelineTask[]> {
  const now = Date.now();
  timestamps.set(task.id, { createdAt: now, updatedAt: now });
  const result = await apiRequest<{ timeline: TimelineResponse }>("/api/v1/schedule/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toPayload(task)),
  });
  return result.timeline.tasks.map(fromPayload);
}

export async function saveTimelineTask(task: TimelineTask): Promise<TimelineTask[]> {
  const current = timestamps.get(task.id);
  const now = Date.now();
  timestamps.set(task.id, {
    createdAt: current?.createdAt ?? now,
    updatedAt: now,
  });
  const result = await apiRequest<{ timeline: TimelineResponse }>(
    `/api/v1/schedule/tasks/${encodeURIComponent(task.id)}`,
    {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(toPayload(task)),
    },
  );
  return result.timeline.tasks.map(fromPayload);
}

export async function deleteTimelineTask(taskId: string): Promise<void> {
  await apiRequest(`/api/v1/schedule/tasks/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
  timestamps.delete(taskId);
}

function fromPayload(payload: TimelineTaskPayload): TimelineTask {
  const now = Date.now();
  timestamps.set(payload.id, {
    createdAt: payload.created_at ?? now,
    updatedAt: payload.updated_at ?? now,
  });
  return {
    id: payload.id,
    title: payload.title,
    start: payload.start,
    end: payload.end,
    predictedEnd: payload.predicted_end,
    intensity: payload.intensity,
    spectrum: payload.spectrum,
    fixed: payload.fixed,
    type: payload.task_type,
    source: payload.source,
    recurrence: payload.recurrence ?? undefined,
    seriesId: payload.series_id ?? undefined,
    seriesStart: payload.series_start ?? undefined,
    scheduled: payload.scheduled,
    unscheduledReason: payload.unscheduled_reason ?? undefined,
  };
}

function toPayload(task: TimelineTask): TimelineTaskWritePayload {
  const now = Date.now();
  const current = timestamps.get(task.id) ?? { createdAt: now, updatedAt: now };
  return {
    id: task.id,
    title: task.title,
    start: task.start,
    end: task.end,
    predicted_end: task.predictedEnd,
    intensity: task.intensity,
    spectrum: task.spectrum,
    fixed: task.fixed,
    task_type: task.type,
    source: task.source,
    recurrence: task.recurrence ?? null,
    created_at: current.createdAt,
    updated_at: current.updatedAt,
  };
}
