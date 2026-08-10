import { AnimatePresence, motion } from "framer-motion";
import type { ChronosLogEntry } from "../../types";
import styles from "./Agent.module.css";

interface ChronosLogProps {
  open: boolean;
  entries: ChronosLogEntry[];
  onClose: () => void;
  onRestore: (id: string) => void;
}

const formatTime = (value: number) =>
  new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  }).format(value);

export function ChronosLog({
  open,
  entries,
  onClose,
  onRestore,
}: ChronosLogProps) {
  return (
    <AnimatePresence>
      {open && (
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
                <span className={styles.eyebrow}>SYSTEM RECORD</span>
                <h2>Chronos Log</h2>
              </div>
              <button className={styles.iconButton} onClick={onClose}>×</button>
            </header>

            <div className={styles.logList}>
              {entries.length === 0 ? (
                <div className={styles.emptyLog}>时间轴尚未被 Chronos 调整。</div>
              ) : (
                [...entries].reverse().map((entry) => (
                  <article className={styles.logEntry} key={entry.id}>
                    <div className={styles.logMeta}>
                      <time>{formatTime(entry.time)}</time>
                      <span data-status={entry.status}>{entry.status}</span>
                    </div>
                    <p className={styles.logRequest}>{entry.request}</p>
                    <div className={styles.changeSet}>
                      <span>RESULT</span>
                      <p>{entry.response}</p>
                    </div>
                    {!!entry.contextUsed?.length && (
                      <details className={styles.contextUsed}>
                        <summary>CONTEXT USED · {entry.contextUsed.length}</summary>
                        {entry.contextUsed.map((item) => <p key={item}>{item}</p>)}
                      </details>
                    )}
                    {entry.status === "applied" && (
                      <button
                        className={styles.restoreButton}
                        onClick={() => onRestore(entry.id)}
                      >
                        Restore previous state
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
