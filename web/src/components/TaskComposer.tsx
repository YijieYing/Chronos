import { type CSSProperties, FormEvent, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type {
  NewTaskInput,
  RecurrenceRule,
  TaskType,
  TimelineTask,
} from "../types";
import { spectrumColor } from "./Timeline/waveMath";
import { cognitiveLoadColor } from "./Timeline/cognitiveLoadColor";
import styles from "./TaskComposer.module.css";

interface TaskComposerProps {
  open: boolean;
  initialStart: number;
  task?: TimelineTask | null;
  onClose: () => void;
  onCreate: (input: NewTaskInput) => void;
  onUpdate: (taskId: string, input: NewTaskInput) => void;
  onDelete: (taskId: string) => void;
}

function localDateTime(timestamp: number) {
  const date = new Date(timestamp);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(timestamp - offset).toISOString().slice(0, 16);
}

function typeForSpectrum(value: number): TaskType {
  if (value < 0.18) return "creative";
  if (value < 0.4) return "coding";
  if (value < 0.62) return "research";
  if (value < 0.82) return "communication";
  return "execution";
}

export function TaskComposer({
  open,
  initialStart,
  task,
  onClose,
  onCreate,
  onUpdate,
  onDelete,
}: TaskComposerProps) {
  const [title, setTitle] = useState("");
  const [start, setStart] = useState(localDateTime(initialStart));
  const [duration, setDuration] = useState(45);
  const [intensity, setIntensity] = useState(0.55);
  const [spectrum, setSpectrum] = useState(0.35);
  const [fixed, setFixed] = useState(false);
  const [frequency, setFrequency] = useState<"none" | "daily" | "weekly">("none");
  const [weekdays, setWeekdays] = useState<number[]>([]);
  const [recurrenceUntil, setRecurrenceUntil] = useState("");

  useEffect(() => {
    if (!open) return;
    if (task) {
      setTitle(task.title);
      setStart(localDateTime(task.start));
      setDuration(Math.max(5, Math.round((task.end - task.start) / 60_000)));
      setIntensity(task.intensity);
      setSpectrum(task.spectrum);
      setFixed(task.fixed);
      setFrequency(task.recurrence?.frequency ?? "none");
      setWeekdays(
        task.recurrence?.frequency === "weekly"
          ? task.recurrence.weekdays
          : [],
      );
      setRecurrenceUntil(task.recurrence?.until ?? "");
      return;
    }
    setTitle("");
    setStart(localDateTime(initialStart));
    setDuration(45);
    setIntensity(0.55);
    setSpectrum(0.35);
    setFixed(false);
    setFrequency("none");
    setWeekdays([]);
    setRecurrenceUntil("");
  }, [initialStart, open, task]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    const input: NewTaskInput = {
      title: title.trim(),
      start: new Date(start).getTime(),
      durationMinutes: duration,
      intensity,
      spectrum,
      fixed,
      type: fixed ? "meeting" : typeForSpectrum(spectrum),
      recurrence: recurrenceRule(frequency, weekdays, recurrenceUntil),
    };
    if (task) onUpdate(task.id, input);
    else onCreate(input);
    onClose();
  }

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.button
            aria-label="Close task composer"
            className={styles.backdrop}
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          />
          <motion.form
            className={styles.composer}
            onSubmit={submit}
            initial={{ y: 30, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 30, opacity: 0 }}
          >
            <header>
              <div>
                <span>{task ? "EDIT TIMELINE OBJECT" : "NEW TIMELINE OBJECT"}</span>
                <h2>{task ? "编辑任务" : "创建任务"}</h2>
              </div>
              <button type="button" onClick={onClose}>×</button>
            </header>

            <label>
              TASK
              <input
                autoFocus
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="任务名称"
              />
            </label>

            <div className={styles.twoColumns}>
              <label>
                START
                <input
                  type="datetime-local"
                  value={start}
                  onChange={(event) => setStart(event.target.value)}
                />
              </label>
              <label>
                MINUTES
                <input
                  type="number"
                  min={5}
                  step={5}
                  value={duration}
                  onChange={(event) => setDuration(Number(event.target.value))}
                />
              </label>
            </div>

            <label>
              <span className={styles.rangeLabel}>
                COGNITIVE INTENSITY <b>{Math.round(intensity * 100)}</b>
              </span>
              <input
                className={styles.intensity}
                style={{
                  "--intensity-color": cognitiveLoadColor(intensity),
                } as CSSProperties}
                type="range"
                min={0.1}
                max={1}
                step={0.05}
                value={intensity}
                onChange={(event) => setIntensity(Number(event.target.value))}
              />
            </label>

            <fieldset className={styles.recurrence}>
              <legend>FREQUENCY / OPTIONAL</legend>
              <div className={styles.frequencyOptions}>
                {([
                  ["none", "不重复"],
                  ["daily", "每天"],
                  ["weekly", "每周"],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    data-active={frequency === value}
                    onClick={() => {
                      setFrequency(value);
                      if (value === "weekly" && weekdays.length === 0) {
                        setWeekdays([new Date(start).getDay()]);
                      }
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {frequency === "weekly" && (
                <div className={styles.weekdayOptions}>
                  {weekdaysInDisplayOrder.map(({ value, label }) => (
                    <button
                      key={value}
                      type="button"
                      data-active={weekdays.includes(value)}
                      onClick={() =>
                        setWeekdays((current) =>
                          current.includes(value)
                            ? current.filter((day) => day !== value)
                            : [...current, value].sort(),
                        )
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {frequency !== "none" && (
                <label>
                  REPEAT UNTIL / OPTIONAL
                  <input
                    type="date"
                    min={start.slice(0, 10)}
                    value={recurrenceUntil}
                    onChange={(event) => setRecurrenceUntil(event.target.value)}
                  />
                </label>
              )}
            </fieldset>

            <label>
              <span className={styles.rangeLabel}>
                <i>INSPIRATION</i>
                TASK SPECTRUM
                <i>EXECUTION</i>
              </span>
              <input
                className={styles.spectrum}
                style={{
                  "--spectrum-color": spectrumColor(spectrum),
                } as CSSProperties}
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={spectrum}
                onChange={(event) => setSpectrum(Number(event.target.value))}
              />
            </label>

            <label className={styles.fixedToggle}>
              <input
                type="checkbox"
                checked={fixed}
                onChange={(event) => setFixed(event.target.checked)}
              />
              <span>
                <b>固定安排</b>
                使用稳定方波显示，Chronos 不会自动移动
              </span>
            </label>

            {task ? (
              <div className={styles.editActions}>
                <button
                  className={styles.deleteButton}
                  type="button"
                  onClick={() => {
                    onDelete(task.id);
                    onClose();
                  }}
                >
                  {task.recurrence ? "DELETE SERIES" : "DELETE"}
                </button>
                <button className={styles.saveButton} type="submit">
                  SAVE CHANGES
                </button>
              </div>
            ) : (
              <button className={styles.createButton} type="submit">
                ADD TO TIMELINE
              </button>
            )}
          </motion.form>
        </>
      )}
    </AnimatePresence>
  );
}

const weekdaysInDisplayOrder = [
  { value: 1, label: "一" },
  { value: 2, label: "二" },
  { value: 3, label: "三" },
  { value: 4, label: "四" },
  { value: 5, label: "五" },
  { value: 6, label: "六" },
  { value: 0, label: "日" },
];

function recurrenceRule(
  frequency: "none" | "daily" | "weekly",
  weekdays: number[],
  until: string,
): RecurrenceRule | undefined {
  const boundary = until ? { until } : {};
  if (frequency === "daily") return { frequency: "daily", ...boundary };
  if (frequency === "weekly" && weekdays.length) {
    return { frequency: "weekly", weekdays, ...boundary };
  }
  return undefined;
}
