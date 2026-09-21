"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import NoteReader from "@/components/notebook/NoteReader";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";

/**
 * `/notebook/note?path=<vault path>` — read a generated note in the app.
 *
 * A query parameter rather than a catch-all segment, because a vault path
 * contains slashes, spaces and characters (`_1_ _2_.pptx`) that survive
 * `encodeURIComponent` far more predictably than they survive being split
 * across route segments and reassembled.
 *
 * `useSearchParams` opts a route out of static prerendering unless it sits
 * under a Suspense boundary.
 */
function Reader() {
  const path = useSearchParams().get("path");
  if (!path) {
    return <p className="text-body text-nb-faint">No note was named.</p>;
  }
  return <NoteReader path={path} />;
}

export default function NotePage() {
  return (
    <>
      <NotebookStatusLine title="Note" />
      <div className="shell">
        <Suspense fallback={<p className="text-body text-nb-faint">Opening…</p>}>
          <Reader />
        </Suspense>
      </div>
    </>
  );
}
