import { useEffect, useState } from "react";

import { paint } from "../ai/highlight";
import { findMatches, scrollToPosition } from "../ai/navigate";
import { useDocumentStore } from "../store";
import { useEditorDoc } from "../useEditorDoc";

/** Matches `findMatches`' own default, so the "first 500" notice cannot drift from it. */
const LIMIT = 500;

/**
 * Find text in the open document, with next/previous and a live count.
 *
 * Read-only in exactly the same way as the outline: it scrolls and it draws a decoration,
 * and it never dispatches a document transaction, so it cannot touch the dirty flag or
 * move the caret out of the input the user is typing in.
 */
export default function FindBar() {
  const doc = useEditorDoc();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);

  const matches = doc ? findMatches(doc, query.trim(), LIMIT) : [];
  const truncated = matches.length === LIMIT;
  // Clamped rather than stored-and-corrected: the document can change under an index the
  // user set three keystrokes ago, and a stale index would highlight nothing at all.
  const current = matches.length === 0 ? -1 : Math.min(index, matches.length - 1);

  // The two numbers the effect actually depends on, lifted out of the array. Depending
  // on `matches` itself would repaint and re-scroll on every keystroke anywhere in the
  // document, because a fresh array is derived on every render.
  const from = matches[current]?.from ?? null;
  const to = matches[current]?.to ?? null;

  // Painting is a side effect of which match is current, so it belongs in an effect
  // rather than in the click handlers — otherwise editing the document out from under a
  // match leaves the old band drawn over unrelated text.
  useEffect(() => {
    const editor = useDocumentStore.getState().editor;
    // No match: nothing to draw, and no transaction to spend. The PREVIOUS run's cleanup
    // has already cleared whatever was drawn, so there is no stale band to worry about —
    // and an unconditional clear here would dispatch on every mount, for every user who
    // never touches the find box.
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
    <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-1.5">
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
        className="focus-ring min-w-0 flex-1 rounded-md border border-slate-300 px-2 py-1 text-[0.8125rem]"
      />

      {/* aria-live, so a screen reader hears the count change without the field
          announcing every character typed into it. */}
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
        className="focus-ring shrink-0 rounded-md border border-slate-300 px-2 py-1 text-[0.75rem] text-slate-700 transition-colors duration-150 hover:bg-slate-100 disabled:opacity-40"
      >
        ↑
      </button>
      <button
        type="button"
        aria-label="Next match"
        disabled={matches.length === 0}
        onClick={() => step(1)}
        className="focus-ring shrink-0 rounded-md border border-slate-300 px-2 py-1 text-[0.75rem] text-slate-700 transition-colors duration-150 hover:bg-slate-100 disabled:opacity-40"
      >
        ↓
      </button>
    </div>
  );
}
