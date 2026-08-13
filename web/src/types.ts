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

export interface Reminder {
  id: string;
  title: string;
  trigger:
    | { type: "time"; at: number }
    | { type: "window"; start: number; end: number };
  delivery: "exact" | "context-aware";
  priority: number;
  status: "pending" | "delivered" | "done" | "dismissed";
  source: "user" | "agent";
  createdAt: number;
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

export type TimelineReference =
  | { type: "task"; id: string; start?: never; end?: never }
  | { type: "reminder"; id: string; start?: never; end?: never }
  | { type: "time_range"; start: number; end: number; id?: never };

export type TimelineSelection = TimelineReference;

export type ChronosLogEventType =
  | "user_prompt" | "agent_message" | "operation_created"
  | "clarification_requested" | "clarification_answered"
  | "proposal_created" | "proposal_updated" | "operation_approved"
  | "operation_executed" | "operation_completed" | "operation_rejected"
  | "operation_failed" | "manual_task_move" | "manual_task_resize"
  | "manual_reminder_move" | "undo" | "restore";

export interface ChronosLogEntry {
  id: string;
  time: number;
  eventType: ChronosLogEventType;
  message: string;
  operationId?: string;
  references: TimelineReference[];
  metadata: Record<string, unknown>;
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
