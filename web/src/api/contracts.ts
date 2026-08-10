import type { TimelineTask } from "../types";

export interface ApiEnvelope<T> {
  schema_version: number;
  request_id: string;
  data: T | null;
  error: { code: string; message: string } | null;
}

export interface TimelineTaskPayload {
  id: string;
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
  status: "pending" | "accepted" | "rejected" | "restored" | "informational";
  requires_confirmation?: boolean;
  request_text: string;
  proposed_task: TimelineTaskPayload | null;
  results?: TimelineTaskPayload[];
  changes: Array<{ operation: string; task_id: string; summary: string }>;
  conflicts: Array<{ task_id: string; reason: string; remaining_minutes: number }>;
  explanation: string[];
  created_at: string;
  updated_at: string;
}
