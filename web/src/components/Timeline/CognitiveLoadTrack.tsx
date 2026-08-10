import { motion } from "framer-motion";
import type { CognitiveStatePoint } from "../../types";
import { cognitiveLoadColor } from "./cognitiveLoadColor";

interface CognitiveLoadTrackProps {
  history: CognitiveStatePoint[];
  xFor: (time: number) => number;
  visibleStart: number;
  visibleEnd: number;
  baseline: number;
}

const amplitude = 54;

export function CognitiveLoadTrack({
  history,
  xFor,
  visibleStart,
  visibleEnd,
  baseline,
}: CognitiveLoadTrackProps) {
  const margin = 10 * 60_000;
  const visible = history.filter(
    (point) => point.time >= visibleStart - margin && point.time <= visibleEnd + margin,
  );
  const coordinates = visible.map((point) => ({
    ...point,
    x: xFor(point.time),
    y: baseline - point.cognitiveLoad * amplitude,
  }));
  const area = coordinates.length >= 2
    ? [
        `M ${coordinates[0].x} ${baseline}`,
        ...coordinates.map((point) => `L ${point.x} ${point.y}`),
        `L ${coordinates.at(-1)!.x} ${baseline}`,
        "Z",
      ].join(" ")
    : null;
  return (
    <g aria-label="Cognitive load record for the past 24 hours">
      {coordinates.length >= 2 && [0, 0.5, 1].map((value) => {
        const y = baseline - value * amplitude;
        return (
          <g key={value}>
            <line
              x1={coordinates[0].x}
              x2={coordinates.at(-1)!.x}
              y1={y}
              y2={y}
              stroke="#809087"
              strokeOpacity={value === 0 ? 0.18 : 0.08}
              strokeDasharray={value === 0 ? undefined : "2 7"}
            />
            <text x={coordinates[0].x + 3} y={y - 4} className="load-scale">
              {Math.round(value * 100)}
            </text>
          </g>
        );
      })}
      {area && (
        <motion.path
          d={area}
          fill="rgba(102, 154, 126, 0.07)"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
        />
      )}
      {coordinates.slice(1).map((point, index) => {
        const previous = coordinates[index];
        const load = (previous.cognitiveLoad + point.cognitiveLoad) / 2;
        return (
          <line
            key={point.time}
            x1={previous.x}
            y1={previous.y}
            x2={point.x}
            y2={point.y}
            stroke={cognitiveLoadColor(load)}
            strokeWidth={1.7}
            strokeLinecap="round"
          />
        );
      })}
      <text x={16} y={baseline + 18} className="record-label">
        COGNITIVE LOAD / 24H RECORD
      </text>
    </g>
  );
}
