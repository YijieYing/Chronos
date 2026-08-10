import { useEffect, useState } from "react";
import type { CognitiveStatePoint, TaskType } from "../types";

type MonitorMode = "live" | "history" | "demo";

interface LiveMonitorState {
  mode: MonitorMode;
  points: CognitiveStatePoint[];
}

interface ApiPoint {
  time: number;
  cognitive_load: number;
  mental_fatigue: number;
  focus: number;
  task_type?: string | null;
  task_confidence: number;
  recovery_state: CognitiveStatePoint["recoveryState"];
  source: CognitiveStatePoint["source"];
}

export function useLiveMonitor(): LiveMonitorState {
  const [state, setState] = useState<LiveMonitorState>({
    mode: "demo",
    points: [],
  });

  useEffect(() => {
    let cancelled = false;

    async function refresh() {
      const now = Date.now();
      try {
        const [currentResponse, historyResponse] = await Promise.all([
          fetch("/api/current-state", { cache: "no-store" }),
          fetch(
            `/api/cognitive-state?from=${now - 24 * 3_600_000}&to=${now}`,
            { cache: "no-store" },
          ),
        ]);
        if (!currentResponse.ok || !historyResponse.ok) throw new Error("Monitor API unavailable");
        const current = await currentResponse.json();
        const history = await historyResponse.json();
        if (cancelled) return;
        const points = (history.points as ApiPoint[]).map(fromApiPoint);
        const live = current.status === "live" && current.point;
        setState({
          mode: live ? "live" : points.length ? "history" : "demo",
          points,
        });
      } catch {
        if (!cancelled) setState({ mode: "demo", points: [] });
      }
    }

    void refresh();
    const timer = window.setInterval(refresh, 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return state;
}

function fromApiPoint(point: ApiPoint): CognitiveStatePoint {
  return {
    time: point.time,
    cognitiveLoad: point.cognitive_load,
    mentalFatigue: point.mental_fatigue,
    focus: point.focus,
    taskType: mapTaskType(point.task_type),
    taskConfidence: point.task_confidence,
    recoveryState: point.recovery_state,
    source: point.source,
  };
}

function mapTaskType(value?: string | null): TaskType | undefined {
  return {
    coding: "coding",
    writing: "creative",
    planning: "creative",
    researching: "research",
    communicating: "communication",
    meeting: "meeting",
    entertainment: "recovery",
  }[value ?? ""] as TaskType | undefined;
}
