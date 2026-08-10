import {
  aggregateCognitiveHistory,
  cognitiveLoadAt,
  clamp,
  forecastCognitiveLoad,
} from "./CognitiveState";
import type {
  CognitiveStatePoint,
  MonitorSample,
  TemporalIntelligence,
  TimelineTask,
} from "../types";

const minute = 60_000;

export function adaptMonitorData(
  samples: MonitorSample[],
  tasks: TimelineTask[],
  now = Date.now(),
  historySamples = samples,
): { intelligence: TemporalIntelligence; predictedTasks: TimelineTask[] } {
  const history = aggregateCognitiveHistory(historySamples, now);
  return adaptCognitiveStateData(history, tasks, now);
}

export function adaptCognitiveStateData(
  history: CognitiveStatePoint[],
  tasks: TimelineTask[],
  now = Date.now(),
): { intelligence: TemporalIntelligence; predictedTasks: TimelineTask[] } {
  const latest = history.at(-1);
  const stale = !latest || now - latest.time > 10 * minute;
  const activeTask =
    tasks.find((task) => task.start <= now && task.predictedEnd >= now) ??
    tasks.find((task) => task.start > now);
  const efficiency = latest && !stale
    ? clamp(latest.focus, 0.2, 1)
    : 0.5;
  const forecast = forecastCognitiveLoad(history, tasks, now);

  const predictedTasks = tasks.map((task) => {
    if (task.fixed || task.end <= now) return task;
    const plannedMinutes = (task.end - task.start) / minute;
    const load = cognitiveLoadAt(forecast, task.start);
    const efficiencyPenalty = Math.max(0, 0.82 - efficiency) * 0.65;
    const multiplier = 1 + load * 0.34 + efficiencyPenalty;
    return {
      ...task,
      predictedEnd: task.start + plannedMinutes * multiplier * minute,
    };
  });

  const predictedActive =
    predictedTasks.find((task) => task.start <= now && task.predictedEnd >= now) ??
    predictedTasks.find((task) => task.start > now);
  const estimatedDelay = predictedActive
    ? Math.max(0, Math.round((predictedActive.predictedEnd - predictedActive.end) / minute))
    : 0;
  const currentLoad = cognitiveLoadAt(forecast, now);

  return {
    predictedTasks,
    intelligence: {
      currentActivity: !stale
        ? latest?.taskType ?? activeTask?.type ?? "recovery"
        : activeTask?.type ?? "recovery",
      cognitiveState:
        stale || latest?.recoveryState !== "working"
          ? "recovery"
          : efficiency > 0.76 && latest.taskConfidence > 0.7
            ? "deep_work"
            : latest.taskConfidence < 0.5
              ? "fragmented"
              : "engaged",
      focus: !stale ? latest?.focus ?? 0 : 0,
      stateConfidence: latest && !stale
        ? latest.taskConfidence
        : 0.35,
      efficiency,
      estimatedDelay,
      predictedFinish: predictedActive?.predictedEnd ?? now,
      health:
        currentLoad < 0.58
          ? "Healthy"
          : currentLoad < 0.78
            ? "Elevated load"
            : "Recovery advised",
      history,
      forecast,
    },
  };
}
