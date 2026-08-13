import { FormEvent, useState } from "react";
import { motion } from "framer-motion";
import styles from "./Agent.module.css";

interface AgentInputProps {
  onSubmit: (command: string) => Promise<void>;
  selectionLabel?: string;
  onClearSelection?: () => void;
  onOpenProperties?: () => void;
}

export function AgentInput({ onSubmit, selectionLabel, onClearSelection, onOpenProperties }: AgentInputProps) {
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const command = value.trim();
    if (!command || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(command);
      setValue("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <motion.form
      className={styles.commandBar}
      onSubmit={submit}
      initial={{ y: 18, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
    >
      {selectionLabel && (
        <div className={styles.selectionContext}>
          <span>SELECTED · {selectionLabel}</span>
          {onOpenProperties && <button type="button" onClick={onOpenProperties}>PROPERTIES</button>}
          <button type="button" onClick={onClearSelection}>×</button>
        </div>
      )}
      <span className={styles.prompt}>&gt;</span>
      <input
        aria-label="Chronos command"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            event.currentTarget.form?.requestSubmit();
          }
        }}
        placeholder={selectionLabel ? "Ask Chronos about this…" : "告诉 Chronos 要安排、移动或查找什么…"}
        disabled={submitting}
      />
      <span className={styles.commandHint}>
        {submitting ? "ANALYZING…" : "⌘ ↵"}
      </span>
      <button type="submit" disabled={submitting}>
        {submitting ? "WAIT" : "RUN"}
      </button>
      {error && (
        <output className={styles.commandError} role="alert" title={error}>
          AGENT ERROR · {error}
        </output>
      )}
    </motion.form>
  );
}
