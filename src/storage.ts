import type { ProgressRecord } from "./types";

const KEY = "moviebox:continue-watching";
const MAX_ITEMS = 18;

function read(): ProgressRecord[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(value) ? value.filter((item): item is ProgressRecord => typeof item === "object" && item !== null && typeof (item as ProgressRecord).key === "string") : [];
  } catch {
    return [];
  }
}

export function getProgress(): ProgressRecord[] {
  return read().sort((a, b) => b.updatedAt - a.updatedAt);
}

export function getProgressFor(key: string): ProgressRecord | null {
  return getProgress().find((item) => item.key === key) || null;
}

export function saveProgress(record: ProgressRecord): void {
  try {
    const next = [record, ...read().filter((item) => item.key !== record.key)].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_ITEMS);
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Private browsing or quota errors should never interrupt playback.
  }
}
