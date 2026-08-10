import type { MonitorSample } from "../types";

export function createMockMonitorData(now = Date.now()): MonitorSample[] {
  const minute = 60_000;
  const focuses = [0.76, 0.81, 0.85, 0.79, 0.84, 0.82, 0.78, 0.8, 0.82];
  return focuses.map((focus, index) => ({
    time: now - (focuses.length - index - 1) * 15 * minute,
    state: "working",
    taskType: "coding",
    focus,
    switching: index === 3 || index === 6 ? 2 : index % 3 === 0 ? 1 : 0,
    intensity: 0.9,
    activityConfidence: index === 3 || index === 6 ? 0.72 : 0.88,
  }));
}

export function createMockMonitorHistory(now = Date.now()): MonitorSample[] {
  const minute = 60_000;
  const points = 24 * 12;

  return Array.from({ length: points + 1 }, (_, index) => {
    const time = now - (points - index) * 5 * minute;
    const ageHours = (now - time) / 3_600_000;
    const phase = workPhase(ageHours);
    const working = phase.taskType !== "recovery";
    const pulse = Math.sin(index * 0.31) * 0.045 + Math.sin(index * 0.071) * 0.035;
    const focus = working
      ? Math.max(0.5, Math.min(0.91, phase.focus + pulse))
      : 0.18;

    return {
      time,
      state: working ? "working" as const : "not_working" as const,
      taskType: phase.taskType,
      focus,
      switching: working && index % 19 === 0 ? 2 : working && index % 7 === 0 ? 1 : 0,
      intensity: working ? phase.intensity : 0.08,
      activityConfidence: working ? 0.84 : 0.9,
    };
  });
}

function workPhase(ageHours: number) {
  if (ageHours > 17 || (ageHours > 12 && ageHours < 13) || (ageHours > 6 && ageHours < 8)) {
    return { taskType: "recovery" as const, focus: 0.18, intensity: 0.08 };
  }
  if (ageHours > 13) {
    return { taskType: "creative" as const, focus: 0.77, intensity: 0.76 };
  }
  if (ageHours > 8) {
    return { taskType: "execution" as const, focus: 0.69, intensity: 0.46 };
  }
  if (ageHours > 3) {
    return { taskType: "research" as const, focus: 0.74, intensity: 0.68 };
  }
  return { taskType: "coding" as const, focus: 0.82, intensity: 0.9 };
}
