import { useState } from "react";

import DeleteVersionDialog from "./DeleteVersionDialog";
import Pager from "./Pager";
import Timestamp from "./Timestamp";
import TreeRow from "./TreeRow";
import type { DocumentSummary, VersionSummary } from "../types";

/** Only one name can be under the cursor at a time, so one slot covers both levels. */
type Editing = { kind: "patent"; id: number } | { kind: "version"; n: number } | null;

/** The version behind the confirmation dialog, if one is open. */
type Deleting = { n: number; name: string } | null;

export interface PatentTreeProps {
  documents: DocumentSummary[];
  documentsTotal: number;
  documentsOffset: number;
  documentsLimit: number;
  /** The open patent: the one row that expands. */
  selectedId: number | null;

  /** Every version loaded so far for the open patent, newest first. */
  versions: VersionSummary[];
  versionsTotal: number;
  selectedVersion: number | null;

  /** True while the editor pane is busy: a second selection would be discarded. */
  selectDisabled: boolean;
  /** True while a list page is in flight — paging and "show more" only. */
  listBusy: boolean;

  onSelectDocument(id: number): void;
  onSelectVersion(n: number): void;
  onCreate(): void;
  /** Opens the import dialog. A DIFFERENT job from the chat panel's .txt drop zone,
   *  which attaches a file as reference material and never creates anything. */
  onImport(): void;
  onRenameDocument(id: number, title: string): Promise<string | null>;
  onRenameVersion(n: number, name: string): Promise<string | null>;
  /** Refused (409) if `n` is the document's only version — see `versionsTotal`
   *  gating below, which keeps the UI from ever reaching that click. */
  onDeleteVersion(n: number): Promise<string | null>;
  onPageDocuments(offset: number): void;
  /** Appends the next page of versions to the ones already shown. */
  onShowMoreVersions(): void;
}

/** Tall enough for five versions, short enough that 200 cannot eat the rail. */
// Bounded so a patent with 200 versions cannot push the rest of the tree off
// screen, but tall enough to use the sidebar: a fixed 13rem showed four rows
// with ~600px of empty panel underneath. `vh` lets a taller window show more.
const VERSIONS_MAX_HEIGHT = "min(28rem, 45vh)";

/**
 * The sidebar: patents, with the open one expanded to show its versions nested
 * beneath it.
 *
 * Two flat panels stacked in the same rail did not say that a version belongs to
 * a patent — they read as two unrelated lists that happened to share a column.
 *
 * The two levels page differently on purpose. Patents keep a prev/next pager:
 * it is a flat list and "where am I" is a fair question. Versions *accumulate*
 * ("show more"), because a pager inside a tree branch is two navigations in one
 * control, and losing version 40 from view to reach version 41 is not what the
 * user asked for. The nested list scrolls inside a capped height so a patent
 * with 200 versions cannot push the rest of the tree off screen.
 */
export default function PatentTree({
  documents,
  documentsTotal,
  documentsOffset,
  documentsLimit,
  selectedId,
  versions,
  versionsTotal,
  selectedVersion,
  selectDisabled,
  listBusy,
  onSelectDocument,
  onSelectVersion,
  onCreate,
  onImport,
  onRenameDocument,
  onRenameVersion,
  onDeleteVersion,
  onPageDocuments,
  onShowMoreVersions,
}: PatentTreeProps) {
  const [editing, setEditing] = useState<Editing>(null);
  const [deleting, setDeleting] = useState<Deleting>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const more = versions.length < versionsTotal;

  return (
    <section className="flex min-h-0 flex-1 flex-col" aria-labelledby="patents-heading">
      <div className="flex items-center justify-between gap-2 pb-1.5">
        <h2
          id="patents-heading"
          className="text-[0.6875rem] font-semibold uppercase tracking-wide text-slate-500"
        >
          Patents
        </h2>
        {/* Gated like the rows below: creating opens the new patent, so it is a
            selection. Ungated, pressing Save then New patent opens the dirty
            dialog with all four buttons disabled — a modal the user cannot
            answer. */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            disabled={selectDisabled}
            onClick={onCreate}
            className="focus-ring flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[0.6875rem] font-medium text-slate-700 shadow-sm transition-all duration-150 hover:-translate-y-px hover:border-indigo-300 hover:text-indigo-700 hover:shadow disabled:translate-y-0 disabled:opacity-50 disabled:shadow-none"
          >
            <span aria-hidden="true">+</span> New patent
          </button>
          {/* Gated identically, and for the same reason: an import opens what it
              creates, so it is a selection. */}
          <button
            type="button"
            disabled={selectDisabled}
            onClick={onImport}
            className="focus-ring flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[0.6875rem] font-medium text-slate-700 shadow-sm transition-all duration-150 hover:-translate-y-px hover:border-indigo-300 hover:text-indigo-700 hover:shadow disabled:translate-y-0 disabled:opacity-50 disabled:shadow-none"
          >
            <span aria-hidden="true">↥</span> Import .txt
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {documents.length === 0 ? (
          <p className="px-1 py-2 text-[0.8125rem] text-slate-500">
            No patents yet. Create one to get started.
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {documents.map((document) => {
              const open = document.id === selectedId;
              return (
                <li key={document.id}>
                  <TreeRow
                    label={document.title}
                    meta={
                      <>
                        {document.version_count}{" "}
                        {document.version_count === 1 ? "version" : "versions"} ·{" "}
                        <Timestamp stamp={document.updated_at} />
                      </>
                    }
                    // Same pill, same colour, as the version-level "Open" badge
                    // below: one visual language for "this is the open thing",
                    // so a reader never has to guess whether the patent or only
                    // one of its versions is what's on screen. Nesting supplies
                    // the rest — the version badge sits inside the expanded,
                    // "Open"-tagged patent, so the two read as one fact, not two.
                    badge={
                      open ? (
                        <span className="shrink-0 rounded-full bg-gradient-to-r from-sky-600 to-sky-500 px-1.5 text-[0.625rem] font-semibold uppercase tracking-wide leading-4 text-white shadow-sm shadow-sky-600/30">
                          Open
                        </span>
                      ) : undefined
                    }
                    selected={open}
                    expanded={open}
                    disabled={selectDisabled}
                    renameLabel={`Rename patent "${document.title}"`}
                    editing={editing?.kind === "patent" && editing.id === document.id}
                    onSelect={() => onSelectDocument(document.id)}
                    onEdit={() => setEditing({ kind: "patent", id: document.id })}
                    onCancelEdit={() => setEditing(null)}
                    onRename={async (title) => {
                      const message = await onRenameDocument(document.id, title);
                      if (!message) setEditing(null);
                      return message;
                    }}
                  />

                  {open && (
                    // Indented and rule-marked, so the versions read as part of
                    // the patent above them rather than as more patents.
                    // The rule is sky, matching the open row above it: the
                    // branch and its parent are one selected thing.
                    <div className="ml-3 mt-0.5 border-l-2 border-sky-200/80 pl-2">
                      {versions.length === 0 ? (
                        <p className="px-1 py-1 text-[0.6875rem] text-slate-500">
                          No versions to show.
                        </p>
                      ) : (
                        <ul
                          aria-label={`Versions of "${document.title}"`}
                          style={{ maxHeight: VERSIONS_MAX_HEIGHT, scrollbarGutter: "stable" }}
                          className="flex flex-col gap-0.5 overflow-y-auto"
                        >
                          {versions.map((version) => (
                            <li key={version.version_number}>
                              <TreeRow
                                label={version.name}
                                meta={
                                  <>
                                    v{version.version_number} · saved{" "}
                                    <Timestamp stamp={version.updated_at} />
                                  </>
                                }
                                badge={
                                  version.version_number === selectedVersion ? (
                                    <span className="shrink-0 rounded-full bg-gradient-to-r from-sky-600 to-sky-500 px-1.5 text-[0.625rem] font-semibold uppercase tracking-wide leading-4 text-white shadow-sm shadow-sky-600/30">
                                      Open
                                    </span>
                                  ) : undefined
                                }
                                selected={version.version_number === selectedVersion}
                                disabled={selectDisabled}
                                renameLabel={`Rename version ${version.version_number}`}
                                editing={
                                  editing?.kind === "version" &&
                                  editing.n === version.version_number
                                }
                                onSelect={() => onSelectVersion(version.version_number)}
                                onEdit={() =>
                                  setEditing({ kind: "version", n: version.version_number })
                                }
                                onCancelEdit={() => setEditing(null)}
                                onRename={async (name) => {
                                  const message = await onRenameVersion(
                                    version.version_number,
                                    name,
                                  );
                                  if (!message) setEditing(null);
                                  return message;
                                }}
                                // Omitted entirely when this is the document's
                                // only version: deleting it would always 409,
                                // so there is no click to offer.
                                deleteLabel={
                                  versionsTotal > 1
                                    ? `Delete version ${version.version_number}`
                                    : undefined
                                }
                                onDelete={
                                  versionsTotal > 1
                                    ? () => {
                                        setDeleteError(null);
                                        setDeleting({
                                          n: version.version_number,
                                          name: version.name,
                                        });
                                      }
                                    : undefined
                                }
                              />
                            </li>
                          ))}
                        </ul>
                      )}

                      {/* Below the scroll area, not inside it: a control the user
                          has to scroll to find is one they do not know exists. */}
                      {versions.length > 0 && (
                        <div className="flex items-center justify-between gap-2 pt-1 text-[0.6875rem] text-slate-500">
                          {/* Shown even when everything is loaded: the list
                              scrolls, so without a count a full list looks
                              identical to a truncated one. */}
                          <span aria-live="polite">
                            {more
                              ? `${versions.length} of ${versionsTotal}`
                              : `${versionsTotal} ${versionsTotal === 1 ? "version" : "versions"}`}
                          </span>
                          {more && (
                            <button
                              type="button"
                              disabled={listBusy}
                              onClick={onShowMoreVersions}
                              className="focus-ring rounded-md border border-slate-200 bg-white px-1.5 py-0.5 font-medium text-slate-700 transition-colors duration-150 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              Show more versions
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <Pager
        noun="patents"
        total={documentsTotal}
        offset={documentsOffset}
        limit={documentsLimit}
        busy={listBusy}
        onPage={onPageDocuments}
      />

      {deleting && (
        <DeleteVersionDialog
          versionNumber={deleting.n}
          versionName={deleting.name}
          busy={selectDisabled}
          error={deleteError}
          onCancel={() => setDeleting(null)}
          onConfirm={async () => {
            const message = await onDeleteVersion(deleting.n);
            if (message) setDeleteError(message);
            else setDeleting(null);
          }}
        />
      )}
    </section>
  );
}
