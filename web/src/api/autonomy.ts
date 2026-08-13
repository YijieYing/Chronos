import { apiRequest } from "./client";

export interface AutonomyPolicyPayload {
  level: 0 | 1 | 2 | 3;
  label: string;
  max_risk: number;
  max_ambiguity: number;
  max_impact: number;
  require_reversible: boolean;
}

export function loadAutonomyPolicy() {
  return apiRequest<AutonomyPolicyPayload>("/api/v1/agent/autonomy");
}

export function updateAutonomyPolicy(level: number) {
  return apiRequest<AutonomyPolicyPayload>("/api/v1/agent/autonomy", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ level }),
  });
}
