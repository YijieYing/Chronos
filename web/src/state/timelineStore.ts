import { useEffect, useMemo, useRef, useState } from "react";
import {
  createTimelineTask,
  deleteTimelineTask,
  loadTimelineTasks,
  saveTimelineTask,
} from "../schedule/timelineApi";
import {
  createProposal,
  loadProposals,
  resolveProposal,
  restoreProposal,
  type ScheduleProposal,
} from "../api/proposals";
import type {
  AgentCommand,
  ChronosLogEntry,
  NewTaskInput,
  TimelineTask,
} from "../types";

const minute = 60_000;

export function useTimelineStore() {
  const [tasks, setTasks] = useState<TimelineTask[]>([]);
  const [commands, setCommands] = useState<AgentCommand[]>([]);
  const [logs, setLogs] = useState<ChronosLogEntry[]>([]);
  const [focusTarget, setFocusTarget] = useState<number | null>(null);
  const [storageStatus, setStorageStatus] =
    useState<"loading" | "ready" | "error">("loading");
  const [storageError, setStorageError] = useState<string | null>(null);
  const hasLocalMutation = useRef(false);

  useEffect(() => {
    let active = true;
    Promise.all([loadTimelineTasks(), loadProposals()])
      .then(([storedTasks, storedProposals]) => {
        if (!active) return;
        if (!hasLocalMutation.current) setTasks(storedTasks);
        setCommands(
          storedProposals
            .filter(
              (proposal): proposal is ScheduleProposal & { task: TimelineTask } =>
                proposal.status === "pending" && proposal.task !== null,
            )
            .map(proposalToCommand),
        );
        setLogs(storedProposals.map(proposalToLog));
        setStorageStatus("ready");
        setStorageError(null);
      })
      .catch((error: unknown) => {
        if (!active) return;
        reportStorageError(error);
      });
    return () => {
      active = false;
    };
  }, []);

  const sortedTasks = useMemo(
    () => expandRecurringTasks(tasks).sort((left, right) => left.start - right.start),
    [tasks],
  );

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
      })
      .catch((error: unknown) => {
        setTasks((current) => current.filter((item) => item.id !== task.id));
        reportStorageError(error);
      });
    setFocusTarget(task.start);
    setLogs((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        time: Date.now(),
        request: source === "agent" ? "Agent task insertion" : "Create task",
        response: `Added ${task.title} at ${formatTime(task.start)}`,
        status: "applied",
        addedTaskId: task.id,
      },
    ]);
    return task;
  }

  function updateTaskTiming(taskId: string, start: number, end: number) {
    const occurrence = sortedTasks.find((task) => task.id === taskId);
    const baseId = occurrence?.seriesId ?? taskId;
    const previous = tasks.find((task) => task.id === baseId);
    if (!previous || !occurrence || end <= start) return;
    const updated = updateBaseTiming(previous, occurrence, start, end);
    hasLocalMutation.current = true;
    setTasks((current) =>
      current.map((task) => (task.id === baseId ? updated : task)),
    );
    saveTimelineTask(updated)
      .then((storedTasks) => {
        setTasks(storedTasks);
        confirmStorage();
      })
      .catch((error: unknown) => {
        setTasks((current) =>
          current.map((task) => (task.id === baseId ? previous : task)),
        );
        reportStorageError(error);
      });
    setLogs((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        time: Date.now(),
        request: "Overview timeline adjustment",
        response: `${previous.title}: ${formatTime(previous.start)}–${formatTime(previous.end)} → ${formatTime(start)}–${formatTime(end)}`,
        status: "applied",
        changedTaskId: baseId,
        previousTask: previous,
      },
    ]);
  }

  function updateTask(taskId: string, input: NewTaskInput) {
    const occurrence = sortedTasks.find((task) => task.id === taskId);
    const baseId = occurrence?.seriesId ?? taskId;
    const previous = tasks.find((task) => task.id === baseId);
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
    setTasks((current) =>
      current.map((task) => (task.id === baseId ? updated : task)),
    );
    saveTimelineTask(updated)
      .then((storedTasks) => {
        setTasks(storedTasks);
        confirmStorage();
      })
      .catch((error: unknown) => {
        setTasks((current) =>
          current.map((task) => (task.id === baseId ? previous : task)),
        );
        reportStorageError(error);
      });
    setFocusTarget(input.start);
    setLogs((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        time: Date.now(),
        request: previous.recurrence ? "Edit recurring task series" : "Edit task",
        response: `Updated ${previous.title} → ${updated.title}`,
        status: "applied",
        changedTaskId: baseId,
        previousTask: previous,
      },
    ]);
  }

  function deleteTask(taskId: string) {
    const occurrence = sortedTasks.find((task) => task.id === taskId);
    const baseId = occurrence?.seriesId ?? taskId;
    const previous = tasks.find((task) => task.id === baseId);
    if (!previous) return;
    hasLocalMutation.current = true;
    setTasks((current) => current.filter((task) => task.id !== baseId));
    deleteTimelineTask(baseId)
      .then(loadTimelineTasks)
      .then((storedTasks) => {
        setTasks(storedTasks);
        confirmStorage();
      })
      .catch((error: unknown) => {
        setTasks((current) =>
          current.some((task) => task.id === previous.id)
            ? current
            : [...current, previous],
        );
        reportStorageError(error);
      });
    setLogs((current) => [
      ...current,
      {
        id: crypto.randomUUID(),
        time: Date.now(),
        request: previous.recurrence ? "Delete recurring task series" : "Delete task",
        response: `Deleted ${previous.title}`,
        status: "applied",
        deletedTask: previous,
      },
    ]);
  }

  function focusTime(time: number) {
    setFocusTarget(time);
  }

  async function runAgent(request: string) {
    try {
      const proposal = await createProposal(request);
      if (proposal.status === "pending" && proposal.task) {
        const command = proposalToCommand(proposal);
        setCommands((current) => [
          ...current.filter((item) => item.status !== "proposed"),
          command,
        ]);
        setFocusTarget(proposal.task.start);
      } else if (proposal.results.length) {
        setFocusTarget(proposal.results[0].start);
      }
      setLogs((current) => [...current, proposalToLog(proposal)]);
      confirmStorage();
    } catch (error) {
      reportStorageError(error);
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
        setTasks(await loadTimelineTasks());
      }
      setLogs((current) =>
        current.map((item) =>
          item.id === id ? { ...item, status: accepted ? "applied" : "rejected" } : item,
        ),
      );
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

  async function restoreLog(id: string) {
    const entry = logs.find((item) => item.id === id);
    if (!entry) return;
    if (entry.proposalId) {
      try {
        await restoreProposal(entry.proposalId);
        setTasks(await loadTimelineTasks());
        setLogs((current) =>
          current.map((item) =>
            item.id === id ? { ...item, status: "restored" } : item,
          ),
        );
        confirmStorage();
      } catch (error) {
        reportStorageError(error);
      }
      return;
    }
    if (entry.addedTaskId) {
      const addedTask = tasks.find((task) => task.id === entry.addedTaskId);
      setTasks((current) => current.filter((task) => task.id !== entry.addedTaskId));
      hasLocalMutation.current = true;
      deleteTimelineTask(entry.addedTaskId)
        .then(loadTimelineTasks)
        .then((storedTasks) => {
          setTasks(storedTasks);
          confirmStorage();
        })
        .catch((error: unknown) => {
          if (addedTask) {
            setTasks((current) =>
              current.some((task) => task.id === addedTask.id)
                ? current
                : [...current, addedTask],
            );
          }
          reportStorageError(error);
        });
    }
    if (entry.changedTaskId && entry.previousTask) {
      const changedTask = tasks.find((task) => task.id === entry.changedTaskId);
      setTasks((current) =>
        current.map((task) =>
          task.id === entry.changedTaskId ? entry.previousTask! : task,
        ),
      );
      hasLocalMutation.current = true;
      saveTimelineTask(entry.previousTask)
        .then((storedTasks) => {
          setTasks(storedTasks);
          confirmStorage();
        })
        .catch((error: unknown) => {
          if (changedTask) {
            setTasks((current) =>
              current.map((task) =>
                task.id === changedTask.id ? changedTask : task,
              ),
            );
          }
          reportStorageError(error);
        });
    }
    if (entry.deletedTask) {
      setTasks((current) =>
        current.some((task) => task.id === entry.deletedTask!.id)
          ? current
          : [...current, entry.deletedTask!],
      );
      hasLocalMutation.current = true;
      saveTimelineTask(entry.deletedTask)
        .then((storedTasks) => {
          setTasks(storedTasks);
          confirmStorage();
        })
        .catch((error: unknown) => {
          setTasks((current) =>
            current.filter((task) => task.id !== entry.deletedTask!.id),
          );
          reportStorageError(error);
        });
    }
    setLogs((current) =>
      current.map((item) => (item.id === id ? { ...item, status: "restored" } : item)),
    );
  }

  function reportStorageError(error: unknown) {
    setStorageStatus("error");
    setStorageError(error instanceof Error ? error.message : String(error));
  }

  function confirmStorage() {
    setStorageStatus("ready");
    setStorageError(null);
  }

  return {
    tasks: sortedTasks,
    commands,
    logs,
    storageStatus,
    storageError,
    focusTarget,
    addTask,
    updateTaskTiming,
    updateTask,
    deleteTask,
    focusTime,
    runAgent,
    resolveCommand,
    restoreLog,
  };
}

const formatTime = (time: number) =>
  new Intl.DateTimeFormat("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(time);

const day = 24 * 60 * minute;

function proposalToCommand(proposal: ScheduleProposal): AgentCommand {
  if (!proposal.task) throw new Error("Mutation proposal is missing its task projection");
  const change = proposal.changes[0];
  return {
    id: proposal.id,
    cursorTime: proposal.task.start,
    title: change ? `${change.operation.toUpperCase()} proposal` : "Schedule proposal",
    lines: [
      `${formatTime(proposal.task.start)}–${formatTime(proposal.task.end)}`,
      proposal.task.title,
      proposal.conflicts.length
        ? `${proposal.conflicts.length} conflict(s) reported`
        : "Planner verified",
    ],
    status:
      proposal.status === "pending"
        ? "proposed"
        : proposal.status === "accepted"
          ? "accepted"
          : "rejected",
    proposedTask: proposal.task,
  };
}

function proposalToLog(proposal: ScheduleProposal): ChronosLogEntry {
  const resultSummary = proposal.results.length
    ? proposal.results
        .map((task) => `${formatTime(task.start)} ${task.title}`)
        .join("；")
    : "";
  return {
    id: proposal.id,
    time: proposal.createdAt,
    request: proposal.request,
    response: [proposal.explanation.join(" "), resultSummary].filter(Boolean).join(" "),
    status:
      proposal.status === "informational"
        ? "info"
        : proposal.status === "pending"
        ? "proposed"
        : proposal.status === "accepted"
          ? "applied"
          : proposal.status === "restored"
            ? "restored"
            : "rejected",
    addedTaskId: proposal.changes[0]?.operation === "add" ? proposal.task?.id : undefined,
    proposalId: proposal.id,
  };
}

function expandRecurringTasks(tasks: TimelineTask[]) {
  const rangeStart = Date.now() - day;
  const rangeEnd = Date.now() + 90 * day;
  return tasks.flatMap((task) => {
    if (!task.recurrence) return [task];
    const duration = task.end - task.start;
    const predictedDuration = task.predictedEnd - task.start;
    const baseTime = new Date(task.start);
    const cursor = new Date(rangeStart);
    cursor.setHours(0, 0, 0, 0);
    const instances: TimelineTask[] = [];

    while (cursor.getTime() <= rangeEnd) {
      const occurrence = new Date(cursor);
      occurrence.setHours(
        baseTime.getHours(),
        baseTime.getMinutes(),
        baseTime.getSeconds(),
        0,
      );
      const occurrenceStart = occurrence.getTime();
      const matches =
        task.recurrence.frequency === "daily" ||
        task.recurrence.weekdays.includes(occurrence.getDay());
      if (matches && occurrenceStart >= task.start && occurrenceStart <= rangeEnd) {
        instances.push({
          ...task,
          id: `${task.id}::${occurrenceStart}`,
          seriesId: task.id,
          start: occurrenceStart,
          end: occurrenceStart + duration,
          predictedEnd: occurrenceStart + predictedDuration,
          scheduled:
            occurrenceStart === task.start ? task.scheduled : false,
          unscheduledReason:
            occurrenceStart === task.start
              ? task.unscheduledReason
              : "plan_not_generated",
        });
      }
      cursor.setDate(cursor.getDate() + 1);
    }
    return instances;
  });
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
