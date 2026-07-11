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
