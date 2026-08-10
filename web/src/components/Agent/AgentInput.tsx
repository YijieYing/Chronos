import { FormEvent, useState } from "react";
import { motion } from "framer-motion";
import styles from "./Agent.module.css";

interface AgentInputProps {
  onSubmit: (command: string) => void;
}

export function AgentInput({ onSubmit }: AgentInputProps) {
  const [value, setValue] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    const command = value.trim();
    if (!command) return;
    onSubmit(command);
    setValue("");
  }

  return (
    <motion.form
      className={styles.commandBar}
      onSubmit={submit}
      initial={{ y: 18, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
    >
      <span className={styles.prompt}>&gt;</span>
      <input
        aria-label="Chronos command"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="告诉 Chronos 要安排、移动或查找什么…"
      />
      <span className={styles.commandHint}>⌘ ↵</span>
      <button type="submit">RUN</button>
    </motion.form>
  );
}
