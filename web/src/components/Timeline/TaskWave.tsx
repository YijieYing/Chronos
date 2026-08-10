import { motion } from "framer-motion";
import type { TimelineTask } from "../../types";
import { PredictionShadow } from "./PredictionShadow";
import {
  fixedWavePath,
  spectrumColor,
  taskSeed,
  waveformAreaPath,
} from "./waveMath";

interface TaskWaveProps {
  task: TimelineTask;
  xStart: number;
  xEnd: number;
  xPredictedEnd: number;
  baseline: number;
  labelAbove: boolean;
  onEdit: (task: TimelineTask) => void;
}

export function TaskWave({
  task,
  xStart,
  xEnd,
  xPredictedEnd,
  baseline,
  labelAbove,
  onEdit,
}: TaskWaveProps) {
  const amplitude = 16 + task.intensity * 42;
  const width = xEnd - xStart;
  if (width < 3) return null;
  const color = spectrumColor(task.spectrum);
  const fillPath = waveformAreaPath(
    xStart,
    xEnd,
    baseline,
    amplitude,
    taskSeed(task),
  );
  const squarePath = fixedWavePath(xStart, xEnd, baseline, amplitude);
  const labelY = labelAbove ? baseline - amplitude - 46 : baseline + amplitude + 28;

  return (
    <g
      role="graphics-symbol"
      aria-label={`${task.title}, ${formatTime(task.start)} to ${formatTime(task.end)}`}
      onClick={(event) => {
        event.stopPropagation();
        onEdit(task);
      }}
      opacity={task.scheduled === false ? 0.5 : 1}
    >
      <PredictionShadow
        task={task}
        xStart={xStart}
        xPlannedEnd={xEnd}
        xPredictedEnd={xPredictedEnd}
        baseline={baseline}
      />
      {task.fixed ? (
        <motion.path
          d={squarePath}
          fill={spectrumColor(task.spectrum, 0.32)}
          stroke={color}
          strokeWidth={1.3}
          strokeDasharray={task.scheduled === false ? "4 4" : undefined}
          strokeLinejoin="miter"
          initial={{ pathLength: 0, opacity: 0 }}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={{ duration: 0.65 }}
        />
      ) : (
        <motion.path
          d={fillPath}
          fill={spectrumColor(task.spectrum, 0.32)}
          stroke={color}
          strokeWidth={1.3}
          strokeDasharray={task.scheduled === false ? "4 4" : undefined}
          initial={{ opacity: 0, scaleY: 0.55 }}
          animate={{
            opacity: 1,
            scaleY: [0.94, 1.03, 0.98],
          }}
          transition={{
            opacity: { duration: 0.45 },
            scaleY: { duration: 4 + task.intensity * 2, repeat: Infinity, repeatType: "mirror" },
          }}
          style={{ transformOrigin: `${(xStart + xEnd) / 2}px ${baseline}px` }}
        />
      )}
      {!task.fixed && (
        <line
          x1={xStart}
          x2={xStart}
          y1={baseline - amplitude - 5}
          y2={baseline + amplitude + 5}
          stroke={spectrumColor(task.spectrum, 0.42)}
          strokeWidth={1}
        />
      )}
      <g transform={`translate(${xStart + 8} ${labelY})`}>
        <text className="task-kicker">
          {task.scheduled === false ? "UNSCHEDULED / " : ""}
          {task.fixed ? "FIXED / " : ""}
          {task.recurrence ? "RECURRING / " : ""}
          {task.type.toUpperCase()}
        </text>
        <text y={18} className="task-title">
          {truncate(task.title, Math.max(12, Math.floor(width / 7)))}
        </text>
        <text y={35} className="task-meta">
          {formatTime(task.start)}—{formatTime(task.end)}
          {task.predictedEnd > task.end + 60_000
            ? `  /  predicted ${formatTime(task.predictedEnd)}`
            : ""}
        </text>
      </g>
    </g>
  );
}

const formatTime = (value: number) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(value);

const truncate = (value: string, max: number) =>
  value.length > max ? `${value.slice(0, Math.max(8, max - 1))}…` : value;
