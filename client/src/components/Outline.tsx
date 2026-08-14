import { useState } from "react";

import { paint } from "../ai/highlight";
import { documentOutline, scrollToPosition, type OutlineEntry } from "../ai/navigate";
import { useDocumentStore } from "../store";
import { useEditorDoc } from "../useEditorDoc";

/** How long a jumped-to section stays highlighted, matching the citation flash. */
const FLASH_MS = 1_500;

/**
 * Above this, a claim group starts collapsed. A patent with six claims should just show
 * them; a patent with sixty must not bury its five section headings under them, which is
 * exactly what a flat 66-row list did.
 */
const AUTO_COLLAPSE_ABOVE = 8;

/** Headings indent by level; claims sit one step in from the heading they belong to. */
const INDENT = ["", "pl-0", "pl-3", "pl-5", "pl-6", "pl-7", "pl-8"];

/**
 * A heading and the claims that follow it. Claims belong to the section they are under —
 * which in practice is always "Claims" — so the outline reads like the document rather
 * than like two lists the reader has to correlate by eye.
 */
interface Group {
  heading: OutlineEntry | null; // null for anything before the first heading
  claims: OutlineEntry[];
}

function groups(entries: OutlineEntry[]): Group[] {
  const out: Group[] = [];
  for (const entry of entries) {
    if (entry.kind === "heading" || out.length === 0) {
      out.push({ heading: entry.kind === "heading" ? entry : null, claims: [] });
    }
    if (entry.kind === "claim") out[out.length - 1].claims.push(entry);
  }
  return out;
}

/**
 * The document map: every heading, and every claim under it, each one a button that
 * scrolls the editor to it.
 *
 * This is the answer to "claim 60 is 43 screens down". It is deliberately NOT a page
 * view: it reads `editor.state.doc` and calls `scrollIntoView`, so the editor stays
 * uncontrolled (invariant 7) and selection, undo and the AI's claim resolution are
 * untouched — none of which survives paginating a single contenteditable.
 */
export default function Outline() {
  const doc = useEditorDoc();
  const [filter, setFilter] = useState("");
  /** Which claim groups the user has toggled AWAY from their default. */
  const [toggled, setToggled] = useState<Record<number, boolean>>({});

  const needle = filter.trim().toLowerCase();
  const all = doc ? documentOutline(doc) : [];
  const matches = (entry: OutlineEntry) => entry.label.toLowerCase().includes(needle);
  const visible = needle ? all.filter(matches) : all;
  const sections = groups(visible);

  const jump = (from: number, to: number) => {
    const editor = useDocumentStore.getState().editor;
    if (!editor || editor.isDestroyed) return;
    if (!scrollToPosition(editor, from)) return;
    // Cosmetic only — `paint` dispatches a meta-only transaction, so `docChanged` is
    // false and this can never reach the dirty flag.
    paint(editor, { kind: "citation", from, to });
    window.setTimeout(() => {
      const live = useDocumentStore.getState().editor;
      if (live && !live.isDestroyed) paint(live, { kind: "clear" });
    }, FLASH_MS);
  };

  const row = (entry: OutlineEntry, className: string) => (
    <li key={`${entry.kind}:${entry.from}`}>
      <button
        type="button"
        onClick={() => jump(entry.from, entry.to)}
        className={`focus-ring w-full truncate rounded-md px-1.5 py-1 text-left text-[0.8125rem] transition-colors duration-150 hover:bg-indigo-50 hover:text-indigo-800 ${className}`}
      >
        {entry.label}
      </button>
    </li>
  );

  return (
    <section aria-labelledby="outline-heading" className="flex min-h-0 flex-col">
      <div className="flex items-center justify-between gap-2 pb-1.5">
        <h2
          id="outline-heading"
          className="text-[0.6875rem] font-semibold uppercase tracking-wide text-slate-500"
        >
          Outline
        </h2>
        {all.length > 0 && (
          <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-[0.6875rem] font-medium tabular-nums text-slate-500">{all.length}</span>
        )}
      </div>

      {all.length === 0 ? (
        <p className="px-1 text-[0.8125rem] text-slate-500">
          {doc ? "No headings or claims yet." : "Open a patent to see its outline."}
        </p>
      ) : (
        <>
          <label htmlFor="outline-filter" className="sr-only">
            Filter outline
          </label>
          <input
            id="outline-filter"
            type="search"
            value={filter}
            placeholder="Filter…"
            onChange={(event) => setFilter(event.target.value)}
            className="focus-ring mb-1.5 w-full rounded-full border border-slate-300 bg-white px-3 py-1 text-[0.8125rem] transition-colors duration-150 placeholder:text-slate-400 hover:border-slate-400"
          />

          {/* Bounded and scrollable, like the nested version list: sixty claims must not
              push the patent tree off the rail. `vh` so a taller window shows more. */}
          <nav
            aria-label="Document outline"
            className="min-h-0 overflow-y-auto"
            style={{ maxHeight: "min(30rem, 48vh)" }}
          >
            {visible.length === 0 ? (
              <p className="px-1 py-2 text-[0.8125rem] text-slate-500">Nothing matches “{filter}”.</p>
            ) : (
              <ul className="space-y-0.5">
                {sections.map((group, index) => {
                  // Filtering is itself a request to see what matched, so a filtered list
                  // is always open; otherwise the user's own toggle wins, and failing that
                  // the size rule decides.
                  const open =
                    needle !== "" ||
                    (toggled[index] ?? group.claims.length <= AUTO_COLLAPSE_ABOVE);
                  return (
                    <li key={group.heading ? `h:${group.heading.from}` : `top:${index}`}>
                      <ul className="space-y-0.5">
                        {group.heading &&
                          row(
                            group.heading,
                            `font-semibold text-slate-800 ${INDENT[group.heading.level] ?? "pl-8"}`,
                          )}

                        {group.claims.length > 0 && (
                          <>
                            <li>
                              <button
                                type="button"
                                aria-expanded={open}
                                onClick={() =>
                                  setToggled((t) => ({ ...t, [index]: !open }))
                                }
                                className="focus-ring flex w-full items-center gap-1 rounded-md px-1.5 py-1 pl-3 text-left text-[0.75rem] text-slate-500 transition-colors duration-150 hover:bg-slate-100"
                              >
                                <span aria-hidden="true">{open ? "▾" : "▸"}</span>
                                {group.claims.length}{" "}
                                {group.claims.length === 1 ? "claim" : "claims"}
                              </button>
                            </li>
                            {open && group.claims.map((claim) => row(claim, "pl-6 text-slate-600"))}
                          </>
                        )}
                      </ul>
                    </li>
                  );
                })}
              </ul>
            )}
          </nav>
        </>
      )}
    </section>
  );
}
