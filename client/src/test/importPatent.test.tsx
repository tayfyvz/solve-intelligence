import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CONTEXT_LIMIT, IMPORT_LIMIT, readTextFile } from "../textFile";
import dialogSource from "../components/ImportPatentDialog.tsx?raw";
import type { TextImportResult } from "../types";

vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  importText: vi.fn(),
}));

const { importText } = vi.mocked(await import("../api"));
const { default: ImportPatentDialog } = await import("../components/ImportPatentDialog");

const txt = (name: string, body: string) =>
  new File([body], name, { type: "text/plain" });

const preview = (over: Partial<TextImportResult> = {}): TextImportResult => ({
  title: "Widget Patent",
  content: "<h1>Claims</h1><p>1. A widget.</p><p>2. The widget of claim 1.</p>",
  claim_count: 2,
  notes: [],
  ...over,
});

function open(props: Partial<React.ComponentProps<typeof ImportPatentDialog>> = {}) {
  const onImport = vi.fn(async () => null);
  const onCancel = vi.fn();
  render(
    <ImportPatentDialog
      openPatentTitle={null}
      onImport={onImport}
      onCancel={onCancel}
      {...props}
    />,
  );
  return { onImport, onCancel };
}

const choose = (file: File) =>
  userEvent.upload(screen.getByLabelText("Choose a .txt patent to import"), file);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("IM-C1 the two jobs a .txt can have are told apart", () => {
  // The requirement in one assertion: two flows, same file type, and the user must be
  // able to see which one they are in without trying it.
  it("says in the dialog that this one CREATES a patent, unlike the chat drop zone", () => {
    open();
    expect(screen.getByText(/becomes a patent you can edit/)).toBeTruthy();
    expect(screen.getByText(/drop it on the chat panel; that never changes your document/))
      .toBeTruthy();
  });

  it("uses the SAVE limit, not the AI context limit — a 60-page patent is not oversized", async () => {
    // 100,000 bytes: far over the 40,000-byte context cap, far under the 1 MB save cap.
    const big = txt("patent.txt", "x".repeat(100_000));
    expect((await readTextFile(big, CONTEXT_LIMIT)).ok).toBe(false);
    expect((await readTextFile(big, IMPORT_LIMIT)).ok).toBe(true);
  });
});

describe("IM-C2 the preview", () => {
  // jsdom cannot reproduce this one: its FileList is not live, so the bug is invisible to
  // every assertion above and showed up only on the first real click in Chrome — the
  // input was cleared BEFORE its list was read, and every pick arrived as "no files".
  // The repo already asserts a one-character contract from source (`editor.test.tsx`);
  // this is the same tool for the same reason.
  it("copies the chosen files out BEFORE resetting the input", () => {
    const copy = dialogSource.indexOf("Array.from(event.target.files");
    const reset = dialogSource.indexOf('event.target.value = ""');
    expect(copy).toBeGreaterThan(-1);
    expect(copy).toBeLessThan(reset);
  });

  it("converts on drop and shows the claim count and title before anything is created", async () => {
    importText.mockResolvedValue(preview());
    const { onImport } = open();

    await choose(txt("widget.txt", "Widget Patent\n\nCLAIMS\n\n1. A widget.\n\n2. Second.\n"));
    await waitFor(() => expect(screen.getByText("2")).toBeTruthy());

    expect(importText).toHaveBeenCalledWith(
      "Widget Patent\n\nCLAIMS\n\n1. A widget.\n\n2. Second.\n",
      "widget.txt",
    );
    // Nothing is created until the user says so.
    expect(onImport).not.toHaveBeenCalled();
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Widget Patent");
  });

  it("shows every note the importer produced, BEFORE the patent exists", async () => {
    // "never fail silently": an odd claim set is the user's decision, not a surprise.
    importText.mockResolvedValue(
      preview({ notes: ["Claim number 2 appears more than once.", "The claim numbering skips."] }),
    );
    open();

    await choose(txt("odd.txt", "1. A.\n\n2. B.\n\n2. C.\n"));
    await waitFor(() =>
      expect(screen.getByText("Claim number 2 appears more than once.")).toBeTruthy(),
    );
    expect(screen.getByText("The claim numbering skips.")).toBeTruthy();
  });

  it("hands over exactly the previewed bytes when Import is clicked", async () => {
    const converted = preview();
    importText.mockResolvedValue(converted);
    const { onImport } = open();

    await choose(txt("widget.txt", "anything"));
    await waitFor(() => expect(screen.getByLabelText("Title")).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "Import patent" }));

    expect(onImport).toHaveBeenCalledWith("document", "Widget Patent", converted.content);
  });
});

describe("IM-C3 failures are visible, never only in the console", () => {
  it("shows the rejection for a file that is not a .txt, and converts nothing", async () => {
    open();
    // DROPPED, not picked: the file input carries `accept=".txt"` and the browser (and
    // testing-library) filters a .pdf out before `change` ever fires. Drag-and-drop has
    // no such filter, which is exactly why the rule is enforced in `textFile.ts` and not
    // left to the attribute.
    fireEvent.drop(screen.getByTestId("import-drop-zone"), {
      dataTransfer: { files: [new File(["x"], "report.pdf", { type: "application/pdf" })] },
    });

    expect((await screen.findByRole("alert")).textContent).toBe(
      'Only .txt files are supported, and "report.pdf" is not one.',
    );
    expect(importText).not.toHaveBeenCalled();
  });

  it("shows the server's own sentence when the conversion fails", async () => {
    const { ApiError } = await import("../api");
    importText.mockRejectedValue(new ApiError(413, "That file is too large to import."));
    open();

    await choose(txt("huge.txt", "x"));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "That file is too large to import.",
    );
    // Import stays disabled: there is nothing to import.
    expect(
      (screen.getByRole("button", { name: "Import patent" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });

  it("keeps the dialog open with the reason when creating the patent is refused", async () => {
    importText.mockResolvedValue(preview());
    const onImport = vi.fn(async () => 'A patent called "Widget Patent" already exists.');
    render(
      <ImportPatentDialog openPatentTitle={null} onImport={onImport} onCancel={vi.fn()} />,
    );

    await choose(txt("widget.txt", "x"));
    await waitFor(() => expect(screen.getByLabelText("Title")).toBeTruthy());
    await userEvent.click(screen.getByRole("button", { name: "Import patent" }));

    expect((await screen.findByRole("alert")).textContent).toContain("already exists");
    // Still open, with the title still filled in — a rejected title is one word away
    // from a good one.
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Widget Patent");
  });
});

describe("IM-C4 the second destination", () => {
  it("offers a new version only when a patent is open, and reports the choice", async () => {
    importText.mockResolvedValue(preview());
    const { onImport } = open({ openPatentTitle: "Oxygenator" });

    await choose(txt("widget.txt", "x"));
    await waitFor(() => expect(screen.getByText("Import as")).toBeTruthy());
    await userEvent.click(screen.getByRole("radio", { name: /A new version of/ }));

    // A version has no title of its own, so the field goes away with the choice.
    expect(screen.queryByLabelText("Title")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Import patent" }));
    expect(onImport).toHaveBeenCalledWith("version", "Widget Patent", preview().content);
  });

  it("does not offer a version when nothing is open", async () => {
    importText.mockResolvedValue(preview());
    open({ openPatentTitle: null });

    await choose(txt("widget.txt", "x"));
    await waitFor(() => expect(screen.getByLabelText("Title")).toBeTruthy());
    expect(screen.queryByText("Import as")).toBeNull();
  });
});
