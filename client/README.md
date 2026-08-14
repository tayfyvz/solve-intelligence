# Patent Reviewer UI

## Layout

Application code is in the `src/` directory.

```
src
├── App.tsx          # App shell: layout, mount-time load, error banner, the one dirty guard
├── store.ts         # useDocumentStore (zustand) — shared document/version/editor state
├── api.ts           # Typed axios helpers; every failure carries a message the UI can show
├── types.ts         # The wire format, mirroring the server's Pydantic models field for field
├── contextFile.ts   # .txt validation for the chat panel's drop zone
├── ai/              # claims.ts, selection.ts, highlight.ts, format.ts — the client-side
│                    # claim walker, the selection context, the decoration plugin, and the
│                    # "make the selected text bold" fast-path that never leaves the browser
├── components/
│   ├── Banner.tsx       # App bar: what is open, and Save / Save as new version
│   ├── PatentTree.tsx   # Patents, with the open one expanded to show its versions
│   ├── Editor.tsx       # TipTap editor (uncontrolled, remounted by key) + Toolbar
│   ├── chat/            # ChatPanel and its five presentational children
│   └── …                # SidePanel, Modal, dialogs, Pager, TreeRow, InlineRename, Spinner
├── test/            # Vitest setup and tests
└── main.tsx
```

## Running locally

To run locally,

```sh
npm install
npm run dev
```

## Tests, lint and build

```sh
npm run test
npm run lint
npm run build
```
