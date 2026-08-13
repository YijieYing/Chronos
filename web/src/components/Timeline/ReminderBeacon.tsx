import { useState } from "react";
import type { Reminder } from "../../types";
import styles from "./Timeline.module.css";

export function ReminderBeacon({ reminder, now, xFor, selected, expanded, onSelect }: { reminder: Reminder; now: number; xFor: (time: number) => number; selected: boolean; expanded: boolean; onSelect: (reminder: Reminder) => void }) {
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  const start = reminder.trigger.type === "time" ? reminder.trigger.at : reminder.trigger.start;
  const end = reminder.trigger.type === "time" ? reminder.trigger.at : reminder.trigger.end;
  const anchor = reminder.trigger.type === "time" ? start : (start + end) / 2;
  const distance = anchor - now;
  const state = Math.abs(distance) <= 5 * 60_000 ? "due" : distance > 0 && distance <= 60 * 60_000 ? "near" : "far";
  return <div className={styles.reminderLayer}>
    {reminder.trigger.type === "window" && <div className={styles.reminderWindow} style={{ left: xFor(start), width: Math.max(4, xFor(end) - xFor(start)) }} />}
    <button className={styles.reminderBeacon} data-timeline-object="reminder" data-selected={selected} data-state={state} style={{ left: xFor(anchor) }} onClick={(event) => { event.stopPropagation(); onSelect(reminder); }} onDoubleClick={(event) => { event.stopPropagation(); setPinned(value => !value); }} onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)} aria-expanded={hovered || pinned || expanded} aria-label={`Reminder ${reminder.title}`}>
      <i />
      {(hovered || pinned || expanded) && <span><b>&gt; reminder · {reminder.title}</b><small>{format(anchor)} · {reminder.delivery}</small></span>}
    </button>
  </div>;
}

const format = (value: number) => new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(value);
