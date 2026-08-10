import {
  type MouseEvent,
  type PointerEvent,
  useMemo,
  useRef,
  useState,
} from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { ChronosLogEntry, TimelineTask } from "../../types";
import { spectrumColor } from "../Timeline/waveMath";
import styles from "./OverviewMap.module.css";

interface OverviewMapProps {
  open: boolean;
  tasks: TimelineTask[];
  logs: ChronosLogEntry[];
  onClose: () => void;
  onOpenLog: () => void;
  onSelectTask: (time: number) => void;
  onCreateAt: (time: number) => void;
  onUpdateTask: (id: string, start: number, end: number) => void;
}

const minute = 60_000;
const dayDuration = 24 * 60 * minute;

export function OverviewMap({
  open,
  tasks,
  logs,
  onClose,
  onOpenLog,
  onSelectTask,
  onCreateAt,
  onUpdateTask,
}: OverviewMapProps) {
  const week = useMemo(() => weekDays(Date.now()), []);

  function selectTask(time: number) {
    onSelectTask(time);
    onClose();
  }

  function createAt(time: number) {
    onClose();
    onCreateAt(time);
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.section
          className={styles.overview}
          initial={{ opacity: 0, scale: 0.985 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.99 }}
          transition={{ duration: 0.22 }}
        >
          <header className={styles.header}>
            <div>
              <span className={styles.eyebrow}>PLANNING MODE / OVERVIEW MAP</span>
              <h2>This Week</h2>
              <p>未来如何安排？一天是一个时间区域，不是一列日历。</p>
            </div>
            <div className={styles.headerActions}>
              <button onClick={onOpenLog}>
                ADJUSTMENT RECORD
                {logs.length > 0 && <b>{logs.length}</b>}
              </button>
              <button className={styles.closeButton} onClick={onClose}>×</button>
            </div>
          </header>

          <div className={styles.weekField}>
            {week.map((day) => {
              const dayTasks = tasks
                .filter((task) => task.start >= day.start && task.start < day.end)
                .sort((left, right) => left.start - right.start);
              return (
                <DayRegion
                  key={day.start}
                  start={day.start}
                  end={day.end}
                  tasks={dayTasks}
                  today={isSameDay(day.start, Date.now())}
                  onSelectTask={selectTask}
                  onCreateAt={createAt}
                  onUpdateTask={onUpdateTask}
                />
              );
            })}
          </div>

          <footer className={styles.footer}>
            <span><i className={styles.flexibleKey} /> FLEXIBLE / AI MOVABLE</span>
            <span><i className={styles.fixedKey} /> FIXED / CONSTRAINED</span>
            <span>Drag task → move</span>
            <span>Drag right edge → duration</span>
          </footer>
        </motion.section>
      )}
    </AnimatePresence>
  );
}

interface DayRegionProps {
  start: number;
  end: number;
  tasks: TimelineTask[];
  today: boolean;
  onSelectTask: (time: number) => void;
  onCreateAt: (time: number) => void;
  onUpdateTask: (id: string, start: number, end: number) => void;
}

function DayRegion({
  start,
  end,
  tasks,
  today,
  onSelectTask,
  onCreateAt,
  onUpdateTask,
}: DayRegionProps) {
  const plannedMinutes = tasks.reduce(
    (total, task) => total + (task.end - task.start) / minute,
    0,
  );
  const projectedLoad = Math.min(
    1,
    tasks.reduce(
      (total, task) => total + ((task.end - task.start) / 3_600_000) * task.intensity,
      0,
    ) / 8,
  );

  function createFromStrip(event: MouseEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const progress = (event.clientX - rect.left) / rect.width;
    const time = snap(start + progress * dayDuration);
    onCreateAt(Math.max(start, Math.min(end - 15 * minute, time)));
  }

  return (
    <article className={styles.dayRegion} data-today={today}>
      <div className={styles.dayHeader}>
        <div>
          <span>{today ? "TODAY" : formatWeekday(start)}</span>
          <strong>{formatDate(start)}</strong>
        </div>
        <code>{formatDuration(plannedMinutes)} planned</code>
      </div>

      <div className={styles.loadSummary}>
        <span>PROJECTED COGNITIVE LOAD</span>
        <b>{loadLabel(projectedLoad)}</b>
        <div><i style={{ width: `${Math.max(4, projectedLoad * 100)}%` }} /></div>
      </div>

      <div className={styles.dayStrip} onClick={createFromStrip}>
        <span className={styles.dawn}>00</span>
        <span className={styles.noon}>12</span>
        <span className={styles.night}>24</span>
        {tasks.map((task) => (
          <WeekTaskBlock
            key={task.id}
            task={task}
            dayStart={start}
            dayEnd={end}
            onSelect={() => onSelectTask(task.start)}
            onUpdate={onUpdateTask}
          />
        ))}
      </div>

      <div className={styles.taskIndex}>
        {tasks.length === 0 ? (
          <button onClick={() => onCreateAt(start + 10 * 60 * minute)}>
            + open field
          </button>
        ) : (
          tasks.slice(0, 3).map((task) => (
            <button key={task.id} onClick={() => onSelectTask(task.start)}>
              <i style={{ background: spectrumColor(task.spectrum) }} />
              <span>{formatTime(task.start)}</span>
              {task.title}
            </button>
          ))
        )}
        {tasks.length > 3 && <small>+{tasks.length - 3} more objects</small>}
      </div>
    </article>
  );
}

interface WeekTaskBlockProps {
  task: TimelineTask;
  dayStart: number;
  dayEnd: number;
  onSelect: () => void;
  onUpdate: (id: string, start: number, end: number) => void;
}

function WeekTaskBlock({
  task,
  dayStart,
  dayEnd,
  onSelect,
  onUpdate,
}: WeekTaskBlockProps) {
  const [preview, setPreview] = useState({ start: task.start, end: task.end });
  const drag = useRef<{
    mode: "move" | "resize";
    clientX: number;
    start: number;
    end: number;
    width: number;
    moved: boolean;
  } | null>(null);

  const left = ((preview.start - dayStart) / dayDuration) * 100;
  const width = ((preview.end - preview.start) / dayDuration) * 100;

  function begin(event: PointerEvent<HTMLElement>, mode: "move" | "resize") {
    event.stopPropagation();
    const stripElement =
      mode === "resize"
        ? event.currentTarget.parentElement?.parentElement
        : event.currentTarget.parentElement;
    const strip = stripElement?.getBoundingClientRect();
    if (!strip) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      mode,
      clientX: event.clientX,
      start: task.start,
      end: task.end,
      width: strip.width,
      moved: false,
    };
  }

  function move(event: PointerEvent<HTMLButtonElement>) {
    if (!drag.current) return;
    const delta = snapDelta(
      ((event.clientX - drag.current.clientX) / drag.current.width) * dayDuration,
    );
    drag.current.moved ||= Math.abs(delta) >= 15 * minute;
    if (drag.current.mode === "move") {
      const duration = drag.current.end - drag.current.start;
      const start = Math.max(
        dayStart,
        Math.min(dayEnd - duration, drag.current.start + delta),
      );
      setPreview({ start, end: start + duration });
    } else {
      const end = Math.max(
        drag.current.start + 15 * minute,
        Math.min(dayEnd, drag.current.end + delta),
      );
      setPreview({ start: drag.current.start, end });
    }
  }

  function finish() {
    if (!drag.current) return;
    const moved = drag.current.moved;
    drag.current = null;
    if (moved) onUpdate(task.id, preview.start, preview.end);
    else onSelect();
  }

  return (
    <button
      className={styles.weekTask}
      data-fixed={task.fixed}
      title={`${task.title} · ${formatTime(preview.start)}–${formatTime(preview.end)}`}
      style={{
        left: `${left}%`,
        width: `${Math.max(1.2, width)}%`,
        borderColor: spectrumColor(task.spectrum),
        background: task.fixed ? "transparent" : spectrumColor(task.spectrum, 0.23),
      }}
      onPointerDown={(event) => begin(event, "move")}
      onPointerMove={move}
      onPointerUp={finish}
      onPointerCancel={() => {
        drag.current = null;
        setPreview({ start: task.start, end: task.end });
      }}
    >
      <i
        className={styles.resizeHandle}
        onPointerDown={(event) => begin(event, "resize")}
      />
    </button>
  );
}

function weekDays(now: number) {
  const date = new Date(now);
  const weekday = (date.getDay() + 6) % 7;
  date.setHours(0, 0, 0, 0);
  date.setDate(date.getDate() - weekday);
  return Array.from({ length: 7 }, (_, index) => {
    const start = date.getTime() + index * dayDuration;
    return { start, end: start + dayDuration };
  });
}

const snap = (time: number) => Math.round(time / (15 * minute)) * 15 * minute;
const snapDelta = (value: number) => Math.round(value / (15 * minute)) * 15 * minute;
const isSameDay = (left: number, right: number) =>
  new Date(left).toDateString() === new Date(right).toDateString();
const formatWeekday = (time: number) =>
  new Intl.DateTimeFormat("en-US", { weekday: "short" }).format(time).toUpperCase();
const formatDate = (time: number) =>
  new Intl.DateTimeFormat("en-US", { month: "short", day: "2-digit" }).format(time);
const formatTime = (time: number) =>
  new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" }).format(time);
const formatDuration = (minutes: number) =>
  minutes >= 60 ? `${(minutes / 60).toFixed(minutes % 60 ? 1 : 0)}h` : `${Math.round(minutes)}m`;
const loadLabel = (load: number) =>
  load < 0.28 ? "LIGHT" : load < 0.62 ? "MODERATE" : "ELEVATED";
