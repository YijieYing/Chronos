import { useEffect, useMemo, useState } from "react";
import { AgentInput } from "./components/Agent/AgentInput";
import { ChronosLog } from "./components/Agent/ChronosLog";
import { MemorySync } from "./components/Agent/MemorySync";
import { TaskComposer } from "./components/TaskComposer";
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
import { useProjectionStore } from "./state/projectionStore";
import type { TimelineReference, TimelineTask } from "./types";
import styles from "./App.module.css";

const fiveMinutes = 5 * 60_000;

export default function App() {
  const projectionStore = useProjectionStore();
  const timeline = useTimelineStore(projectionStore.refresh);
  const liveMonitor = useLiveMonitor();
  const [now, setNow] = useState(Date.now());
  const [composerOpen, setComposerOpen] = useState(false);
  const [creationMode, setCreationMode] = useState<"task" | "reminder">("task");
  const [composerStart, setComposerStart] = useState(Date.now());
  const [editingTask, setEditingTask] = useState<TimelineTask | null>(null);
  const [logOpen, setLogOpen] = useState(false);
  const [overviewOpen, setOverviewOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [expandedReminderId, setExpandedReminderId] = useState<string | null>(null);
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

  useEffect(() => {
    const clear = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      timeline.clearSelection();
      setExpandedReminderId(null);
    };
    window.addEventListener("keydown", clear);
    return () => window.removeEventListener("keydown", clear);
  }, []);

  function openComposer(time = Date.now(), mode: "task" | "reminder" = "task") {
    timeline.clearSelection();
    setExpandedReminderId(null);
    setEditingTask(null);
    setComposerStart(time);
    setCreationMode(mode);
    setComposerOpen(true);
  }

  function openTaskEditor(task: TimelineTask) {
    setEditingTask(task);
    setComposerStart(task.start);
    setCreationMode("task");
    setComposerOpen(true);
  }

  function focusReference(reference: TimelineReference) {
    timeline.selectTimeline(reference);
    if (reference.type === "time_range") {
      timeline.focusTime((reference.start + reference.end) / 2);
      setLogOpen(false);
      return;
    }
    if (reference.type === "task") {
      const task = timeline.tasks.find(
        (item) => item.id === reference.id || item.seriesId === reference.id,
      );
      if (task) timeline.focusTime(task.start);
    } else {
      const reminder = timeline.reminders.find((item) => item.id === reference.id);
      if (reminder) {
        timeline.focusTime(reminder.trigger.type === "time"
          ? reminder.trigger.at
          : (reminder.trigger.start + reminder.trigger.end) / 2);
      }
    }
    setLogOpen(false);
  }

  const selectedTask = timeline.selection?.type === "task"
    ? timeline.tasks.find(
        (item) => item.id === timeline.selection?.id
          || item.seriesId === timeline.selection?.id,
      )
    : undefined;
  const selectedReminder = timeline.selection?.type === "reminder"
    ? timeline.reminders.find((item) => item.id === timeline.selection?.id)
    : undefined;
  const selectionLabel = timeline.selection?.type === "time_range"
    ? `${formatSelectionTime(timeline.selection.start)}–${formatSelectionTime(timeline.selection.end)}`
    : selectedTask?.title ?? selectedReminder?.title;

  function openSelectionProperties() {
    if (selectedTask) openTaskEditor(selectedTask);
    if (selectedReminder) setExpandedReminderId(selectedReminder.id);
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
          <button onClick={() => openComposer(Date.now(), "reminder")}>◇ REMINDER</button>
          <button onClick={() => setLogOpen(true)}>
            CHRONOS LOG
            {timeline.pendingOperationCount > 0 && <em>{timeline.pendingOperationCount}</em>}
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
        projections={projectionStore.projections}
        focusTarget={timeline.focusTarget}
        selection={timeline.selection}
        expandedReminderId={expandedReminderId}
        onCreateAt={(time) => openComposer(time)}
        onSelect={(selection) => {
          timeline.selectTimeline(selection);
          if (selection?.type !== "reminder") setExpandedReminderId(null);
        }}
        onEditTask={openTaskEditor}
        onResolveCommand={timeline.resolveCommand}
      />

      <AgentInput
        onSubmit={timeline.runAgent}
        selectionLabel={selectionLabel}
        onClearSelection={() => {
          timeline.clearSelection();
          setExpandedReminderId(null);
        }}
        onOpenProperties={selectedTask || selectedReminder ? openSelectionProperties : undefined}
      />
      <OverviewMap
        open={overviewOpen}
        tasks={timeline.tasks}
        reminders={timeline.reminders}
        pendingOperationCount={timeline.pendingOperationCount}
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
        creationMode={creationMode}
        onCreationModeChange={setCreationMode}
        onClose={() => {
          setComposerOpen(false);
          setEditingTask(null);
        }}
        onCreate={timeline.addTask}
        onCreateReminder={timeline.addReminder}
        onUpdate={timeline.updateTask}
        onDelete={timeline.deleteTask}
      />
      <ChronosLog
        expanded={logOpen}
        entries={timeline.logs}
        pendingCount={timeline.pendingOperationCount}
        pendingOperations={timeline.pendingOperations}
        selection={timeline.selection}
        onOpen={() => setLogOpen(true)}
        onClose={() => setLogOpen(false)}
        onRestore={timeline.restoreLog}
        onReference={focusReference}
        onAnswer={timeline.answerOperation}
      />
      <MemorySync open={memoryOpen} onClose={() => setMemoryOpen(false)} />
    </main>
  );
}

const formatSelectionTime = (value: number) => new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
}).format(value);
