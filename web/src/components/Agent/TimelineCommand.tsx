import { useState, type WheelEvent } from "react";
import { createPortal } from "react-dom";
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
  const [clarificationOpen, setClarificationOpen] = useState(true);
  const needsClarification = command.canResolve === false;
  if (needsClarification) {
    if (!clarificationOpen) return null;
    return createPortal(
      <div
        className={styles.clarificationBackdrop}
        role="presentation"
        onPointerDown={(event) => event.stopPropagation()}
        onWheel={stopWheelPropagation}
      >
        <motion.section
          className={styles.clarificationDialog}
          role="dialog"
          aria-modal="true"
          aria-labelledby={`clarification-${command.id}`}
          initial={{ opacity: 0, y: 16, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
        >
          <header>
            <span>CHRONOS / CLARIFICATION</span>
            <button
              type="button"
              aria-label="关闭澄清提示"
              onClick={() => setClarificationOpen(false)}
            >
              ×
            </button>
          </header>
          <strong id={`clarification-${command.id}`}>{command.title}</strong>
          <div className={styles.clarificationQuestions}>
            {command.lines.map((line, index) => (
              <p key={`${index}-${line}`}>
                <code>{String(index + 1).padStart(2, "0")}</code>
                <span>{line}</span>
              </p>
            ))}
          </div>
          <footer>
            请关闭此提示，在底部输入栏补充缺失信息后重新提交。
          </footer>
        </motion.section>
      </div>,
      document.body,
    );
  }

  return (
    <motion.div
      className={styles.timelineCommand}
      data-timeline-object="agent-operation"
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

function stopWheelPropagation(event: WheelEvent<HTMLDivElement>) {
  event.stopPropagation();
}
