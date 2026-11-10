export const readingStates = ["unseen", "seen", "skimmed", "deep_read"] as const;
export const readingStateNames = { unseen: "未看", seen: "已看", skimmed: "粗看", deep_read: "细看" } as const;
export type LearningAnalysis = {
  as_of: string;
  total_notes: number;
  recorded_notes: number;
  unrecorded_notes: number;
  states: Record<typeof readingStates[number], number>;
  completed_notes: number;
  completed_last_7_days: number;
  completed_last_30_days: number;
  reminders: { due: number; upcoming: number };
};

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("LEARNING_ANALYSIS_INVALID");
  return value as Record<string, unknown>;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw new Error("LEARNING_ANALYSIS_INVALID");
  return value;
}

export function parseLearningAnalysis(value: unknown): LearningAnalysis {
  const item = object(value), states = object(item.states), reminders = object(item.reminders);
  if (typeof item.as_of !== "string" || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(item.as_of) || !Number.isFinite(Date.parse(item.as_of))) throw new Error("LEARNING_ANALYSIS_INVALID");
  const result: LearningAnalysis = {
    as_of: item.as_of,
    total_notes: count(item.total_notes), recorded_notes: count(item.recorded_notes), unrecorded_notes: count(item.unrecorded_notes),
    states: { unseen: count(states.unseen), seen: count(states.seen), skimmed: count(states.skimmed), deep_read: count(states.deep_read) },
    completed_notes: count(item.completed_notes), completed_last_7_days: count(item.completed_last_7_days), completed_last_30_days: count(item.completed_last_30_days),
    reminders: { due: count(reminders.due), upcoming: count(reminders.upcoming) },
  };
  if (result.total_notes !== result.recorded_notes + result.unrecorded_notes
    || result.total_notes !== readingStates.reduce((sum, state) => sum + result.states[state], 0)
    || result.unrecorded_notes > result.states.unseen
    || result.completed_notes > result.recorded_notes
    || result.completed_last_7_days > result.completed_notes
    || result.completed_last_30_days > result.completed_notes
    || result.reminders.due + result.reminders.upcoming > result.recorded_notes - result.states.deep_read) throw new Error("LEARNING_ANALYSIS_INVALID");
  return result;
}
