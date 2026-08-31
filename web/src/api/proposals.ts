import type { TimelineSelection, TimelineTask } from "../types";
import { apiRequest } from "./client";
import type { ProposalPayload, TimelineTaskPayload } from "./contracts";

export interface ScheduleProposal {
  id: string;
  status: ProposalPayload["status"];
  request: string;
  task: TimelineTask | null;
  results: TimelineTask[];
  changes: ProposalPayload["changes"];
  explanation: string[];
  conflicts: ProposalPayload["conflicts"];
  createdAt: number;
  contextUsed: NonNullable<ProposalPayload["context_used"]>;
  parserMode: NonNullable<ProposalPayload["parser_mode"]>;
  parserWarnings: string[];
  proposedTasks: NonNullable<ProposalPayload["proposed_tasks"]>;
  clarifications: NonNullable<ProposalPayload["clarifications"]>;
  assumptions: string[];
  reminderDrafts: NonNullable<ProposalPayload["reminder_drafts"]>;
  requiresConfirmation: boolean;
  readOnly: boolean;
  source: "canonical" | "legacy";
}

export async function createProposal(
  text: string,
  selection: TimelineSelection | null,
): Promise<ScheduleProposal> {
  const payload = await apiRequest<ProposalPayload>("/api/v1/proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      interaction_context: {
        current_time: Date.now(),
        selection,
      },
    }),
  });
  return fromProposal(payload);
}

export async function loadProposals(): Promise<ScheduleProposal[]> {
  const payload = await apiRequest<{ proposals: ProposalPayload[] }>(
    "/api/v1/proposals",
  );
  return payload.proposals.map(fromProposal);
}

export async function resolveProposal(
  id: string,
  accepted: boolean,
): Promise<ScheduleProposal> {
  const action = accepted ? "accept" : "reject";
  const payload = await apiRequest<ProposalPayload>(
    `/api/v1/proposals/${encodeURIComponent(id)}/${action}`,
    { method: "POST" },
  );
  return fromProposal(payload);
}

export async function answerClarification(
  id: string,
  field: string,
  question: string,
  answer: string,
  selection: TimelineSelection | null,
): Promise<ScheduleProposal> {
  const payload = await apiRequest<ProposalPayload>(
    `/api/v1/operations/${encodeURIComponent(id)}/clarify`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        field,
        question,
        answer,
        interaction_context: { current_time: Date.now(), selection },
      }),
    },
  );
  return fromProposal(payload);
}

export async function restoreProposal(id: string): Promise<ScheduleProposal> {
  const payload = await apiRequest<ProposalPayload>(
    `/api/v1/proposals/${encodeURIComponent(id)}/restore`,
    { method: "POST" },
  );
  return fromProposal(payload);
}

function fromProposal(payload: ProposalPayload): ScheduleProposal {
  return {
    id: payload.proposal_id,
    status: payload.status,
    request: payload.request_text,
    task: payload.proposed_task ? fromTimelinePayload(payload.proposed_task) : null,
    results: (payload.results ?? []).map(fromTimelinePayload),
    changes: payload.changes,
    explanation: payload.explanation,
    conflicts: payload.conflicts,
    createdAt: new Date(payload.created_at).getTime(),
    contextUsed: payload.context_used ?? [],
    parserMode: payload.parser_mode ?? "deterministic",
    parserWarnings: payload.parser_warnings ?? [],
    proposedTasks: payload.proposed_tasks ?? [],
    clarifications: payload.clarifications ?? [],
    assumptions: payload.assumptions ?? [],
    reminderDrafts: payload.reminder_drafts ?? [],
    requiresConfirmation: payload.requires_confirmation === true,
    readOnly: payload.read_only === true,
    source: payload.source ?? "legacy",
  };
}

function fromTimelinePayload(payload: TimelineTaskPayload): TimelineTask {
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
