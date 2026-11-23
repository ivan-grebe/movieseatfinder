// The server skips the outside-the-U.S. notice while this cookie is set.
const DISMISSED_COOKIE = "regionNoticeDismissed=1; Max-Age=31536000; Path=/; SameSite=Lax";

/** Open the outside-the-U.S. dialog when the server asked for it, and handle dismissing it. */
export function setupRegionNotice(dialog, dismissButton) {
  /** Remember the dismissal, then close the dialog once its fade-out finishes. */
  function dismiss() {
    if (dialog.classList.contains("is-closing")) {
      return;
    }

    document.cookie = DISMISSED_COOKIE;
    dialog.classList.add("is-closing");
    const fadeOut = dialog
      .getAnimations({
        subtree: true,
      })
      .map((animation) => animation.finished);

    Promise.allSettled(fadeOut).then(() => {
      dialog.close();
      dialog.classList.remove("is-closing");
    });
  }

  dismissButton.addEventListener("click", dismiss);
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    dismiss();
  });
  dialog.addEventListener("click", (event) => {
    // Clicks on the dimmed backdrop target the dialog itself, not its content.
    if (event.target === dialog) {
      dismiss();
    }
  });
  if ("show" in dialog.dataset) {
    dialog.showModal();
  }
}
