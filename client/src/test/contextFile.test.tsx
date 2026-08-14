import { useState } from "react";
import { fireEvent, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import TxtDropZone from "../components/TxtDropZone";
import {
  readContextFile,
  readDroppedFiles,
  validateContextText,
  type ContextFile,
  type ContextFileResult,
} from "../contextFile";

const txt = (name: string, content: string) => new File([content], name, { type: "text/plain" });

/** A dropped directory arrives as a `File` with no size and no type. */
const folder = (name: string) => new File([], name, { type: "" });

/** Fires the given `FileReader` callback instead of ever reading anything. */
const stubReader = (fire: (reader: FileReader) => void) =>
  vi.spyOn(FileReader.prototype, "readAsText").mockImplementation(function (this: FileReader) {
    fire(this);
  });

const progress = (type: string) => new ProgressEvent(type) as ProgressEvent<FileReader>;

afterEach(() => vi.restoreAllMocks());

// F1. Every content rule, by its exact message. The messages are the feature — a
// user who drops a UTF-16 file needs to be told that, not "something went wrong".
describe("F1 validateContextText rules", () => {
  const cases: { label: string; run: () => ContextFileResult | Promise<ContextFileResult>; expected: ContextFileResult }[] = [
    {
      label: "rule 2 — a non-.txt name is named in the message",
      run: () => validateContextText("report.pdf", "hello", 5),
      expected: {
        ok: false,
        error: 'Only .txt files are supported. "report.pdf" was not attached.',
      },
    },
    {
      label: "rule 5 — a file of only a BOM is empty, not three bytes of content",
      run: () => validateContextText("notes.txt", "\uFEFF", 3),
      expected: { ok: false, error: "That file is empty." },
    },
    {
      label: "rule 5 — whitespace only is empty",
      run: () => validateContextText("notes.txt", "  \r\n \t ", 7),
      expected: { ok: false, error: "That file is empty." },
    },
    {
      label: "rule 6 — a NUL byte is not UTF-8 text",
      run: () => validateContextText("notes.txt", "prior\u0000art", 9),
      expected: {
        ok: false,
        error: "That file isn't valid UTF-8 text. Please save it as UTF-8 and try again.",
      },
    },
    {
      label: "rule 6 — U+FFFD is what a mis-decoded UTF-16 file looks like",
      run: () => validateContextText("notes.txt", "prior\uFFFDart", 9),
      expected: {
        ok: false,
        error: "That file isn't valid UTF-8 text. Please save it as UTF-8 and try again.",
      },
    },
    {
      label: "accept — the BOM is stripped from the text, and `bytes` stays what was measured",
      run: () => validateContextText("notes.txt", "\uFEFFPrior art.", 13),
      expected: { ok: true, file: { name: "notes.txt", text: "Prior art.", bytes: 13 } },
    },
    {
      label: "accept — CRLF and lone CR normalise, interior indentation survives",
      run: () => validateContextText("notes.txt", "a\r\n  b\rc", 8),
      expected: { ok: true, file: { name: "notes.txt", text: "a\n  b\nc", bytes: 8 } },
    },
    {
      label: "rule 1 — two files at once",
      run: () => readDroppedFiles([txt("a.txt", "a"), txt("b.txt", "b")]),
      expected: { ok: false, error: "Please drop one .txt file at a time." },
    },
    {
      label: "rule 1 — nothing droppable at all",
      run: () => readDroppedFiles(null),
      expected: { ok: false, error: "Please drop one .txt file at a time." },
    },
  ];

  it.each(cases)("$label", async ({ run, expected }) => {
    expect(await run()).toEqual(expected);
  });
});

// F2. "Checked before reading" is the whole point of rule 4's position: a 2 GB .txt
// must never be pulled into memory. The spy is what makes that real rather than
// aspirational.
it("F2 oversize and folder drops are rejected without reading", async () => {
  const spy = vi.spyOn(FileReader.prototype, "readAsText");
  const oversize = txt("huge.txt", "x".repeat(50_000));

  expect(await readContextFile(oversize)).toEqual({
    ok: false,
    error: "That file is 50.0 kB. The limit is 40,000 characters.",
  });
  expect(await readContextFile(folder("prior-art.txt"))).toEqual({
    ok: false,
    error: "Folders can't be attached. Please drop a single .txt file.",
  });
  expect(spy).not.toHaveBeenCalled();
});

// F3. The drop path, end to end through the real component.
it("F3 the drop handler attaches", async () => {
  const onAttach = vi.fn();
  const { getByTestId } = render(
    <TxtDropZone file={null} onAttach={onAttach} onReject={vi.fn()} onClear={vi.fn()} disabled={false} />,
  );

  fireEvent.drop(getByTestId("txt-drop-zone"), {
    dataTransfer: { files: [txt("notes.txt", "Prior art.")] },
  });

  await waitFor(() =>
    expect(onAttach).toHaveBeenCalledWith({ name: "notes.txt", text: "Prior art.", bytes: 10 }),
  );
});

// F4. Without preventDefault on *dragover* specifically, `drop` never fires and the
// whole feature is dead. `fireEvent` returns false exactly when the event was
// cancelled.
it("F4 dragover calls preventDefault", () => {
  const { getByTestId } = render(
    <TxtDropZone file={null} onAttach={vi.fn()} onReject={vi.fn()} onClear={vi.fn()} disabled={false} />,
  );

  expect(fireEvent.dragOver(getByTestId("txt-drop-zone"))).toBe(false);
});

// F5. A boolean here flickers the highlight every time the pointer crosses onto a
// child, because `dragleave` fires on the way in as well as the way out.
it("F5 the drag counter is a counter, not a boolean", () => {
  const { getByTestId, getByRole } = render(
    <TxtDropZone file={null} onAttach={vi.fn()} onReject={vi.fn()} onClear={vi.fn()} disabled={false} />,
  );
  const zone = getByTestId("txt-drop-zone");
  const child = getByRole("button", { name: "Attach .txt" });

  fireEvent.dragEnter(zone);
  fireEvent.dragEnter(child);
  fireEvent.dragLeave(child);
  expect(zone.dataset.over).toBe("true");

  fireEvent.dragLeave(zone);
  expect(zone.dataset.over).toBe("false");
});

// F6. No caller has a `try`, so every FileReader failure mode must come back as a
// resolved rejection result.
describe("F6 a FileReader error resolves, never rejects", () => {
  const failures: [string, (reader: FileReader) => void][] = [
    ["onerror", (reader) => reader.onerror?.(progress("error"))],
    ["onabort", (reader) => reader.onabort?.(progress("abort"))],
    [
      "an onload whose result is an ArrayBuffer",
      (reader) => {
        Object.defineProperty(reader, "result", { value: new ArrayBuffer(8), configurable: true });
        reader.onload?.(progress("load"));
      },
    ],
  ];

  it.each(failures)("%s", async (_label, fire) => {
    stubReader(fire);

    await expect(readContextFile(txt("notes.txt", "Prior art."))).resolves.toEqual({
      ok: false,
      error: "Could not read that file.",
    });
  });
});

// F7. Ordering test. A folder has `size === 0`, which *passes* the size check, and
// `readAsText` on a directory entry rejects — so with rule 4 first this dies as
// "Could not read that file" for something we can name precisely.
it("F7 a dropped folder is named precisely", async () => {
  const spy = vi.spyOn(FileReader.prototype, "readAsText");

  expect(await readDroppedFiles([folder("prior-art.txt")])).toEqual({
    ok: false,
    error: "Folders can't be attached. Please drop a single .txt file.",
  });
  expect(spy).not.toHaveBeenCalled();
});

/** The chip is owned by the parent, so clearing it has to be a real state change. */
function Harness({ onAttach }: { onAttach: (file: ContextFile) => void }) {
  const [file, setFile] = useState<ContextFile | null>(null);
  return (
    <TxtDropZone
      file={file}
      disabled={false}
      onAttach={(attached) => {
        setFile(attached);
        onAttach(attached);
      }}
      onReject={vi.fn()}
      onClear={() => setFile(null)}
    />
  );
}

// F8. Without `e.target.value = ""`, attaching notes.txt, clearing the chip and
// picking notes.txt again is a no-op, which reads as a broken button.
it("F8 re-picking the same file fires again", async () => {
  const onAttach = vi.fn();
  const user = userEvent.setup();
  const { container, getByRole } = render(<Harness onAttach={onAttach} />);
  const input = container.querySelector("input[type=file]") as HTMLInputElement;
  const notes = txt("notes.txt", "Prior art.");

  await user.upload(input, notes);
  await waitFor(() => expect(onAttach).toHaveBeenCalledTimes(1));

  await user.click(getByRole("button", { name: "Remove notes.txt" }));
  await user.upload(input, notes);

  await waitFor(() => expect(onAttach).toHaveBeenCalledTimes(2));
});
