import { motion } from "framer-motion";

interface TimeCursorProps {
  x: number;
  height: number;
}

export function TimeCursor({ x, height }: TimeCursorProps) {
  return (
    <g>
      <line x1={x} x2={x} y1={86} y2={height - 44} className="now-line" />
      <motion.circle
        cx={x}
        cy={284}
        r={5}
        className="now-dot"
        animate={{ r: [4, 7, 4], opacity: [1, 0.45, 1] }}
        transition={{ duration: 2.4, repeat: Infinity }}
      />
      <text x={x + 9} y={height - 28} className="now-label">
        NOW
      </text>
    </g>
  );
}
