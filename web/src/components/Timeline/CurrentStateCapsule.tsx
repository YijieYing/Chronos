import { motion } from "framer-motion";
import type { TemporalIntelligence, TimelineTask } from "../../types";
import styles from "./Timeline.module.css";

interface CurrentStateCapsuleProps {
  intelligence: TemporalIntelligence;
  activeTask?: TimelineTask;
}

export function CurrentStateCapsule({
  intelligence,
  activeTask,
}: CurrentStateCapsuleProps) {
  return (
    <motion.aside
      className={styles.stateCapsule}
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
    >
      <div className={styles.capsuleHeader}>
        <span>NOW / TEMPORAL STATE</span>
        <i data-health={intelligence.health} />
      </div>
      <strong>{activeTask?.title ?? "Unassigned activity"}</strong>
      <p>{humanState(intelligence.cognitiveState)}</p>
      <dl>
        <div>
          <dt>Focus</dt>
          <dd>{Math.round(intelligence.focus * 100)}%</dd>
        </div>
        <div>
          <dt>Predicted finish</dt>
          <dd>{formatTime(intelligence.predictedFinish)}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{intelligence.health}</dd>
        </div>
      </dl>
      {intelligence.estimatedDelay > 0 && (
        <span className={styles.delay}>+{intelligence.estimatedDelay} min projected</span>
      )}
    </motion.aside>
  );
}

const humanState = (value: TemporalIntelligence["cognitiveState"]) =>
  ({
    deep_work: "Deep Work",
    engaged: "Engaged",
    fragmented: "Fragmented attention",
    recovery: "Recovery",
  })[value];

const formatTime = (value: number) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(value);
