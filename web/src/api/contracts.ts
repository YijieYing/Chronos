import type { TimelineSelection, TimelineTask } from "../types";

export interface ApiEnvelope<T> {
  schema_version: number;
  request_id: string;
  data: T | null;
  error: { code: string; message: string } | null;
}

export interface TimelineTaskPayload {
  id: string;
  series_id?: string | null;
  series_start?: number | null;
  title: string;
  start: number;
  end: number;
  predicted_end: number;
  intensity: number;
  spectrum: number;
  fixed: boolean;
  task_type: TimelineTask["type"];
  source: TimelineTask["source"];
  recurrence?: TimelineTask["recurrence"] | null;
  plan_id?: string | null;
  plan_version?: number | null;
  created_at?: number;
  updated_at?: number;
  scheduled?: boolean;
  unscheduled_reason?: string | null;
}

export interface ProposalPayload {
  proposal_id: string;
  status: "pending" | "accepted" | "rejected" | "restored" | "informational" | "needs_clarification" | "stale" | "failed";
  requires_confirmation?: boolean;
  request_text: string;
  proposed_task: TimelineTaskPayload | null;
  proposed_tasks?: Array<{
    task_id: string;
    title: string;
    estimated_minutes: number;
    preferred_start: string;
    recurrence?: TimelineTask["recurrence"] | null;
    fixed: boolean;
  }>;
  results?: TimelineTaskPayload[];
  changes: Array<{ operation: string; task_id: string; summary: string }>;
  conflicts: Array<{ task_id: string; reason: string; remaining_minutes: number }>;
  explanation: string[];
  context_used?: Array<{
    context_id: string;
    source: string;
    category: string;
    content: string;
    source_ref: string;
    score: number;
  }>;
  parser_mode?: "semantic" | "deterministic" | "deterministic_fallback";
  parser_warnings?: string[];
  clarifications?: Array<{ field: string; question: string; options?: string[] }>;
  assumptions?: string[];
  reminder_drafts?: Array<{
    reminder: {
      id: string;
      title: string;
      trigger:
        | { type: "time"; at: number }
        | { type: "window"; start: number; end: number };
      delivery: "exact" | "context-aware";
      priority: number;
      status: "pending" | "delivered" | "done" | "dismissed";
      source: "user" | "agent";
      created_at: string;
    };
  }>;
  created_at: string;
  updated_at: string;
  interaction_context?: {
    current_time: number;
    selection: TimelineSelection | null;
  };
}
