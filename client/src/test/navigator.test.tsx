import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, describe, expect, it, vi } from "vitest";

import Editor from "../features/editor/Editor";
import FindBar from "../features/editor/FindBar";
import Outline from "../features/editor/Outline";
import { useDocumentStore } from "../store";

/**
 * The navigator against a REAL TipTap editor, because the property under test is that it
 * never disturbs one. A stub could not tell us that.
 *
 * The two things a page view would have broken, asserted directly: the document is
 * byte-identical after navigating, and the dirty flag never moves.
 */

// Same stubs as `editor.test.tsx`: jsdom has no layout engine, and ProseMirror asks the
// DOM for rectangles the moment anything scrolls a selection into view.
beforeAll(() => {
  const empty = { length: 0, item: () => null } as unknown as DOMRectList;
  Range.prototype.getClientRects = () => empty;
  Range.prototype.getBoundingClientRect = () =>
    ({ top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0 }) as DOMRect;
  Element.prototype.scrollIntoView = vi.fn();
});

const LONG = [
  "<h1>BACKGROUND</h1><p>Prior oxygenators have a large priming volume.</p>",
  "<h2>Related Art</h2><p>US 1,234,567 discloses a membrane with a priming volume.</p>",
  "<h1>Claims</h1>",
  ...Array.from({ length: 40 }, (_, i) => `<p>${i + 1}. A device of type ${i + 1}.</p>`),
].join("");

const mountedEditor = () =>
  waitFor(() => {
    const editor = useDocumentStore.getState().editor;
    expect(editor).not.toBeNull();
    return editor!;
  });

describe("N3 Outline", () => {
  it("shows the sections first and folds a long claim run behind one row", async () => {
    // The shape complaint this replaced: 43 flat rows buried the three headings that say
    // what the document IS. Sections first, claims one click away.
    render(
      <>
        <Editor content={LONG} />
        <Outline />
      </>,
    );
    await mountedEditor();

    const nav = await screen.findByRole("navigation", { name: "Document outline" });
    await waitFor(() =>
      expect(within(nav).getByRole("button", { name: "BACKGROUND" })).toBeTruthy(),
    );
    expect(within(nav).getByRole("button", { name: "Related Art" })).toBeTruthy();

    const fold = within(nav).getByRole("button", { name: "40 claims" });
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(within(nav).queryByRole("button", { name: "Claim 40" })).toBeNull();
    // Four rows, not forty-three: three headings and the fold.
    expect(within(nav).getAllByRole("button")).toHaveLength(4);

    await userEvent.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    // Claim 40 is ~29 screens down. One click to open, one to go there.
    expect(within(nav).getByRole("button", { name: "Claim 40" })).toBeTruthy();
  });

  it("filters to what the user typed, opening the fold so the hits are visible", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Editor content={LONG} />
        <Outline />
      </>,
    );
    await mountedEditor();
    await screen.findByRole("navigation", { name: "Document outline" });

    await user.type(screen.getByLabelText("Filter outline"), "claim 4");
    const nav = screen.getByRole("navigation", { name: "Document outline" });
    // "Claim 4" and "Claim 40": found without expanding the fold by hand.
    await waitFor(() => expect(within(nav).getByRole("button", { name: "Claim 4" })).toBeTruthy());
    expect(within(nav).getByRole("button", { name: "Claim 40" })).toBeTruthy();
    expect(within(nav).queryByRole("button", { name: "BACKGROUND" })).toBeNull();

    await user.clear(screen.getByLabelText("Filter outline"));
    await user.type(screen.getByLabelText("Filter outline"), "zzz");
    await waitFor(() => expect(screen.getByText(/Nothing matches/)).toBeTruthy());
  });

  it("does not fold a short claim set — three claims are just shown", async () => {
    render(
      <>
        <Editor content={"<h1>Claims</h1><p>1. A.</p><p>2. B.</p><p>3. C.</p>"} />
        <Outline />
      </>,
    );
    await mountedEditor();
    const nav = await screen.findByRole("navigation", { name: "Document outline" });
    await waitFor(() => expect(within(nav).getByRole("button", { name: "Claim 3" })).toBeTruthy());
    expect(within(nav).getByRole("button", { name: "3 claims" }).getAttribute("aria-expanded"))
      .toBe("true");
  });

  it("jumps WITHOUT changing the document or the dirty flag", async () => {
    render(
      <>
        <Editor content={LONG} />
        <Outline />
      </>,
    );
    const editor = await mountedEditor();
    const before = editor.getHTML();
    useDocumentStore.setState({ dirty: false });

    const nav = await screen.findByRole("navigation", { name: "Document outline" });
    await waitFor(() => within(nav).getByRole("button", { name: "40 claims" }));
    await userEvent.click(within(nav).getByRole("button", { name: "40 claims" }));
    await userEvent.click(within(nav).getByRole("button", { name: "Claim 40" }));

    // THE assertion. A paginated or virtualised contenteditable could not make it.
    expect(editor.getHTML()).toBe(before);
    expect(useDocumentStore.getState().dirty).toBe(false);
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("says so when there is nothing to navigate, rather than rendering an empty rail", () => {
    useDocumentStore.setState({ editor: null });
    render(<Outline />);
    expect(screen.getByText("Open a patent to see its outline.")).toBeTruthy();
  });
});

describe("N4 FindBar", () => {
  it("counts matches, steps through them, and leaves the document alone", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Editor content={LONG} />
        <FindBar />
      </>,
    );
    const editor = await mountedEditor();
    const before = editor.getHTML();
    useDocumentStore.setState({ dirty: false });

    await user.type(screen.getByLabelText("Find in document"), "priming volume");
    await waitFor(() => expect(screen.getByText("1 of 2")).toBeTruthy());

    await user.click(screen.getByRole("button", { name: "Next match" }));
    expect(screen.getByText("2 of 2")).toBeTruthy();
    // Wraps rather than dead-ending, so "next" is always answerable.
    await user.click(screen.getByRole("button", { name: "Next match" }));
    expect(screen.getByText("1 of 2")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Previous match" }));
    expect(screen.getByText("2 of 2")).toBeTruthy();

    expect(editor.getHTML()).toBe(before);
    expect(useDocumentStore.getState().dirty).toBe(false);
  });

  it("says No matches instead of showing nothing", async () => {
    const user = userEvent.setup();
    render(
      <>
        <Editor content={LONG} />
        <FindBar />
      </>,
    );
    await mountedEditor();

    await user.type(screen.getByLabelText("Find in document"), "titanium");
    await waitFor(() => expect(screen.getByText("No matches")).toBeTruthy());
    expect((screen.getByRole("button", { name: "Next match" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});
