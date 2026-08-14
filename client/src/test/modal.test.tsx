import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import Modal from "../components/Modal";

/**
 * A trigger, a control behind the backdrop, and a dialog with two stops. The
 * "Behind" button is the one Tab used to escape onto — in the real app it is the
 * contenteditable, which is worse: the user types into the document they were
 * being asked about.
 */
function Fixture() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <button type="button">Behind</button>
      {open && (
        <Modal labelledBy="fixture-title" onDismiss={() => setOpen(false)}>
          <h2 id="fixture-title">Dialog</h2>
          <button type="button">First</button>
          <button type="button">Last</button>
        </Modal>
      )}
    </>
  );
}

// Verified in a browser: four Tabs from the unsaved-changes dialog walked focus
// out of it and into the page behind the backdrop. `aria-modal` tells a screen
// reader the rest of the page is inert; it does not make Tab obey.
it("keeps Tab inside the dialog, in both directions", async () => {
  const user = userEvent.setup();
  render(<Fixture />);
  await user.click(screen.getByRole("button", { name: "Open" }));

  // From outside the panel, the first Tab goes in rather than on to "Behind".
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "First" }));

  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Last" }));

  // …and past the last stop it wraps, instead of leaving.
  await user.tab();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "First" }));

  await user.tab({ shift: true });
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Last" }));
});

// The other half of a modal: on close, focus landed on <body>, so a keyboard user
// restarted from the top of the page every time.
it("returns focus to whatever opened it", async () => {
  const user = userEvent.setup();
  render(<Fixture />);
  const trigger = screen.getByRole("button", { name: "Open" });
  await user.click(trigger);

  await user.keyboard("{Escape}");

  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
