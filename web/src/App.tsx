import { useEffect, useMemo, useState } from "react";
import { AgentInput } from "./components/Agent/AgentInput";
import { ChronosLog } from "./components/Agent/ChronosLog";
import { MemorySync } from "./components/Agent/MemorySync";
import { TaskComposer } from "./components/TaskComposer";
import { ReminderComposer } from "./components/ReminderComposer";
import { OverviewMap } from "./components/Overview/OverviewMap";
import { WaveTimeline } from "./components/Timeline/WaveTimeline";
import {
  createMockMonitorData,
  createMockMonitorHistory,
} from "./mock/mockMonitorData";
import {
  adaptCognitiveStateData,
  adaptMonitorData,
} from "./monitor/MonitorAdapter";
import { useLiveMonitor } from "./monitor/useLiveMonitor";
import { useTimelineStore } from "./state/timelineStore";
import type { TimelineTask } from "./types";
import styles from "./App.module.css";

const fiveMinutes = 5 * 60_000;

export default function App() {
  const timeline = useTimelineStore();
  const liveMonitor = useLiveMonitor();
  const [now, setNow] = useState(Date.now());
  const [composerOpen, setComposerOpen] = useState(false);
  const [reminderOpen, setReminderOpen] = useState(false);
  const [reminderStart, setReminderStart] = useState(Date.now());
  const [creationChoice, setCreationChoice] = useState<number | null>(null);
  const [composerStart, setComposerStart] = useState(Date.now());
  const [editingTask, setEditingTask] = useState<TimelineTask | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [overviewOpen, setOverviewOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const monitorBucket = Math.floor(now / fiveMinutes) * fiveMinutes;
  const mockMonitor = useMemo(
    () => createMockMonitorData(monitorBucket),
    [monitorBucket],
  );
  const mockMonitorHistory = useMemo(
    () => createMockMonitorHistory(monitorBucket),
    [monitorBucket],
  );
  const demoTimeline = useMemo(
    () => adaptMonitorData(mockMonitor, timeline.tasks, now, mockMonitorHistory),
    [mockMonitor, mockMonitorHistory, now, timeline.tasks],
  );
  const liveTimeline = useMemo(
    () =>
      liveMonitor.mode !== "demo" && liveMonitor.points.length
        ? adaptCognitiveStateData(liveMonitor.points, timeline.tasks, now)
        : null,
    [liveMonitor, now, timeline.tasks],
  );
  const { intelligence, predictedTasks } = liveTimeline ?? demoTimeline;

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  function openComposer(time = Date.now()) {
    setEditingTask(null);
    setComposerStart(time);
    setComposerOpen(true);
  }
  function chooseCreation(time: number) { setCreationChoice(time); }

  function openTaskEditor(task: TimelineTask) {
    setEditingTask(task);
    setComposerStart(task.start);
    setComposerOpen(true);
  }

  return (
    <main className={styles.app}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <span className={styles.mark}><i />C</span>
          <div>
            <h1>CHRONOS</h1>
            <p>DYNAMIC TEMPORAL SYSTEM · 24H</p>
          </div>
        </div>
        <div className={styles.headerActions}>
          <div className={styles.monitorStatus} data-mode={liveMonitor.mode}>
            <i />
            {liveMonitor.mode === "live"
              ? "MONITOR LIVE"
              : liveMonitor.mode === "history"
                ? "MONITOR HISTORY"
                : "DEMO DATA"}
            <b>{Math.round(intelligence.stateConfidence * 100)}% CONF.</b>
          </div>
          <div
            className={styles.monitorStatus}
            data-mode={timeline.storageStatus === "ready" ? "live" : "demo"}
            title={timeline.storageError ?? "Timeline tasks are stored in local SQLite"}
          >
            <i />
            {timeline.storageStatus === "ready"
              ? "SCHEDULE SAVED"
              : timeline.storageStatus === "loading"
                ? "SCHEDULE LOADING"
                : "SCHEDULE ERROR"}
          </div>
          <button onClick={() => setOverviewOpen(true)}>OVERVIEW</button>
          <button onClick={() => setMemoryOpen(true)}>MEMORY SYNC</button>
          <button onClick={() => openComposer()}>＋ TASK</button>
          <button onClick={() => {
            setReminderStart(Date.now());
            setReminderOpen(true);
          }}>◇ REMINDER</button>
          <button onClick={() => setLogOpen(true)}>
            OPEN CHRONOS LOG
            {timeline.logs.length > 0 && <em>{timeline.logs.length}</em>}
          </button>
        </div>
      </header>

      <section className={styles.contextStrip}>
        <span>
          {liveMonitor.mode === "live"
            ? "LIVE FIELD"
            : liveMonitor.mode === "history"
              ? "OBSERVED HISTORY"
              : "DEMO FIELD"}
        </span>
        <p>
          {liveMonitor.mode === "live"
            ? "真实 Monitor 信号已转换为 Cognitive State；原始输入事件不会进入前端。"
            : liveMonitor.mode === "history"
              ? "Monitor 当前不在线；Record 仍使用已持久化的真实观测，当前状态按过期数据处理。"
              : "Monitor 尚未产生实时状态，当前显示明确标记的演示数据。"}
        </p>
      </section>

      <WaveTimeline
        now={now}
        tasks={predictedTasks}
        reminders={timeline.reminders}
        intelligence={intelligence}
        commands={timeline.commands}
        focusTarget={timeline.focusTarget}
        onCreateAt={chooseCreation}
        onEditTask={openTaskEditor}
        onResolveCommand={timeline.resolveCommand}
      />

      <AgentInput onSubmit={timeline.runAgent} />
      <OverviewMap
        open={overviewOpen}
        tasks={timeline.tasks}
        reminders={timeline.reminders}
        logs={timeline.logs}
        onClose={() => setOverviewOpen(false)}
        onOpenLog={() => setLogOpen(true)}
        onSelectTask={timeline.focusTime}
        onCreateAt={openComposer}
        onUpdateTask={timeline.updateTaskTiming}
      />
      <TaskComposer
        open={composerOpen}
        initialStart={composerStart}
        task={editingTask}
        onClose={() => {
          setComposerOpen(false);
          setEditingTask(null);
        }}
        onCreate={timeline.addTask}
        onUpdate={timeline.updateTask}
        onDelete={timeline.deleteTask}
      />
      {creationChoice !== null && <div className={styles.creationChoice}>
        <button onClick={() => { openComposer(creationChoice); setCreationChoice(null); }}>＋ Task</button>
        <button onClick={() => { setReminderStart(creationChoice); setReminderOpen(true); setCreationChoice(null); }}>◇ Reminder</button>
      </div>}
      <ReminderComposer open={reminderOpen} initialTime={reminderStart} onClose={() => setReminderOpen(false)} onCreate={timeline.addReminder} />
      <ChronosLog
        open={logOpen}
        entries={timeline.logs}
        onClose={() => setLogOpen(false)}
        onRestore={timeline.restoreLog}
      />
      <MemorySync open={memoryOpen} onClose={() => setMemoryOpen(false)} />
    </main>
  );
}
