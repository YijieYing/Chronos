import type {
  CognitiveStatePoint,
  FatiguePoint,
  MonitorSample,
  TimelineTask,
} from "../types";

const hour = 3_600_000;
const fiveMinutes = 300_000;

export function aggregateCognitiveHistory(
  samples: MonitorSample[],
  now = Date.now(),
): CognitiveStatePoint[] {
  const start = now - 24 * hour;
  const buckets = new Map<number, MonitorSample[]>();

  for (const sample of samples) {
    if (sample.time < start || sample.time > now) continue;
    const bucket = Math.floor(sample.time / fiveMinutes) * fiveMinutes;
    buckets.set(bucket, [...(buckets.get(bucket) ?? []), sample]);
  }

  let fatigue = 0.12;
  let continuousWorkMinutes = 0;
  let recoveryMinutes = 0;
  let switchingPressure = 0;
  let previousTime: number | undefined;
  return [...buckets.entries()]
    .sort(([left], [right]) => left - right)
    .map(([time, bucket]) => {
      const gapMinutes = previousTime === undefined ? 5 : (time - previousTime) / 60_000;
      previousTime = time;
      if (gapMinutes > 10) {
        continuousWorkMinutes = 0;
        switchingPressure *= 0.35;
        // Missing observations are not assumed to be full recovery.
        fatigue = clamp(fatigue - Math.min(gapMinutes, 30) * 0.0015);
      }

      const working = bucket.filter((sample) => sample.state === "working");
      const focus = mean(bucket.map((sample) => sample.focus));
      const intensity = mean(bucket.map((sample) => sample.intensity));
      const switching = mean(bucket.map((sample) => sample.switching));
      const activityConfidence = mean(
        bucket.map((sample) => sample.activityConfidence),
      );
      const isWorking = working.length > bucket.length / 2;

      if (isWorking) {
        continuousWorkMinutes += Math.min(5, gapMinutes);
        recoveryMinutes = 0;
      } else {
        recoveryMinutes += Math.min(5, gapMinutes);
        continuousWorkMinutes = Math.max(0, continuousWorkMinutes - 10);
      }

      const continuousPressure = smoothstep(20, 120, continuousWorkMinutes);
      switchingPressure =
        switchingPressure * 0.72 + clamp(switching / 3) * 0.28;

      if (isWorking) {
        fatigue = clamp(
          fatigue +
            0.004 +
            intensity * 0.009 +
            continuousPressure * 0.006,
        );
      } else {
        const recoveryRate =
          0.014 + smoothstep(5, 45, recoveryMinutes) * 0.016;
        fatigue = clamp(fatigue - recoveryRate * activityConfidence);
      }

      const recoveryEffect = isWorking
        ? 0
        : 0.3 + smoothstep(5, 30, recoveryMinutes) * 0.25;
      const rawLoad =
        intensity * 0.52 +
        continuousPressure * 0.18 +
        switchingPressure * 0.15 +
        fatigue * 0.15 -
        recoveryEffect;
      const cognitiveLoad = isWorking
        ? clamp(rawLoad)
        : clamp(rawLoad, 0.04, 0.26);
      const latest = bucket.at(-1)!;
      const confidence = clamp(
        activityConfidence * 0.7 +
          (gapMinutes <= 10 ? 0.2 : 0.05) +
          Math.min(bucket.length, 2) * 0.05,
        0.2,
        0.96,
      );

      return {
        time,
        cognitiveLoad,
        mentalFatigue: fatigue,
        focus,
        taskType: isWorking ? latest.taskType : undefined,
        taskConfidence: isWorking
          ? clamp(confidence - switching * 0.08, 0.25, 0.95)
          : confidence,
        recoveryState: isWorking
          ? "working" as const
          : recoveryMinutes >= 25 && fatigue < 0.25
            ? "rested" as const
            : "recovering" as const,
        source: "observed" as const,
      };
    });
}

export function forecastCognitiveLoad(
  states: CognitiveStatePoint[],
  tasks: TimelineTask[],
  now = Date.now(),
  horizonHours = 6,
): FatiguePoint[] {
  const recent = states.slice(-8);
  const meanFocus =
    recent.reduce((total, state) => total + state.focus, 0) / Math.max(1, recent.length);
  const latestFatigue = recent.at(-1)?.mentalFatigue ?? 0.18;
  const continuousMinutes =
    recent.filter((state) => state.recoveryState === "working").length * 5;
  const baseFatigue = clamp(
    latestFatigue * 0.7 + (1 - meanFocus) * 0.18 + continuousMinutes / 600,
    0.08,
    0.78,
  );

  return Array.from({ length: horizonHours + 1 }, (_, index) => {
    const time = now + index * hour;
    const nearby = tasks.find((task) => task.start <= time && task.predictedEnd >= time);
    const intensity = nearby?.intensity ?? 0.24;
    const recovery = nearby?.type === "recovery" ? 0.22 : 0;
    const mentalFatigue = clamp(baseFatigue + index * 0.075 + intensity * 0.18 - recovery, 0, 1);
    const cognitiveLoad = clamp(intensity * 0.62 + mentalFatigue * 0.48, 0, 1);
    return { time, cognitiveLoad, mentalFatigue };
  });
}

export function cognitiveLoadAt(forecast: FatiguePoint[], time: number): number {
  if (!forecast.length) return 0;
  const closest = forecast.reduce((best, point) =>
    Math.abs(point.time - time) < Math.abs(best.time - time) ? point : best,
  );
  return closest.cognitiveLoad;
}

export const clamp = (value: number, min = 0, max = 1) =>
  Math.min(max, Math.max(min, value));

const mean = (values: number[]) =>
  values.reduce((total, value) => total + value, 0) / Math.max(1, values.length);

function smoothstep(edge0: number, edge1: number, value: number) {
  const progress = clamp((value - edge0) / (edge1 - edge0));
  return progress * progress * (3 - 2 * progress);
}
