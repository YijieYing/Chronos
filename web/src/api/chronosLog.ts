import { apiRequest } from "./client";
import type {
  ChronosLogEntry,
  PendingAgentOperation,
  TimelineReference,
} from "../types";

interface LogEntryPayload {
  schema_version: number;
  id: string;
  event_type: ChronosLogEntry["eventType"];
  occurred_at: string;
  message: string;
  operation_id: string | null;
  references: TimelineReference[];
  metadata: Record<string, unknown>;
}

export async function loadChronosLog(): Promise<{
  entries: ChronosLogEntry[];
  pendingCount: number;
  pendingOperations: PendingAgentOperation[];
}> {
  const result = await apiRequest<{
    entries: LogEntryPayload[];
    pending_count: number;
    pending_operations: Array<{
      id: string;
      state: PendingAgentOperation["state"];
      summary: string;
      questions: PendingAgentOperation["questions"];
      created_at: string;
    }>;
  }>("/api/v1/chronos-log");
  return {
    entries: result.entries.map(fromPayload),
    pendingCount: result.pending_count,
    pendingOperations: result.pending_operations.map((operation) => ({
      id: operation.id,
      state: operation.state,
      summary: operation.summary,
      questions: operation.questions,
      createdAt: new Date(operation.created_at).getTime(),
    })),
  };
}

export async function appendChronosLog(input: {
  eventType: ChronosLogEntry["eventType"];
  message: string;
  operationId?: string;
  references?: TimelineReference[];
  metadata?: Record<string, unknown>;
}): Promise<ChronosLogEntry> {
  const result = await apiRequest<LogEntryPayload>("/api/v1/chronos-log", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_type: input.eventType,
      message: input.message,
      operation_id: input.operationId,
      references: input.references ?? [],
      metadata: input.metadata ?? {},
    }),
  });
  return fromPayload(result);
}

const fromPayload = (entry: LogEntryPayload): ChronosLogEntry => ({
  id: entry.id,
  time: new Date(entry.occurred_at).getTime(),
  eventType: entry.event_type,
  message: entry.message,
  operationId: entry.operation_id ?? undefined,
  references: entry.references,
  metadata: entry.metadata,
});
