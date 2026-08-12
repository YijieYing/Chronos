import { motion } from "framer-motion";
import type { AgentCommand } from "../../types";
import styles from "../Timeline/Timeline.module.css";

interface TimelineCommandProps {
  command: AgentCommand;
  x: number;
  onResolve: (id: string, accepted: boolean) => void;
}

export function TimelineCommand({
  command,
  x,
  onResolve,
}: TimelineCommandProps) {
  return (
    <motion.div
      className={styles.timelineCommand}
      style={{ left: `clamp(16px, ${x}px, calc(100% - 270px))` }}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: command.status === "proposed" ? 1 : 0.48, y: 0 }}
    >
      <span className={styles.commandCursor}>&gt;_</span>
      <strong>{command.title}</strong>
      {command.lines.map((line) => (
        <code key={line}>{line}</code>
      ))}
      {!!command.contextUsed?.length && (
        <details>
          <summary>CONTEXT USED · {command.contextUsed.length}</summary>
          {command.contextUsed.map((item) => <p key={item}>{item}</p>)}
        </details>
      )}
      {command.status === "proposed" && command.canResolve !== false ? (
        <div>
          <button onClick={() => onResolve(command.id, true)}>Accept</button>
          <button onClick={() => onResolve(command.id, false)}>Reject</button>
        </div>
      ) : command.status !== "proposed" ? (
        <small>{command.status.toUpperCase()}</small>
      ) : null}
    </motion.div>
  );
}
