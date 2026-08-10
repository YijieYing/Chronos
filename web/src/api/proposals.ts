import type { TimelineTask } from "../types";
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
}

export async function createProposal(text: string): Promise<ScheduleProposal> {
  const payload = await apiRequest<ProposalPayload>("/api/v1/proposals", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
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
    scheduled: payload.scheduled,
    unscheduledReason: payload.unscheduled_reason ?? undefined,
  };
}
