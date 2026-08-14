import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import PatentTree, { type PatentTreeProps } from "../components/PatentTree";
import { relativeTime } from "../components/time";

/**
 * PatentTree is props-only, so it is testable without the store. These tests
 * cover what App-level tests cannot see cheaply: the nesting itself, the
 * accumulate-and-show-more control, timestamp rendering, the patents pager's
 * boundaries and the inline rename's exits.
 */

/** What the server sends: an ISO instant in UTC with the zone suffix stripped. */
function naiveUtcAgo(ms: number): string {
  return new Date(Date.now() - ms).toISOString().slice(0, 19);
}

const TWO_MINUTES_AGO = naiveUtcAgo(2 * 60_000);
const YESTERDAY = naiveUtcAgo(26 * 3_600_000);

function summary(n: number, updated = TWO_MINUTES_AGO, name = `Version ${n}`) {
  return { version_number: n, name, created_at: "2026-01-01T00:00:00", updated_at: updated };
}

function patent(id: number, title = `Patent ${id}`) {
  return { id, title, version_count: 2, updated_at: TWO_MINUTES_AGO };
}

function treeProps(overrides: Partial<PatentTreeProps> = {}): PatentTreeProps {
  return {
    documents: [patent(1), patent(2)],
    documentsTotal: 2,
    documentsOffset: 0,
    documentsLimit: 20,
    selectedId: 1,
    versions: [summary(2), summary(1, YESTERDAY)],
    versionsTotal: 2,
    selectedVersion: 2,
    selectDisabled: false,
    listBusy: false,
    onSelectDocument: vi.fn(),
    onSelectVersion: vi.fn(),
    onCreate: vi.fn(),
    onImport: vi.fn(),
    onRenameDocument: vi.fn(async () => null),
    onRenameVersion: vi.fn(async () => null),
    onPageDocuments: vi.fn(),
    onShowMoreVersions: vi.fn(),
    ...overrides,
  };
}

/** A row button, not the pencil beside it: its name starts with the row's label. */
const row = (label: string) => screen.getByRole("button", { name: new RegExp(`^${label}\\b`) });
const versionsOf = (title: string) => screen.getByRole("list", { name: `Versions of "${title}"` });

describe("PatentTree", () => {
  // The whole point of the restructure: a version has to read as belonging to a
  // patent, not as a second list that happens to share the rail.
  it("nests the versions under the open patent and nowhere else", () => {
    render(<PatentTree {...treeProps()} />);

    const nested = versionsOf("Patent 1");
    expect(within(nested).getByRole("button", { name: /^Version 2\b/ })).toBeTruthy();
    expect(within(nested).getByRole("button", { name: /^Version 1\b/ })).toBeTruthy();
    // The closed patent has no branch at all.
    expect(screen.queryByRole("list", { name: 'Versions of "Patent 2"' })).toBeNull();
    // And the open row says it is expanded.
    expect(row("Patent 1").getAttribute("aria-expanded")).toBe("true");
    expect(row("Patent 2").getAttribute("aria-expanded")).toBe("false");
  });

  it("marks the open version and switches on one click", async () => {
    const props = treeProps();
    render(<PatentTree {...props} />);

    expect(row("Version 2").getAttribute("aria-current")).toBe("true");
    expect(row("Version 1").getAttribute("aria-current")).toBeNull();

    await userEvent.click(row("Version 1"));
    expect(props.onSelectVersion).toHaveBeenCalledWith(1);
    await userEvent.click(row("Patent 2"));
    expect(props.onSelectDocument).toHaveBeenCalledWith(2);
  });

  // Versions accumulate rather than paging: the control is "show more", it says
  // how far through the list we are, and it disappears once there is no more.
  it("offers Show more versions with a count until every version is loaded", async () => {
    const props = treeProps({
      versions: [summary(45), summary(44)],
      versionsTotal: 45,
    });
    const { rerender } = render(<PatentTree {...props} />);

    expect(screen.getByText("2 of 45")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Show more versions" }));
    expect(props.onShowMoreVersions).toHaveBeenCalledTimes(1);

    // A list request in flight: clicking again would ask for the same offset twice.
    rerender(
      <PatentTree
        {...treeProps({ versions: [summary(45)], versionsTotal: 45, listBusy: true })}
      />,
    );
    expect(
      (screen.getByRole("button", { name: "Show more versions" }) as HTMLButtonElement).disabled,
    ).toBe(true);

    rerender(<PatentTree {...treeProps()} />);
    expect(screen.queryByRole("button", { name: "Show more versions" })).toBeNull();
    expect(screen.queryByText("2 of 2")).toBeNull();
  });

  // Naive UTC must not be read as local: parsed as local, a browser in UTC+13
  // would render every save as hours in the future — which is exactly what the
  // fixed-clock assertion below pins down, whatever zone CI runs in. The exact
  // second stays on the element for the user who needs it.
  it("shows relative time with the exact UTC value on hover", () => {
    render(<PatentTree {...treeProps()} />);

    expect(screen.getByText("yesterday").getAttribute("title")).toBe(
      `${YESTERDAY.replace("T", " ")} UTC`,
    );
    expect(relativeTime("2026-01-01T11:58:00", Date.parse("2026-01-01T12:00:00Z"))).toBe(
      "2 minutes ago",
    );
  });

  // Both ends, because "disabled at the boundary" is the half of pagination that
  // is easy to get wrong and impossible to notice on a two-page list. Patents keep
  // a pager; only versions accumulate.
  it("pages the patents within bounds and hides the pager when everything fits", () => {
    const { rerender } = render(
      <PatentTree {...treeProps({ documentsTotal: 45, documentsOffset: 20 })} />,
    );

    expect(screen.getByText("21–40 of 45")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Previous page of patents" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);

    rerender(<PatentTree {...treeProps({ documentsTotal: 45, documentsOffset: 0 })} />);
    expect(
      (screen.getByRole("button", { name: "Previous page of patents" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);

    rerender(<PatentTree {...treeProps({ documentsTotal: 45, documentsOffset: 40 })} />);
    expect(screen.getByText("41–45 of 45")).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: "Next page of patents" }) as HTMLButtonElement).disabled,
    ).toBe(true);

    rerender(<PatentTree {...treeProps()} />);
    expect(screen.queryByRole("button", { name: "Next page of patents" })).toBeNull();
  });

  it("shows empty states rather than bare lists", () => {
    const { rerender } = render(<PatentTree {...treeProps({ versions: [], versionsTotal: 0 })} />);
    expect(screen.getByText("No versions to show.")).toBeTruthy();

    rerender(<PatentTree {...treeProps({ documents: [], documentsTotal: 0, selectedId: null })} />);
    expect(screen.getByText("No patents yet. Create one to get started.")).toBeTruthy();
  });

  // The 409 path: the sentence has to land under the field being edited, and the
  // typed name has to survive it — retyping a long name because the server said
  // "already exists" is a punishment.
  it("keeps a rejected rename open, with the reason under the field", async () => {
    const user = userEvent.setup();
    const onRenameVersion = vi.fn(async () => 'A version called "Final" already exists.');
    render(<PatentTree {...treeProps({ onRenameVersion })} />);

    await user.click(screen.getByRole("button", { name: "Rename version 2" }));
    const field = screen.getByRole("textbox", { name: "Rename version 2" });
    await user.clear(field);
    await user.type(field, "Final{Enter}");

    expect(onRenameVersion).toHaveBeenCalledWith(2, "Final");
    const form = field.closest("form") as HTMLFormElement;
    expect((await within(form).findByRole("alert")).textContent).toBe(
      'A version called "Final" already exists.',
    );
    expect((field as HTMLInputElement).value).toBe("Final");
  });

  it("commits a rename on Enter and abandons it on Escape", async () => {
    const user = userEvent.setup();
    const props = treeProps();
    render(<PatentTree {...props} />);

    await user.click(screen.getByRole("button", { name: 'Rename patent "Patent 1"' }));
    await user.type(
      screen.getByRole("textbox", { name: 'Rename patent "Patent 1"' }),
      " draft{Enter}",
    );
    expect(props.onRenameDocument).toHaveBeenCalledWith(1, "Patent 1 draft");
    await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());

    await user.click(screen.getByRole("button", { name: "Rename version 1" }));
    await user.type(screen.getByRole("textbox", { name: "Rename version 1" }), " again{Escape}");
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(props.onRenameVersion).not.toHaveBeenCalled();
  });

  // Blur is deliberately not an exit: clicking Save blurs the field first, so a
  // commit-or-cancel-on-blur would race the click that started it.
  it("leaves the editor open on blur", async () => {
    const user = userEvent.setup();
    render(<PatentTree {...treeProps()} />);

    await user.click(screen.getByRole("button", { name: "Rename version 1" }));
    const form = screen.getByRole("textbox", { name: "Rename version 1" }).closest("form");
    await user.tab();

    expect(within(form as HTMLFormElement).getByRole("textbox")).toBeTruthy();
  });

  // Every row that changes what is in the editor is gated on the same flag, the
  // "New patent" button included: it opens the patent it creates, so an ungated
  // one raises a dirty dialog whose buttons are all disabled.
  it("disables every control that would change the editor while it is busy", () => {
    render(<PatentTree {...treeProps({ selectDisabled: true })} />);

    for (const name of ["Patent 1", "Patent 2", "Version 1", "Version 2"]) {
      expect((row(name) as HTMLButtonElement).disabled).toBe(true);
    }
    expect(
      (screen.getByRole("button", { name: "New patent" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});
