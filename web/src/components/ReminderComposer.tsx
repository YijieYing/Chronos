import { FormEvent, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { Reminder } from "../types";
import styles from "./ReminderComposer.module.css";

interface Props {
  open: boolean;
  initialTime: number;
  onClose: () => void;
  onCreate: (reminder: Reminder) => void;
}

const local = (value: number) => {
  const date = new Date(value);
  return new Date(value - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};

export function ReminderComposer({ open, initialTime, onClose, onCreate }: Props) {
  const [title, setTitle] = useState("");
  const [type, setType] = useState<"time" | "window">("time");
  const [start, setStart] = useState(local(initialTime));
  const [end, setEnd] = useState(local(initialTime + 3 * 3_600_000));
  const [delivery, setDelivery] = useState<"exact" | "context-aware">("exact");
  useEffect(() => {
    if (!open) return;
    setTitle("");
    setType("time");
    setStart(local(initialTime));
    setEnd(local(initialTime + 3 * 3_600_000));
    setDelivery("exact");
  }, [initialTime, open]);
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    onCreate({
      id: crypto.randomUUID(), title: title.trim(),
      trigger: type === "time"
        ? { type, at: new Date(start).getTime() }
        : { type, start: new Date(start).getTime(), end: new Date(end).getTime() },
      delivery: type === "time" ? "exact" : delivery,
      priority: 3, status: "pending", source: "user", createdAt: Date.now(),
    });
    onClose();
  }
  return <AnimatePresence>{open && <>
    <motion.button className={styles.backdrop} aria-label="Close reminder" onClick={onClose} />
    <motion.form className={styles.composer} onSubmit={submit} initial={{ y: 18, opacity: 0 }} animate={{ y: 0, opacity: 1 }}>
      <header><span>NEW TEMPORAL BEACON</span><button type="button" onClick={onClose}>×</button></header>
      <input autoFocus value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提醒内容" />
      <div className={styles.switches}><button type="button" data-active={type === "time"} onClick={() => setType("time")}>POINT</button><button type="button" data-active={type === "window"} onClick={() => setType("window")}>WINDOW</button></div>
      <input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} />
      {type === "window" && <><input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} /><label><input type="checkbox" checked={delivery === "context-aware"} onChange={(e) => setDelivery(e.target.checked ? "context-aware" : "exact")} /> CONTEXT-AWARE DELIVERY</label></>}
      <button type="submit">PLACE BEACON</button>
    </motion.form>
  </>}</AnimatePresence>;
}
