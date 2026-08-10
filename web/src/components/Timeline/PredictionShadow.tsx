import { motion } from "framer-motion";
import type { TimelineTask } from "../../types";
import { spectrumColor, taskSeed, waveformAreaPath } from "./waveMath";

interface PredictionShadowProps {
  task: TimelineTask;
  xStart: number;
  xPlannedEnd: number;
  xPredictedEnd: number;
  baseline: number;
}

export function PredictionShadow({
  task,
  xStart,
  xPlannedEnd,
  xPredictedEnd,
  baseline,
}: PredictionShadowProps) {
  if (xPredictedEnd <= xPlannedEnd + 2 || task.fixed) return null;
  const amplitude = 16 + task.intensity * 42;
  const path = waveformAreaPath(
    xStart,
    xPredictedEnd,
    baseline,
    amplitude,
    taskSeed(task) + 11,
  );
  return (
    <motion.path
      d={path}
      fill={spectrumColor(task.spectrum, 0.1)}
      stroke={spectrumColor(task.spectrum, 0.28)}
      strokeDasharray="4 7"
      strokeWidth={1}
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.7 }}
    />
  );
}
