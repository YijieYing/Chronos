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
              (proposal) =>
                proposal.status === "pending" ||
                proposal.status === "needs_clarification",
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
    () => tasks.slice().sort((left, right) => left.start - right.start),
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
      })
      .catch((error: unknown) => {
        if (!occurrence.seriesId) {
          setTasks((current) =>
            current.map((task) => (task.id === baseId ? previous : task)),
          );
        }
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
      if (
        proposal.status === "pending" ||
        proposal.status === "needs_clarification"
      ) {
        const command = proposalToCommand(proposal);
        setCommands((current) => [
          ...current.filter((item) => item.status !== "proposed"),
          command,
        ]);
        if (proposal.task) setFocusTarget(proposal.task.start);
      } else if (proposal.results.length) {
        setFocusTarget(proposal.results[0].start);
      }
      setLogs((current) => [...current, proposalToLog(proposal)]);
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

function proposalToCommand(proposal: ScheduleProposal): AgentCommand {
  const change = proposal.changes[0];
  const needsClarification = proposal.status === "needs_clarification";
  const cursorTime = proposal.task?.start ?? Date.now();
  return {
    id: proposal.id,
    cursorTime,
    title: needsClarification
      ? "Chronos needs clarification"
      : proposal.proposedTasks.length > 1
        ? `${proposal.proposedTasks.length} TASK PLAN`
        : change ? `${change.operation.toUpperCase()} proposal` : "Schedule proposal",
    lines: needsClarification
      ? proposal.clarifications.map((item) => item.question)
      : [
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
    response: [
      ...proposal.parserWarnings,
      proposal.explanation.join(" "),
      resultSummary,
    ].filter(Boolean).join(" "),
    status:
      proposal.status === "informational"
        ? "info"
        : proposal.status === "pending"
        ? "proposed"
        : proposal.status === "needs_clarification"
          ? "info"
          : proposal.status === "accepted"
          ? "applied"
          : proposal.status === "restored"
            ? "restored"
            : "rejected",
    addedTaskId: proposal.changes[0]?.operation === "add" ? proposal.task?.id : undefined,
    proposalId: proposal.id,
    contextUsed: proposal.contextUsed.map((item) => item.content),
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
