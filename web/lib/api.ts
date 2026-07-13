"use client";

import useSWR from "swr";

/** Shared JSON fetcher for SWR — throws on non-2xx so errors surface in hooks. */
export async function fetcher<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed: ${response.status}`);
  }
  return response.json();
}

export interface NoteInfo {
  path: string;
  title: string;
  folder: string;
  modified: string;
}

/** All non-private notes in the vault, newest first. */
export function useNotes() {
  return useSWR<NoteInfo[]>("/api/notes", fetcher);
}

export interface JournalProject {
  slug: string;
  title: string;
  updated: string;
  sessions: number;
  open_threads: number;
  path: string;
}

export interface JournalSession {
  date: string;
  project: string;
  branch: string | null;
  files: number;
  has_narrative: boolean;
  path: string;
}

export interface JournalNote {
  path: string;
  markdown: string;
  obsidian_uri: string;
}

/** Dev-journal projects (90-Meta/projects), most recently updated first. */
export function useJournalProjects() {
  return useSWR<JournalProject[]>("/api/journal/projects", fetcher);
}

/** Dev-journal sessions, newest first, optionally scoped to one project. */
export function useJournalSessions(project?: string) {
  const query = project ? `?project=${encodeURIComponent(project)}` : "";
  return useSWR<JournalSession[]>(`/api/journal/sessions${query}`, fetcher);
}

/** One journal note's raw markdown + obsidian:// deep link. */
export function useJournalNote(path?: string) {
  return useSWR<JournalNote>(
    path ? `/api/journal/note?path=${encodeURIComponent(path)}` : null,
    fetcher,
  );
}

export interface InsightsSummary {
  completion_trend: { date: string; completed: number }[];
  overdue: { date: string; count: number }[];
  calendar: { date: string; event_hours: number; focus_hours: number }[];
  study: { streak_days: number; courses: { course: string; attempts: { date: string; pct: number }[] }[] };
  configured: { gcal: boolean };
}

/** Insights rollup for stat tiles and charts. */
export function useInsights() {
  return useSWR<InsightsSummary>("/api/insights", fetcher);
}
