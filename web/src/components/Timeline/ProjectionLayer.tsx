import type { TimelineProjection } from "../../types";
import styles from "./Timeline.module.css";

interface ProjectionLayerProps {
  projections: TimelineProjection[];
  xFor: (time: number) => number;
  baseline: number;
}

export function ProjectionLayer({
  projections,
  xFor,
  baseline,
}: ProjectionLayerProps) {
  return <g className={styles.projectionLayer} pointerEvents="none">
    {projections.map((projection, index) => (
      <ProjectionMark
        key={projection.id}
        projection={projection}
        xFor={xFor}
        baseline={baseline}
        lane={index % 3}
      />
    ))}
  </g>;
}

function ProjectionMark({
  projection,
  xFor,
  baseline,
  lane,
}: {
  projection: TimelineProjection;
  xFor: (time: number) => number;
  baseline: number;
  lane: number;
}) {
  const point = projectionPoint(projection);
  if (!point) return null;
  const [start, end] = point;
  const xStart = xFor(start);
  const xEnd = xFor(end);
  const width = Math.max(10, xEnd - xStart);
  const y = baseline - 68 - lane * 18;
  const incomplete = projection.visualState === "incomplete";
  const objectType = metadataString(projection, "object_type");
  const title = metadataString(projection, "title")
    ?? `${projection.target.type} projection`;
  const operation = metadataString(projection, "operation");
  const prefix = incomplete ? "UNRESOLVED" : operation === "delete" ? "REMOVE" : "PROPOSED";

  if (objectType === "reminder" || projection.target.type === "reminder") {
    const anchor = projection.start === undefined
      ? start
      : projection.start === projection.end
        ? projection.start
        : (start + end) / 2;
    const anchorX = xFor(anchor);
    return <g data-visual-state={projection.visualState}>
      {start !== end && (
        <path
          d={`M ${xStart} ${baseline + 18} V ${baseline + 29} H ${xEnd} V ${baseline + 18}`}
          className={incomplete ? styles.incompleteProjection : styles.proposedProjection}
        />
      )}
      <rect
        x={anchorX - 5}
        y={baseline - 5}
        width={10}
        height={10}
        transform={`rotate(45 ${anchorX} ${baseline})`}
        className={incomplete ? styles.incompleteProjection : styles.proposedProjection}
      />
      <text x={anchorX + 10} y={baseline + 25} className="projection-label">
        {prefix} · {title}
      </text>
    </g>;
  }

  return <g data-visual-state={projection.visualState}>
    <rect
      x={xStart}
      y={y}
      width={width}
      height={Math.max(34, 52 - lane * 4)}
      rx={8}
      className={incomplete ? styles.incompleteProjection : styles.proposedProjection}
    />
    <line
      x1={xStart}
      x2={xEnd}
      y1={baseline}
      y2={baseline}
      className={incomplete ? styles.incompleteProjection : styles.proposedProjection}
    />
    <text x={xStart + 8} y={y - 7} className="projection-label">
      {prefix} · {title}
    </text>
  </g>;
}

function projectionPoint(projection: TimelineProjection): [number, number] | null {
  if (projection.start !== undefined && projection.end !== undefined) {
    return [projection.start, projection.end];
  }
  if (projection.target.type === "time_range") {
    return [projection.target.start, projection.target.end];
  }
  const at = projection.metadata.at;
  return typeof at === "number" ? [at, at] : null;
}

function metadataString(projection: TimelineProjection, key: string) {
  const value = projection.metadata[key];
  return typeof value === "string" ? value : undefined;
}
