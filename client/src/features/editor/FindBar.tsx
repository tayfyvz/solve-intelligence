import { useEffect, useState } from "react";

import { paint } from "./highlight";
import { findMatches, scrollToPosition } from "./navigate";
import { useDocumentStore } from "../../store";
import { useEditorDoc } from "./useEditorDoc";

/** Matches `findMatches`' own default, so the "first 500" notice cannot drift from it. */
const LIMIT = 500;

/**
 * Find text in the open document, with next/previous and a live count. Read-only like the
 * outline: it scrolls and draws a decoration but never dispatches a document transaction, so
 * it cannot touch the dirty flag or move the caret out of the input.
 */
export default function FindBar() {
  const doc = useEditorDoc();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);

  const matches = doc ? findMatches(doc, query.trim(), LIMIT) : [];
  const truncated = matches.length === LIMIT;
  // Clamped rather than stored-and-corrected: the document can change under a stale index.
  const current = matches.length === 0 ? -1 : Math.min(index, matches.length - 1);

  // Lifted out of the array: depending on `matches` itself would repaint and re-scroll on
  // every render, since it is derived fresh each time.
  const from = matches[current]?.from ?? null;
  const to = matches[current]?.to ?? null;

  // Painting follows which match is current, so it belongs in an effect: editing the
  // document out from under a match would otherwise leave the band over unrelated text.
  useEffect(() => {
    const editor = useDocumentStore.getState().editor;
    // Nothing to draw: the previous run's cleanup already cleared the band, and an
    // unconditional clear would dispatch on every mount for users who never open find.
    if (!editor || editor.isDestroyed || from === null || to === null) return;
    paint(editor, { kind: "citation", from, to });
    scrollToPosition(editor, from);
    return () => {
      const live = useDocumentStore.getState().editor;
      if (live && !live.isDestroyed) paint(live, { kind: "clear" });
    };
  }, [from, to]);

  const step = (delta: number) => {
    if (matches.length === 0) return;
    setIndex((n) => (Math.min(n, matches.length - 1) + delta + matches.length) % matches.length);
  };

  return (
    <div className="flex items-center gap-2 border-b border-slate-200 bg-gradient-to-b from-white to-slate-50 px-4 py-1.5">
      <label htmlFor="find-input" className="sr-only">
        Find in document
      </label>
      <input
        id="find-input"
        type="search"
        value={query}
        placeholder="Find in document"
        onChange={(event) => {
          setQuery(event.target.value);
          setIndex(0); // a new query always starts at the first hit
        }}
        onKeyDown={(event) => {
          if (event.key !== "Enter") return;
          event.preventDefault();
          step(event.shiftKey ? -1 : 1);
        }}
        className="focus-ring min-w-0 flex-1 rounded-full border border-slate-300 bg-white px-3 py-1 text-[0.8125rem] transition-colors duration-150 placeholder:text-slate-400 hover:border-slate-400"
      />

      {/* aria-live, so the count is announced without the field reading back every key. */}
      <span aria-live="polite" className="shrink-0 text-[0.75rem] tabular-nums text-slate-500">
        {query.trim() === ""
          ? ""
          : matches.length === 0
            ? "No matches"
            : // The cap is stated, never silent: "500 of 500" would read as complete.
              `${current + 1} of ${truncated ? `${LIMIT}+` : matches.length}`}
      </span>

      <button
        type="button"
        aria-label="Previous match"
        disabled={matches.length === 0}
        onClick={() => step(-1)}
        className="focus-ring flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-slate-300 bg-white text-[0.75rem] text-slate-600 transition-colors duration-150 hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 disabled:opacity-40"
      >
        ↑
      </button>
      <button
        type="button"
        aria-label="Next match"
        disabled={matches.length === 0}
        onClick={() => step(1)}
        className="focus-ring flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-slate-300 bg-white text-[0.75rem] text-slate-600 transition-colors duration-150 hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 disabled:opacity-40"
      >
        ↓
      </button>
    </div>
  );
}
