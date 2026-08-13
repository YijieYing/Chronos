import { AnimatePresence, motion } from "framer-motion";
import type { ChronosLogEntry, TimelineReference } from "../../types";
import styles from "./Agent.module.css";

interface ChronosLogProps {
  expanded: boolean;
  entries: ChronosLogEntry[];
  pendingCount: number;
  onOpen: () => void;
  onClose: () => void;
  onRestore: (id: string) => void;
  onReference: (reference: TimelineReference) => void;
}

const actionableEvents = new Set([
  "clarification_requested",
  "proposal_created",
  "proposal_updated",
  "operation_failed",
]);

const formatTime = (value: number) =>
  new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(value);

export function ChronosLog({
  expanded,
  entries,
  pendingCount,
  onOpen,
  onClose,
  onRestore,
  onReference,
}: ChronosLogProps) {
  const peekEntry = pendingCount > 0
    ? entries.find((entry) => actionableEvents.has(entry.eventType))
    : undefined;
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
      {!expanded && peekEntry && (
        <motion.aside
          className={styles.logPeek}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 8 }}
        >
          <span>{eventLabel(peekEntry.eventType)}</span>
          <p>{peekEntry.message}</p>
          <button onClick={onOpen}>OPEN LOG · {pendingCount}</button>
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
