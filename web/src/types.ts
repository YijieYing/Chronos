export type TaskType =
  | "creative"
  | "coding"
  | "research"
  | "communication"
  | "execution"
  | "meeting"
  | "recovery";

export type RecurrenceRule =
  | { frequency: "daily"; until?: string }
  | { frequency: "weekly"; weekdays: number[]; until?: string };

export interface TimelineTask {
  id: string;
  title: string;
  start: number;
  end: number;
  predictedEnd: number;
  intensity: number;
  spectrum: number;
  fixed: boolean;
  type: TaskType;
  source: "user" | "agent" | "schedule";
  recurrence?: RecurrenceRule;
  seriesId?: string;
  seriesStart?: number;
  scheduled?: boolean;
  unscheduledReason?: string;
}

export interface MonitorSample {
  time: number;
  state: "working" | "not_working";
  taskType: TaskType;
  focus: number;
  switching: number;
  intensity: number;
  activityConfidence: number;
}

export interface FatiguePoint {
  time: number;
  cognitiveLoad: number;
  mentalFatigue: number;
}

export interface CognitiveStatePoint {
  time: number;
  cognitiveLoad: number;
  mentalFatigue: number;
  focus: number;
  taskType?: TaskType;
  taskConfidence: number;
  recoveryState: "working" | "recovering" | "rested" | "unknown";
  source: "observed" | "predicted";
}

export interface TemporalIntelligence {
  currentActivity: TaskType;
  cognitiveState: "deep_work" | "engaged" | "fragmented" | "recovery";
  focus: number;
  stateConfidence: number;
  efficiency: number;
  estimatedDelay: number;
  predictedFinish: number;
  health: "Healthy" | "Elevated load" | "Recovery advised";
  history: CognitiveStatePoint[];
  forecast: FatiguePoint[];
}

export interface AgentCommand {
  id: string;
  cursorTime: number;
  title: string;
  lines: string[];
  status: "proposed" | "accepted" | "rejected";
  proposedTask?: TimelineTask;
  contextUsed?: string[];
  canResolve?: boolean;
}

export interface ChronosLogEntry {
  id: string;
  time: number;
  request: string;
  response: string;
  status: "proposed" | "applied" | "rejected" | "restored" | "info";
  addedTaskId?: string;
  changedTaskId?: string;
  previousTask?: TimelineTask;
  deletedTask?: TimelineTask;
  proposalId?: string;
  contextUsed?: string[];
}

export interface NewTaskInput {
  title: string;
  start: number;
  durationMinutes: number;
  intensity: number;
  spectrum: number;
  fixed: boolean;
  type: TaskType;
  recurrence?: RecurrenceRule;
}
