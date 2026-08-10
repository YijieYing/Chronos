import { apiRequest } from "./client";

export type MemorySource = "chatgpt" | "claude";

export interface MemoryCandidate {
  candidate_id: string;
  source: MemorySource;
  category: string;
  content: string;
  evidence: string;
  source_ref: string;
  confidence: number;
  status: "pending" | "accepted" | "ignored";
  created_at: string;
  change_type?: "new" | "possible_update" | "possible_conflict";
  related_context_id?: string | null;
  related_content?: string | null;
}

export interface MemoryImport {
  import_id: string;
  source: MemorySource;
  archive_name: string;
  archive_path: string;
  status: string;
  messages_scanned: number;
  candidates_created: number;
  created_at: string;
  duplicate?: boolean;
}

export interface AgentContextItem {
  context_id: string;
  source: MemorySource;
  category: string;
  content: string;
  source_ref: string;
  updated_at: string;
  revision?: number;
}

export async function uploadMemoryDocument(
  file: File,
  source: MemorySource,
): Promise<MemoryImport> {
  return apiRequest<MemoryImport>(
    `/api/v1/agent/imports?source=${source}&filename=${encodeURIComponent(file.name)}`,
    { method: "POST", body: file },
  );
}

export async function loadMemoryCandidates(): Promise<MemoryCandidate[]> {
  const data = await apiRequest<{ candidates: MemoryCandidate[] }>(
    "/api/v1/agent/memory/candidates",
  );
  return data.candidates;
}

export async function loadMemoryImports(): Promise<MemoryImport[]> {
  const data = await apiRequest<{ imports: MemoryImport[] }>("/api/v1/agent/imports");
  return data.imports;
}

export async function loadAgentContext(): Promise<AgentContextItem[]> {
  const data = await apiRequest<{ items: AgentContextItem[] }>(
    "/api/v1/agent/memory/items",
  );
  return data.items;
}

export function reviewMemoryCandidate(
  id: string,
  accepted: boolean,
): Promise<MemoryCandidate> {
  const action = accepted ? "accept" : "ignore";
  return apiRequest<MemoryCandidate>(
    `/api/v1/agent/memory/candidates/${encodeURIComponent(id)}/${action}`,
    { method: "POST" },
  );
}

export function updateAgentContext(
  id: string,
  content: string,
  category: string,
): Promise<AgentContextItem> {
  return apiRequest<AgentContextItem>(
    `/api/v1/agent/memory/items/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, category }),
    },
  );
}

export function deleteAgentContext(id: string): Promise<{ deleted: boolean }> {
  return apiRequest<{ deleted: boolean }>(
    `/api/v1/agent/memory/items/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
}
