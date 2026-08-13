import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type {
  ChronosLogEntry,
  AgentCommand,
  PendingAgentOperation,
  TimelineReference,
  TimelineSelection,
} from "../../types";
import styles from "./Agent.module.css";

interface ChronosLogProps {
  expanded: boolean;
  entries: ChronosLogEntry[];
  pendingCount: number;
  pendingOperations: PendingAgentOperation[];
  proposals: AgentCommand[];
  selection: TimelineSelection | null;
  onOpen: () => void;
  onClose: () => void;
  onRestore: (id: string) => void;
  onReference: (reference: TimelineReference) => void;
  onAnswer: (
    operationId: string,
    answer: string,
    selection: TimelineSelection | null,
  ) => Promise<void>;
  onResolve: (operationId: string, accepted: boolean) => Promise<void>;
}

const formatTime = (value: number) =>
  new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(value);

export function ChronosLog({
  expanded,
  entries,
  pendingCount,
  pendingOperations,
  proposals,
  selection,
  onOpen,
  onClose,
  onRestore,
  onReference,
  onAnswer,
  onResolve,
}: ChronosLogProps) {
  const [activeOperationId, setActiveOperationId] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clarifications = pendingOperations.filter(
    (operation) => operation.state === "awaiting_clarification",
  );
  const active = clarifications.find((operation) => operation.id === activeOperationId)
    ?? clarifications[0];
  const revertedOperationIds = new Set(
    entries
      .filter((entry) => entry.eventType === "undo" && entry.operationId)
      .map((entry) => entry.operationId),
  );
  const restoredEntryIds = new Set(
    entries
      .map((entry) => entry.metadata.restored_log_entry_id)
      .filter((id): id is string => typeof id === "string"),
  );

  return (
    <AnimatePresence>
      {!expanded && active && (
        <motion.aside
          className={styles.logPeek}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 8 }}
        >
          <span>CHRONOS · NEEDS INPUT</span>
          <p>{active.questions[0]?.question ?? active.summary}</p>
          {!!active.questions[0]?.options.length && (
            <div className={styles.quickAnswers}>
              {active.questions[0].options.map((option) => (
                <button key={option} onClick={() => submit(active.id, option)}>{option}</button>
              ))}
            </div>
          )}
          <form onSubmit={(event) => {
            event.preventDefault();
            void submit(active.id, answer);
          }}>
            <input
              aria-label="回答 Chronos 澄清问题"
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder={selection?.type === "time_range" ? "输入回答，或使用已选时间范围" : "补充信息…"}
              disabled={submitting}
            />
            <button disabled={submitting || (!answer.trim() && !selection)}>
              {submitting ? "…" : selection?.type === "time_range" && !answer.trim() ? "USE RANGE" : "REPLY"}
            </button>
          </form>
          {error && <output role="alert">{error}</output>}
          <div className={styles.peekFooter}>
            <button onClick={onOpen}>OPEN LOG · {pendingCount}</button>
            {clarifications.length > 1 && (
              <button onClick={() => cycleOperation(active.id)}>
                NEXT · {clarifications.length}
              </button>
            )}
          </div>
        </motion.aside>
      )}
      {expanded && (
        <>
          <motion.button
            className={styles.logBackdrop}
            aria-label="Close Chronos log"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          />
          <motion.aside
            className={styles.logDrawer}
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 34 }}
          >
            <header>
              <div>
                <span className={styles.eyebrow}>SHARED EVENT STREAM</span>
                <h2>Chronos Log</h2>
                <p className={styles.logSummary}>
                  {pendingCount ? `${pendingCount} operation${pendingCount > 1 ? "s" : ""} need attention` : "No pending operations"}
                </p>
              </div>
              <button className={styles.iconButton} onClick={onClose}>×</button>
            </header>

            <div className={styles.logList}>
              {clarifications.map((operation) => (
                <article className={styles.pendingOperation} key={operation.id}>
                  <span>CHRONOS · NEEDS INPUT</span>
                  <p>{operation.questions[0]?.question ?? operation.summary}</p>
                  <button onClick={() => {
                    setActiveOperationId(operation.id);
                    onClose();
                  }}>ANSWER</button>
                </article>
              ))}
              {proposals.filter((proposal) => proposal.canResolve).map((proposal) => (
                <article className={styles.pendingOperation} key={proposal.id}>
                  <span>CHRONOS · PROPOSAL</span>
                  <p>{proposal.title}</p>
                  <div className={styles.proposalChanges}>
                    {proposal.lines.map((line) => <code key={line}>{line}</code>)}
                  </div>
                  <div className={styles.proposalActions}>
                    <button onClick={() => void onResolve(proposal.id, true)}>APPLY</button>
                    <button onClick={() => void onResolve(proposal.id, false)}>REJECT</button>
                  </div>
                </article>
              ))}
              {entries.length === 0 ? (
                <div className={styles.emptyLog}>Chronos 尚未记录时间轴事件。</div>
              ) : (
                entries.map((entry) => (
                  <article className={styles.logEntry} key={entry.id}>
                    <div className={styles.logMeta}>
                      <time>{formatTime(entry.time)}</time>
                      <span data-event={entry.eventType}>{eventLabel(entry.eventType)}</span>
                    </div>
                    <p className={styles.logMessage}>{entry.message}</p>
                    {!!entry.references.length && (
                      <div className={styles.logReferences}>
                        {entry.references.map((reference, index) => (
                          <button
                            key={referenceKey(reference, index)}
                            onClick={() => onReference(reference)}
                          >
                            {referenceLabel(reference)}
                          </button>
                        ))}
                      </div>
                    )}
                    {canRestore(entry, revertedOperationIds, restoredEntryIds) && (
                      <button
                        className={styles.restoreButton}
                        onClick={() => onRestore(entry.id)}
                      >
                        Undo
                      </button>
                    )}
                  </article>
                ))
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );

  async function submit(operationId: string, value: string) {
    if (submitting || (!value.trim() && !selection)) return;
    setSubmitting(true);
    setError(null);
    try {
      await onAnswer(operationId, value.trim(), selection);
      setAnswer("");
      setActiveOperationId(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSubmitting(false);
    }
  }

  function cycleOperation(currentId: string) {
    const index = clarifications.findIndex((item) => item.id === currentId);
    setActiveOperationId(clarifications[(index + 1) % clarifications.length].id);
    setAnswer("");
    setError(null);
  }
}

function eventLabel(eventType: ChronosLogEntry["eventType"]) {
  return eventType.replaceAll("_", " ").toUpperCase();
}

function referenceKey(reference: TimelineReference, index: number) {
  return reference.type === "time_range"
    ? `${reference.type}-${reference.start}-${reference.end}-${index}`
    : `${reference.type}-${reference.id}-${index}`;
}

function referenceLabel(reference: TimelineReference) {
  if (reference.type === "time_range") {
    return `↗ ${formatTime(reference.start)}–${formatTime(reference.end)}`;
  }
  return `↗ ${reference.type.toUpperCase()} · ${reference.id.slice(0, 8)}`;
}

function canRestore(
  entry: ChronosLogEntry,
  revertedOperationIds: Set<string | undefined>,
  restoredEntryIds: Set<string>,
) {
  if (restoredEntryIds.has(entry.id)) return false;
  if (entry.eventType === "operation_completed" && entry.operationId) {
    return !revertedOperationIds.has(entry.operationId);
  }
  if (entry.eventType.startsWith("manual_task_")) return true;
  return entry.eventType === "operation_completed"
    && typeof entry.metadata.manual_action === "string";
}
