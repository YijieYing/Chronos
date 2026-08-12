import { apiRequest } from "./client";
import type { Reminder } from "../types";

interface ReminderPayload extends Omit<Reminder, "createdAt"> {
  created_at: string;
}

export async function loadReminders(): Promise<Reminder[]> {
  const result = await apiRequest<{ reminders: ReminderPayload[] }>(
    "/api/v1/reminders",
  );
  return result.reminders.map(fromPayload);
}

export async function createReminder(reminder: Reminder): Promise<Reminder> {
  const result = await apiRequest<ReminderPayload>("/api/v1/reminders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(reminder),
  });
  return fromPayload(result);
}

export async function updateReminderStatus(
  id: string,
  status: Reminder["status"],
): Promise<Reminder> {
  const result = await apiRequest<ReminderPayload>(
    `/api/v1/reminders/${encodeURIComponent(id)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    },
  );
  return fromPayload(result);
}

const fromPayload = (value: ReminderPayload): Reminder => ({
  ...value,
  createdAt: new Date(value.created_at).getTime(),
});
