import { useEffect, useMemo, useRef, useState } from "react";
import {
  createTimelineTask,
  deleteTimelineTask,
  loadTimelineTasks,
  saveTimelineTask,
} from "../schedule/timelineApi";
import {
  createProposal,
  answerClarification,
  loadProposals,
  resolveProposal,
  restoreProposal,
  type ScheduleProposal,
} from "../api/proposals";
import type {
  AgentCommand,
  ChronosLogEntry,
  PendingAgentOperation,
  NewTaskInput,
  TimelineTask,
  Reminder,
  TimelineSelection,
} from "../types";
import { createReminder, deleteReminder, loadReminders } from "../api/reminders";
import { appendChronosLog, loadChronosLog } from "../api/chronosLog";

const minute = 60_000;

export function useTimelineStore(onOperationsChanged?: () => Promise<void>) {
  const [tasks, setTasks] = useState<TimelineTask[]>([]);
  const [commands, setCommands] = useState<AgentCommand[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [logs, setLogs] = useState<ChronosLogEntry[]>([]);
  const [pendingOperationCount, setPendingOperationCount] = useState(0);
  const [pendingOperations, setPendingOperations] = useState<PendingAgentOperation[]>([]);
  const [selection, setSelection] = useState<TimelineSelection | null>(null);
  const [focusTarget, setFocusTarget] = useState<number | null>(null);
  const [storageStatus, setStorageStatus] =
    useState<"loading" | "ready" | "error">("loading");
  const [storageError, setStorageError] = useState<string | null>(null);
  const hasLocalMutation = useRef(false);

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      loadTimelineTasks(), loadProposals(), loadReminders(), loadChronosLog(),
    ])
      .then(([taskResult, proposalResult, reminderResult, logResult]) => {
        if (!active) return;
        if (taskResult.status === "fulfilled") {
          if (!hasLocalMutation.current) setTasks(taskResult.value);
          setStorageStatus("ready");
        } else {
          reportStorageError(taskResult.reason);
        }
        if (proposalResult.status === "fulfilled") {
          const pending = proposalResult.value.filter(
            (proposal) => proposal.status === "pending"
              || proposal.status === "needs_clarification",
          );
          setCommands(pending.map(proposalToCommand));
        }
        if (reminderResult.status === "fulfilled") {
          setReminders(reminderResult.value);
        }
        if (logResult.status === "fulfilled") {
          setLogs(logResult.value.entries);
          setPendingOperationCount(logResult.value.pendingCount);
          setPendingOperations(logResult.value.pendingOperations);
        }
        const optionalFailures = [proposalResult, reminderResult, logResult]
          .filter((result) => result.status === "rejected")
          .map((result) => errorMessage(result.reason));
        if (taskResult.status === "fulfilled") {
          setStorageError(optionalFailures.length ? optionalFailures.join(" · ") : null);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  const sortedTasks = useMemo(
    () => tasks.slice().sort((left, right) => left.start - right.start),
    [tasks],
  );

  useEffect(() => {
    if (!selection || storageStatus === "loading") return;
    if (selection.type === "task") {
      const exists = tasks.some(
        (task) => task.id === selection.id || task.seriesId === selection.id,
      );
      if (!exists) setSelection(null);
    } else if (
      selection.type === "reminder"
      && !reminders.some((reminder) => reminder.id === selection.id)
    ) {
      setSelection(null);
    }
  }, [reminders, selection, storageStatus, tasks]);

  function addTask(input: NewTaskInput, source: TimelineTask["source"] = "user") {
    const task: TimelineTask = {
      id: crypto.randomUUID(),
      title: input.title,
      start: input.start,
      end: input.start + input.durationMinutes * minute,
      predictedEnd: input.start + input.durationMinutes * minute,
      intensity: input.intensity,
      spectrum: input.spectrum,
      fixed: input.fixed,
      type: input.type,
      source,
      recurrence: input.recurrence,
    };
    hasLocalMutation.current = true;
    setTasks((current) => [...current, task]);
    createTimelineTask(task)
      .then((storedTasks) => {
        setTasks(storedTasks);
        confirmStorage();
        void refreshOperations();
        recordLogBestEffort({
          eventType: "operation_completed",
          message: `Created ${task.title} at ${formatTime(task.start)}`,
          references: [{ type: "task", id: task.id }],
          metadata: { manual_action: "create_task" },
        });
      })
      .catch((error: unknown) => {
        setTasks((current) => current.filter((item) => item.id !== task.id));
        reportStorageError(error);
      });
    setFocusTarget(task.start);
    return task;
  }

  function updateTaskTiming(taskId: string, start: number, end: number) {
    const occurrence = sortedTasks.find((task) => task.id === taskId);
    const baseId = occurrence?.seriesId ?? taskId;
    const previousOccurrence = tasks.find(
      (task) => (task.seriesId ?? task.id) === baseId,
    );
    const previous = previousOccurrence
      ? asSeriesBase(previousOccurrence)
      : undefined;
    if (!previous || !occurrence || end <= start) return;
    const updated = updateBaseTiming(previous, occurrence, start, end);
    hasLocalMutation.current = true;
    if (!occurrence.seriesId) {
      setTasks((current) =>
        current.map((task) => (task.id === baseId ? updated : task)),
      );
    }
    saveTimelineTask(updated)
      .then((storedTasks) => {
        setTasks(storedTasks);
        confirmStorage();
        void refreshOperations();
        recordLogBestEffort({
          eventType: start === previous.start ? "manual_task_resize" : "manual_task_move",
          message: `${previous.title}: ${formatTime(previous.start)}–${formatTime(previous.end)} → ${formatTime(start)}–${formatTime(end)}`,
          references: [{ type: "task", id: baseId }],
          metadata: { previous_task: previous },
        });
      })
      .catch((error: unknown) => {
        if (!occurrence.seriesId) {
          setTasks((current) =>
            current.map((task) => (task.id === baseId ? previous : task)),
          );
        }
        reportStorageError(error);
      });
  }

  function updateTask(taskId: string, input: NewTaskInput) {
    const occurrence = sortedTasks.find((task) => task.id === taskId);
    const baseId = occurrence?.seriesId ?? taskId;
    const previousOccurrence = tasks.find(
      (task) => (task.seriesId ?? task.id) === baseId,
    );
    const previous = previousOccurrence
      ? asSeriesBase(previousOccurrence)
      : undefined;
    if (!previous || !occurrence) return;

    let start = input.start;
    if (occurrence.seriesId && input.recurrence) {
      const baseStart = new Date(previous.start);
      const editedStart = new Date(input.start);
      baseStart.setHours(
        editedStart.getHours(),
        editedStart.getMinutes(),
        editedStart.getSeconds(),
        0,
      );
      start = baseStart.getTime();
    }
    const end = start + input.durationMinutes * minute;
    const updated: TimelineTask = {
      ...previous,
      title: input.title,
      start,
      end,
      predictedEnd: end,
      intensity: input.intensity,
      spectrum: input.spectrum,
      fixed: input.fixed,
      type: input.type,
      recurrence: input.recurrence,
    };
    hasLocalMutation.current = true;
    if (!occurrence.seriesId) {
      setTasks((current) =>
        current.map((task) => (task.id === baseId ? updated : task)),
      );
    }
    saveTimelineTask(updated)
      .then((storedTasks) => {
        setTasks(storedTasks);
        confirmStorage();
        void refreshOperations();
        recordLogBestEffort({
          eventType: "operation_completed",
          message: `Updated ${previous.title} → ${updated.title}`,
          references: [{ type: "task", id: baseId }],
          metadata: { manual_action: "update_task", previous_task: previous },
        });
      })
      .catch((error: unknown) => {
        if (!occurrence.seriesId) {
          setTasks((current) =>
            current.map((task) => (task.id === baseId ? previous : task)),
          );
        }
        reportStorageError(error);
      });
    setFocusTarget(input.start);
  }

  function deleteTask(taskId: string) {
    const occurrence = sortedTasks.find((task) => task.id === taskId);
    const baseId = occurrence?.seriesId ?? taskId;
    const previousOccurrence = tasks.find(
      (task) => (task.seriesId ?? task.id) === baseId,
    );
    const previous = previousOccurrence
      ? asSeriesBase(previousOccurrence)
      : undefined;
    if (!previous) return;
    hasLocalMutation.current = true;
    setTasks((current) =>
      current.filter((task) => (task.seriesId ?? task.id) !== baseId),
    );
    deleteTimelineTask(baseId)
      .then(loadTimelineTasks)
      .then((storedTasks) => {
        setTasks(storedTasks);
        confirmStorage();
        void refreshOperations();
        recordLogBestEffort({
          eventType: "operation_completed",
          message: `Deleted ${previous.title}`,
          references: [{ type: "task", id: baseId }],
          metadata: { manual_action: "delete_task", deleted_task: previous },
        });
      })
      .catch((error: unknown) => {
        setTasks((current) =>
          current.some((task) => task.id === previous.id)
            ? current
            : [...current, previous],
        );
        reportStorageError(error);
      });
  }

  function focusTime(time: number) {
    setFocusTarget(time);
  }

  function addReminder(reminder: Reminder) {
    setReminders((current) => [...current, reminder]);
    createReminder(reminder)
      .then((stored) => {
        setReminders((current) => current.map((item) => item.id === stored.id ? stored : item));
        confirmStorage();
        void refreshOperations();
        recordLogBestEffort({
          eventType: "operation_completed",
          message: `Created reminder ${stored.title}`,
          references: [{ type: "reminder", id: stored.id }],
          metadata: { manual_action: "create_reminder" },
        });
      })
      .catch((error) => {
        setReminders((current) => current.filter((item) => item.id !== reminder.id));
        reportStorageError(error);
      });
  }

  async function runAgent(request: string) {
    try {
      const proposal = await createProposal(request, selection);
      if (
        proposal.status === "pending" ||
        proposal.status === "needs_clarification"
      ) {
        const command = proposalToCommand(proposal);
        setCommands((current) => [
          ...current.filter((item) => item.id !== proposal.id),
          command,
        ]);
        if (proposal.task) setFocusTarget(proposal.task.start);
      } else if (proposal.results.length) {
        setFocusTarget(proposal.results[0].start);
      }
      await refreshLog();
      await onOperationsChanged?.();
      confirmStorage();
    } catch (error) {
      reportStorageError(error);
      throw error;
    }
  }

  async function resolveCommand(id: string, accepted: boolean) {
    const command = commands.find((item) => item.id === id);
    if (!command) return;
    setCommands((current) =>
      current.map((item) =>
        item.id === id ? { ...item, status: accepted ? "accepted" : "rejected" } : item,
      ),
    );
    try {
      await resolveProposal(id, accepted);
      if (accepted) {
        hasLocalMutation.current = true;
        const [storedTasks, storedReminders] = await Promise.all([
          loadTimelineTasks(), loadReminders(),
        ]);
        setTasks(storedTasks);
        setReminders(storedReminders);
      }
      await refreshLog();
      await onOperationsChanged?.();
      setCommands((current) => current.filter((item) => item.id !== id));
      confirmStorage();
    } catch (error) {
      setCommands((current) =>
        current.map((item) =>
          item.id === id ? { ...item, status: "proposed" } : item,
        ),
      );
      reportStorageError(error);
    }
  }

  async function answerOperation(
    id: string,
    answer: string,
    answerSelection: TimelineSelection | null = selection,
  ) {
    const proposal = await answerClarification(id, answer, answerSelection);
    setCommands((current) => [
      ...current.filter((item) => item.id !== id),
      ...(proposal.status === "pending" || proposal.status === "needs_clarification"
        ? [proposalToCommand(proposal)]
        : []),
    ]);
    if (proposal.task) setFocusTarget(proposal.task.start);
    await refreshLog();
    await onOperationsChanged?.();
    confirmStorage();
  }

  async function restoreLog(id: string) {
    const entry = logs.find((item) => item.id === id);
    if (!entry) return;
    if (entry.operationId && entry.eventType === "operation_completed") {
      try {
        await restoreProposal(entry.operationId);
        const [storedTasks, storedReminders] = await Promise.all([
          loadTimelineTasks(),
          loadReminders(),
        ]);
        setTasks(storedTasks);
        setReminders(storedReminders);
        await refreshLog();
        await onOperationsChanged?.();
        confirmStorage();
      } catch (error) {
        reportStorageError(error);
      }
      return;
    }
    const action = entry.metadata.manual_action;
    const reference = entry.references[0];
    try {
      if (action === "create_task" && reference?.type === "task") {
        await deleteTimelineTask(reference.id);
        setTasks(await loadTimelineTasks());
      } else if (action === "create_reminder" && reference?.type === "reminder") {
        await deleteReminder(reference.id);
        setReminders(await loadReminders());
      } else if (action === "delete_task" && isTimelineTask(entry.metadata.deleted_task)) {
        setTasks(await createTimelineTask(entry.metadata.deleted_task));
      } else if (
        (action === "update_task" || entry.eventType.startsWith("manual_task_"))
        && isTimelineTask(entry.metadata.previous_task)
      ) {
        setTasks(await saveTimelineTask(entry.metadata.previous_task));
      } else {
        return;
      }
      await recordLog({
        eventType: "undo",
        message: `Undid: ${entry.message}`,
        references: entry.references,
        metadata: { restored_log_entry_id: entry.id },
      });
      confirmStorage();
    } catch (error) {
      reportStorageError(error);
    }
  }

  async function recordLog(input: Parameters<typeof appendChronosLog>[0]) {
    const entry = await appendChronosLog(input);
    setLogs((current) => [entry, ...current]);
  }

  function recordLogBestEffort(input: Parameters<typeof appendChronosLog>[0]) {
    void recordLog(input).catch((error) => {
      console.error("Chronos Log append failed", error);
    });
  }

  async function refreshLog() {
    const result = await loadChronosLog();
    setLogs(result.entries);
    setPendingOperationCount(result.pendingCount);
    setPendingOperations(result.pendingOperations);
  }

  async function refreshOperations() {
    await Promise.all([refreshLog(), onOperationsChanged?.()]);
    const proposals = await loadProposals();
    setCommands(proposals
      .filter((proposal) => proposal.status === "pending"
        || proposal.status === "needs_clarification")
      .map(proposalToCommand));
  }

  function reportStorageError(error: unknown) {
    setStorageStatus("error");
    setStorageError(errorMessage(error));
  }

  function confirmStorage() {
    setStorageStatus("ready");
    setStorageError(null);
  }

  return {
    tasks: sortedTasks,
    reminders,
    commands,
    logs,
    pendingOperationCount,
    pendingOperations,
    selection,
    storageStatus,
    storageError,
    focusTarget,
    addTask,
    addReminder,
    updateTaskTiming,
    updateTask,
    deleteTask,
    focusTime,
    selectTimeline: setSelection,
    clearSelection: () => setSelection(null),
    runAgent,
    resolveCommand,
    answerOperation,
    restoreLog,
  };
}

function isTimelineTask(value: unknown): value is TimelineTask {
  if (!value || typeof value !== "object") return false;
  const task = value as Partial<TimelineTask>;
  return typeof task.id === "string"
    && typeof task.title === "string"
    && typeof task.start === "number"
    && typeof task.end === "number";
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

const formatTime = (time: number) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(time);

function proposalToCommand(proposal: ScheduleProposal): AgentCommand {
  const change = proposal.changes[0];
  const needsClarification = proposal.status === "needs_clarification";
  const cursorTime = proposal.task?.start ?? Date.now();
  const reminderLines = proposal.reminderDrafts.map(({ reminder }) => {
    const time = reminder.trigger.type === "time"
      ? reminder.trigger.at
      : (reminder.trigger.start + reminder.trigger.end) / 2;
    return `◇ ${formatTime(time)} · ${reminder.title} · ${reminder.delivery}`;
  });
  return {
    id: proposal.id,
    cursorTime,
    title: needsClarification
      ? "Chronos needs clarification"
      : proposal.reminderDrafts.length
        ? `${proposal.reminderDrafts.length} REMINDER BEACON${proposal.reminderDrafts.length > 1 ? "S" : ""}`
      : proposal.proposedTasks.length > 1
        ? `${proposal.proposedTasks.length} TASK PLAN`
        : change ? `${change.operation.toUpperCase()} proposal` : "Schedule proposal",
    lines: needsClarification
      ? proposal.clarifications.map((item) => item.question)
      : [
      ...reminderLines,
      ...(proposal.proposedTasks.length > 1
        ? proposal.proposedTasks.map((task) => {
            const time = new Date(task.preferred_start).getTime();
            const recurrence = task.recurrence?.frequency ?? "once";
            return `${formatTime(time)} · ${task.title} · ${recurrence}`;
          })
        : proposal.task
          ? [`${formatTime(proposal.task.start)}–${formatTime(proposal.task.end)}`, proposal.task.title]
          : []),
      proposal.conflicts.length
        ? `${proposal.conflicts.length} conflict(s) reported`
        : "Planner verified",
      ...proposal.parserWarnings,
    ],
    status:
      proposal.status === "pending" || proposal.status === "needs_clarification"
        ? "proposed"
        : proposal.status === "accepted"
          ? "accepted"
          : "rejected",
    proposedTask: proposal.task ?? undefined,
    contextUsed: proposal.contextUsed.map((item) => item.content),
    canResolve: proposal.status === "pending",
  };
}

function updateBaseTiming(
  base: TimelineTask,
  occurrence: TimelineTask,
  nextStart: number,
  nextEnd: number,
) {
  if (!occurrence.seriesId) {
    return {
      ...base,
      start: nextStart,
      end: nextEnd,
      predictedEnd: nextEnd,
    };
  }

  const baseStart = new Date(base.start);
  const occurrenceStart = new Date(nextStart);
  baseStart.setHours(
    occurrenceStart.getHours(),
    occurrenceStart.getMinutes(),
    occurrenceStart.getSeconds(),
    0,
  );
  const duration = nextEnd - nextStart;
  return {
    ...base,
    start: baseStart.getTime(),
    end: baseStart.getTime() + duration,
    predictedEnd: baseStart.getTime() + duration,
  };
}

function asSeriesBase(task: TimelineTask): TimelineTask {
  if (!task.seriesId) return task;
  const duration = task.end - task.start;
  const start = task.seriesStart ?? task.start;
  return {
    ...task,
    id: task.seriesId,
    start,
    end: start + duration,
    predictedEnd: start + duration,
    seriesId: undefined,
    seriesStart: undefined,
  };
}
