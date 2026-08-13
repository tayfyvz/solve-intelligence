# Solve Intelligence Engineering Challenge

## Objective

You have received a mock-up of a patent reviewing application from a junior colleague. It is incomplete and needs work. Your job is to extend and improve it to a standard you'd be comfortable shipping to production. This means:

- Clean code that is production quality
- Unit tests
- No bugs

After completing the tasks below, add a couple of sentences to the end of this file briefly outlining what improvements you made.

## Docker

From a clean clone, two commands:

```sh
cp server/.env.example server/.env   # then put the provided OpenAI API key in it
docker-compose up --build
```

The copy is not optional: Compose resolves `env_file` up front, so `docker-compose up` fails
with "env file … not found" if `server/.env` is missing.

Client: http://localhost:5173 · API: http://localhost:8000

If a rebuild ever picks up a stale dependency environment, clear the anonymous volumes:

```sh
docker compose down -v && docker-compose up --build
```

## Task 1: Implement Document Versioning

Currently, the user can save a document, but there is no concept of **versioning**. Paying customers have expressed an interest in this and have requested the following:

1. The ability to create new versions
2. The ability to switch between existing versions
3. The ability to make changes to any of the existing versions and save those changes (without creating a new version)

You will need to modify the database model (`app/models.py`), add some API routes (`app/__main__.py`), and update the client-side code accordingly.

## Task 2: Choose One of the Following

Complete **one** of the two options below.

### Option A: AI-Powered Document Editing

Implement a chat interface that allows users to edit the patent document using natural language instructions.

Minimal Requirements:
1. A chat-style UI panel where users can type editing instructions
2. The AI should interpret the instruction and modify the document HTML accordingly
3. Changes should be applied to the editor and visible immediately
4. Support drag-and-drop .txt file upload to the chat to provide additional context for the AI


Example instructions your solution should handle:
- "Make claim 1 bold"
- "Delete claim 3"
- "Add a new dependent claim after claim 2 that specifies the material is glass"
- "Write a background section based on the prior art file I have uploaded"

### Option B: Live Collaboration

Implement real-time collaborative editing so multiple users can work on the same document simultaneously.

Minimal Requirements:
1. Multiple users should be able to view and edit the same document at the same time
2. Changes made by one user should appear in real-time for all other users
3. Show presence indicators (e.g., cursors, user avatars) to indicate where other users are editing
4. Handle conflict resolution gracefully when multiple users edit the same section

## Note
You may use AI (and the API key we have provided) to assist with coding on this task. When we review submissions we will stress test your solution across a range of inputs and common user behaviours, so do consider this when designing your solution. 

If your submission passes our review, the next stage will involve pair programming without AI assistance.

Good luck!

## Improvements made

**Task 1 (document versioning) is implemented.** Content moved off `Document` onto a
`DocumentVersion` table — a mutable draft, not an immutable snapshot, which is what makes
requirement 3 work: `PUT /api/documents/{id}/versions/{n}` updates a version in place and never
creates one, while `POST /api/documents/{id}/versions` creates `MAX+1` from the editor buffer.
Switching versions is guarded by a Save / Save as new version / Discard / Cancel dialog when
there are unsaved edits.

Fixes to the inherited scaffold along the way:

- File-backed SQLite with an idempotent seed, so versions survive a restart
- `app/` split into `main.py`, `config.py`, `crud.py`, `sanitize.py` and `routers/`; 404s with
  readable messages where routes previously returned `None` or a silent 200
- Saved HTML sanitised against an allowlist derived from TipTap's StarterKit
- Uncontrolled TipTap remounted by key (removes the sync-effect caret bug); Zustand store with
  loading and error states rendered in the UI, not just the console
- `docker-compose up --build` works from a clean clone; `npm run lint` and the test suites run

**Task 2 is not implemented.** Option A (AI-powered editing) is designed but not built — see
`DESIGN.md` for the intended structured-operations approach and the reasoning behind it.
