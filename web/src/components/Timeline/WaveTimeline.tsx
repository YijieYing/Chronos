import {
  type PointerEvent,
  type WheelEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { motion } from "framer-motion";
import type {
  AgentCommand,
  TemporalIntelligence,
  TimelineTask,
  Reminder,
  TimelineSelection,
} from "../../types";
import { CurrentStateCapsule } from "./CurrentStateCapsule";
import { CognitiveLoadTrack } from "./CognitiveLoadTrack";
import { TaskWave } from "./TaskWave";
import { TimeCursor } from "./TimeCursor";
import { TimelineCommand } from "../Agent/TimelineCommand";
import styles from "./Timeline.module.css";
import { ReminderBeacon } from "./ReminderBeacon";

const minute = 60_000;
const basePixelsPerMinute = 1.45;
const height = 610;
const baseline = 294;

interface WaveTimelineProps {
  now: number;
  tasks: TimelineTask[];
  reminders: Reminder[];
  intelligence: TemporalIntelligence;
  commands: AgentCommand[];
  focusTarget: number | null;
  selection: TimelineSelection | null;
  expandedReminderId: string | null;
  onCreateAt: (time: number) => void;
  onSelect: (selection: TimelineSelection | null) => void;
  onEditTask: (task: TimelineTask) => void;
  onResolveCommand: (id: string, accepted: boolean) => void;
}

export function WaveTimeline({
  now,
  tasks,
  reminders,
  intelligence,
  commands,
  focusTarget,
  selection,
  expandedReminderId,
  onCreateAt,
  onSelect,
  onEditTask,
  onResolveCommand,
}: WaveTimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const clickTimer = useRef<number | null>(null);
  const dragState = useRef<
    | { type: "pan"; x: number; center: number }
    | { type: "range"; pointerId: number; x: number; start: number; moved: boolean }
    | null
  >(null);
  const [width, setWidth] = useState(1200);
  const [zoom, setZoom] = useState(1);
  const [centerTime, setCenterTime] = useState(() => now + 2 * 60 * minute);
  const [spacePressed, setSpacePressed] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [rangeDraft, setRangeDraft] = useState<{ start: number; end: number } | null>(null);
  const scale = basePixelsPerMinute * zoom;

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      if (event.code === "Space" && !isTextInput(event.target)) {
        event.preventDefault();
        setSpacePressed(true);
      }
    };
    const up = (event: KeyboardEvent) => {
      if (event.code === "Space") setSpacePressed(false);
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, []);

  useEffect(() => {
    if (focusTarget) setCenterTime(focusTarget);
  }, [focusTarget]);

  const xFor = (time: number) => width / 2 + ((time - centerTime) / minute) * scale;
  const timeForX = (x: number) => centerTime + ((x - width / 2) / scale) * minute;
  const nowX = xFor(now);
  const visibleStart = timeForX(-120);
  const visibleEnd = timeForX(width + 120);

  const ticks = useMemo(() => {
    const interval = zoom > 2.4 ? 15 : zoom > 1.15 ? 30 : zoom > 0.55 ? 60 : 180;
    const step = interval * minute;
    const first = Math.floor(visibleStart / step) * step;
    const values: number[] = [];
    for (let value = first; value <= visibleEnd; value += step) values.push(value);
    return values;
  }, [visibleStart, visibleEnd, zoom]);

  const activeTask = tasks.find((task) => task.start <= now && task.predictedEnd >= now);

  function onWheel(event: WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    if (event.metaKey || event.ctrlKey) {
      const cursorX = event.clientX - rect.left;
      const cursorTime = timeForX(cursorX);
      const nextZoom = clamp(zoom * Math.exp(-event.deltaY * 0.0022), 0.22, 4.8);
      const nextScale = basePixelsPerMinute * nextZoom;
      setCenterTime(cursorTime - ((cursorX - width / 2) / nextScale) * minute);
      setZoom(nextZoom);
    } else {
      setCenterTime((current) => current + ((event.deltaX + event.deltaY) / scale) * minute);
    }
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    if (
      dragState.current
      || !isTimelineFieldTarget(event.target)
      || isInteractiveTimelineTarget(event.target)
    ) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    if (spacePressed) {
      dragState.current = { type: "pan", x: event.clientX, center: centerTime };
      setDragging(true);
      return;
    }
    const rect = event.currentTarget.getBoundingClientRect();
    const start = snap(timeForX(event.clientX - rect.left));
    dragState.current = {
      type: "range",
      pointerId: event.pointerId,
      x: event.clientX,
      start,
      moved: false,
    };
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!dragState.current) return;
    if (
      dragState.current.type === "range"
      && dragState.current.pointerId !== event.pointerId
    ) return;
    const delta = event.clientX - dragState.current.x;
    if (dragState.current.type === "pan") {
      setCenterTime(dragState.current.center - (delta / scale) * minute);
      return;
    }
    if (Math.abs(delta) < 6) return;
    dragState.current.moved = true;
    const rect = event.currentTarget.getBoundingClientRect();
    const end = snap(timeForX(event.clientX - rect.left));
    setRangeDraft({ start: dragState.current.start, end });
  }

  function endDrag(event: PointerEvent<HTMLDivElement>) {
    const ended = dragState.current;
    if (ended?.type === "range" && ended.pointerId !== event.pointerId) return;
    dragState.current = null;
    setDragging(false);
    setRangeDraft(null);
    if (!ended || ended.type === "pan") return;
    if (ended.moved) {
      const rect = event.currentTarget.getBoundingClientRect();
      let end = snap(timeForX(event.clientX - rect.left));
      if (end === ended.start) {
        end += event.clientX >= ended.x ? 15 * minute : -15 * minute;
      }
      onSelect({
        type: "time_range",
        start: Math.min(ended.start, end),
        end: Math.max(ended.start, end),
      });
      return;
    }
    if (clickTimer.current) window.clearTimeout(clickTimer.current);
    clickTimer.current = window.setTimeout(() => onCreateAt(ended.start), 180);
  }

  function cancelGesture() {
    dragState.current = null;
    setDragging(false);
    setRangeDraft(null);
  }

  function returnToNow() {
    if (clickTimer.current) window.clearTimeout(clickTimer.current);
    setCenterTime(now);
    setZoom(1);
  }

  return (
    <section
      ref={containerRef}
      className={`${styles.timelineShell} ${spacePressed ? styles.panReady : ""} ${
        dragging ? styles.dragging : ""
      }`}
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={cancelGesture}
    >
      <div className={styles.timelineReadout}>
        <span>CHRONOS / CONTINUOUS TEMPORAL FIELD</span>
        <code>{Math.round(60 / scale)} min / 60px</code>
      </div>
      <svg
        className={styles.timelineSvg}
        width="100%"
        height={height}
        onDoubleClick={returnToNow}
      >
        <defs>
          <linearGradient id="axis-fade" x1="0" x2="1">
            <stop offset="0%" stopColor="#738078" stopOpacity="0" />
            <stop offset="12%" stopColor="#738078" stopOpacity="0.32" />
            <stop offset="88%" stopColor="#738078" stopOpacity="0.32" />
            <stop offset="100%" stopColor="#738078" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="forecast-fill" x1="0" x2="1">
            <stop offset="0%" stopColor="#82aa98" stopOpacity="0.08" />
            <stop offset="100%" stopColor="#d5a34e" stopOpacity="0.24" />
          </linearGradient>
        </defs>
        <rect width={width} height={height} className={styles.svgBackground} />
        {ticks.map((tick) => {
          const x = xFor(tick);
          const major = new Date(tick).getMinutes() === 0;
          return (
            <g key={tick}>
              <line
                x1={x}
                x2={x}
                y1={104}
                y2={height - 54}
                className={major ? "major-grid" : "minor-grid"}
              />
              <text x={x + 5} y={height - 28} className="time-label">
                {formatTick(tick, major)}
              </text>
            </g>
          );
        })}
        <line x1={0} x2={width} y1={baseline} y2={baseline} stroke="url(#axis-fade)" />
        <AmbientWave width={width} baseline={baseline} />
        {(rangeDraft || selection?.type === "time_range") && (
          <RangeSelection
            selection={rangeDraft ?? selection as Extract<TimelineSelection, { type: "time_range" }>}
            xFor={xFor}
            baseline={baseline}
          />
        )}
        {tasks.map((task, index) => (
          <TaskWave
            key={task.id}
            task={task}
            xStart={xFor(task.start)}
            xEnd={xFor(task.end)}
            xPredictedEnd={xFor(task.predictedEnd)}
            baseline={baseline}
            labelAbove={index % 2 === 0}
            selected={selection?.type === "task" && (
              selection.id === task.id || selection.id === task.seriesId
            )}
            onSelect={(selectedTask) => onSelect({
              type: "task",
              id: selectedTask.seriesId ?? selectedTask.id,
            })}
            onEdit={onEditTask}
          />
        ))}
        <CognitiveLoadTrack
          history={intelligence.history}
          xFor={xFor}
          visibleStart={visibleStart}
          visibleEnd={visibleEnd}
          baseline={height - 92}
        />
        <ForecastTrace
          forecast={intelligence.forecast}
          xFor={xFor}
          baseline={height - 92}
          width={width}
        />
        <TimeCursor x={nowX} height={height} />
      </svg>
      {reminders
        .filter((reminder) => reminder.status === "pending" || reminder.status === "delivered")
        .map((reminder) => (
          <ReminderBeacon
            key={reminder.id}
            reminder={reminder}
            now={now}
            xFor={xFor}
            selected={selection?.type === "reminder" && selection.id === reminder.id}
            expanded={expandedReminderId === reminder.id}
            onSelect={(selectedReminder) => onSelect({
              type: "reminder",
              id: selectedReminder.id,
            })}
          />
        ))}
      <CurrentStateCapsule
        intelligence={intelligence}
        activeTask={activeTask}
      />
      {commands
        .filter((command) => command.cursorTime >= visibleStart && command.cursorTime <= visibleEnd)
        .map((command) => (
          <TimelineCommand
            key={command.id}
            command={command}
            x={xFor(command.cursorTime)}
            onResolve={onResolveCommand}
          />
        ))}
      <div className={styles.timelineLegend}>
        <span><i className={styles.plannedLegend} /> Planned</span>
        <span><i className={styles.predictedLegend} /> Predicted extension</span>
        <span><kbd>⌘</kbd> + wheel zoom</span>
        <span><kbd>Space</kbd> + drag pan</span>
        <span>Double-click → NOW</span>
      </div>
    </section>
  );
}

function RangeSelection({
  selection,
  xFor,
  baseline,
}: {
  selection: { start: number; end: number };
  xFor: (time: number) => number;
  baseline: number;
}) {
  const start = Math.min(selection.start, selection.end);
  const end = Math.max(selection.start, selection.end);
  const xStart = xFor(start);
  const xEnd = xFor(end);
  return <g pointerEvents="none">
    <rect
      x={xStart}
      y={baseline - 92}
      width={Math.max(1, xEnd - xStart)}
      height={184}
      rx={9}
      className={styles.selectionRange}
    />
    <line x1={xStart} x2={xStart} y1={baseline - 102} y2={baseline + 102} className={styles.selectionBoundary} />
    <line x1={xEnd} x2={xEnd} y1={baseline - 102} y2={baseline + 102} className={styles.selectionBoundary} />
    <text x={(xStart + xEnd) / 2} y={baseline - 109} textAnchor="middle" className="selection-label">
      {formatTick(start, false)}–{formatTick(end, false)} · {Math.round((end - start) / minute)} MIN
    </text>
  </g>;
}

function isInteractiveTimelineTarget(target: EventTarget | null) {
  return target instanceof Element && Boolean(
    target.closest("[data-timeline-object], button, input, textarea, [role='dialog']"),
  );
}

function isTimelineFieldTarget(target: EventTarget | null) {
  return target instanceof Element && Boolean(target.closest("svg"));
}

function AmbientWave({ width, baseline }: { width: number; baseline: number }) {
  const points = Array.from({ length: 180 }, (_, index) => {
    const x = (index / 179) * width;
    const y =
      baseline +
      Math.sin(index * 0.79) * 2.2 +
      Math.sin(index * 2.31) * 1.1 +
      Math.cos(index * 0.17) * 1.6;
    return `${x},${y}`;
  }).join(" ");
  return (
    <motion.polyline
      points={points}
      fill="none"
      stroke="#6f7e75"
      strokeOpacity={0.25}
      strokeWidth={0.8}
      animate={{ opacity: [0.28, 0.48, 0.28] }}
      transition={{ duration: 5, repeat: Infinity }}
    />
  );
}

function ForecastTrace({
  forecast,
  xFor,
  baseline,
  width,
}: {
  forecast: TemporalIntelligence["forecast"];
  xFor: (time: number) => number;
  baseline: number;
  width: number;
}) {
  if (!forecast.length) return null;
  const points = forecast.map((point) => [
    xFor(point.time),
    baseline - point.cognitiveLoad * 54,
  ]);
  const line = points.map(([x, y], index) => `${index ? "L" : "M"} ${x} ${y}`).join(" ");
  const area = `${line} L ${points.at(-1)![0]} ${baseline} L ${points[0][0]} ${baseline} Z`;
  return (
    <g>
      <path d={area} fill="url(#forecast-fill)" />
      <path d={line} fill="none" stroke="#87a998" strokeWidth={1.2} strokeDasharray="3 5" />
      <text
        x={width - 16}
        y={baseline + 18}
        textAnchor="end"
        className="forecast-label"
      >
        COGNITIVE LOAD / 6H FORECAST
      </text>
      {points.map(([x, y], index) => (
        <circle key={forecast[index].time} cx={x} cy={y} r={2.2} fill="#789b89" />
      ))}
    </g>
  );
}

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));
const snap = (time: number) => Math.round(time / (15 * minute)) * 15 * minute;
const formatTick = (time: number, major: boolean) =>
  new Intl.DateTimeFormat("en-GB", {
    ...(major ? { weekday: "short" } : {}),
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(time);
const isTextInput = (target: EventTarget | null) =>
  target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
