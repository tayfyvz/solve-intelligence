# Frontend — React + TipTap

The patent editor: a document tree, a rich-text editor, and an AI chat panel.

## Setup

```sh
npm install
npm run dev      # http://localhost:5173
```

It expects the backend on `http://localhost:8000`. Point it elsewhere with `VITE_API_URL`.

```sh
npm run test     # vitest
npm run lint
npm run build    # tsc + vite build — must pass before submitting
```

## Layout

Code is grouped by feature, not by file type.

```
src
├── App.tsx              # shell: layout, initial load, error banner, the dirty guard
├── store.ts             # zustand store — shared document / version / editor state
├── types.ts             # the wire format, mirroring the server's Pydantic models
├── services/api.ts      # typed axios helpers; every failure carries a readable message
├── features/
│   ├── patents/         # PatentTree, TreeRow, new-patent and import dialogs
│   ├── versions/        # dirty-state and delete confirmations
│   ├── editor/          # Editor, Toolbar, Outline, FindBar, claim walker, highlighting
│   └── chat/            # ChatPanel and its parts: composer, messages, proposal card,
│                        #   context chips, selection capture, response formatting
├── components/
│   ├── layout/          # Banner (app bar, save actions), SidePanel
│   └── ui/              # Modal, Pager, Spinner, InlineRename, TxtDropZone, ErrorBoundary
├── utils/               # .txt reading and validation, time formatting
├── styles/index.css     # Tailwind entry and the shared design tokens
└── test/                # vitest suites and the seed fixture
```

## Rules worth knowing before you edit

**The editor is uncontrolled.** There is no effect comparing an HTML string to `getHTML()`.
Content changes happen by remounting: `key={docId}:{versionNumber}`. This is the single
biggest source of bugs in editor integrations, and avoiding it entirely is the design.

**Navigation never dispatches a document transaction.** The outline and the find bar read
`editor.state.doc` and scroll; the only transaction either sends is meta-only, so
`docChanged` stays false. That is what lets them coexist with the uncontrolled editor.

**The client only calls `setContent` when the AI response's `html` is non-null.** A failed
or ambiguous request must leave the document byte-identical. And `setContent(html, true)` —
the `true` makes `onUpdate` fire; dropping it is a one-character bug that silently loses the
dirty flag.

**The store holds shared state only.** If one component reads a value, it stays local. There
is exactly one deliberate exception, `versionSource`, documented in a comment where it is
declared: it must be written in the same `set()` as `versionNumber` because it describes a
*transition*, and only the store can observe one.

**Every async action renders explicit loading and error states.** Never `console.error`
alone. No `any` — the API helpers in `services/api.ts` are the typed boundary.

## Seed fixture

`npm run normalise-seed` regenerates `test/seed.fixture.ts` from `scripts/seed-source`, so
tests compare against the exact HTML TipTap produces rather than a hand-written guess.
