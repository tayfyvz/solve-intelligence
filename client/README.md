# Patent Reviewer UI

## Layout

Application code is in the `src/` directory.

```
src
├── App.tsx      # App shell: grid, mount-time load, error banner, dirty-guard dialog
├── store.ts     # useDocumentStore (zustand) — shared document/version/editor state
├── api.ts       # Typed fetch helpers; every failure carries a message the UI can show
├── types.ts     # API response types
├── components/
│   ├── DocumentList.tsx # Patent picker
│   ├── VersionBar.tsx   # Title, version dropdown, Save / Save as new version
│   ├── Editor.tsx       # Tiptap editor (uncontrolled, remounted by key)
│   └── Spinner.tsx
├── test/        # Vitest setup and tests
├── main.tsx
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
