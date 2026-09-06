export function createShareButton(searchUrl) {
  // An iPad can identify itself as a Mac; touch distinguishes it from a desktop Mac.
  const mobile =
    /Android|iPhone|iPad|iPod/u.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const data = { title: "Movie Seat Finder search", url: searchUrl };
  const nativeShare = mobile && typeof navigator.share === "function" && navigator.canShare?.(data);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "buy-btn share-btn";
  button.textContent = "Copy link";
  if (nativeShare) {
    button.textContent = "Share";
  }
  button.setAttribute("aria-live", "polite");
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      if (nativeShare) {
        await navigator.share(data);
        button.textContent = "Share";
        button.title = "";
      } else {
        await navigator.clipboard.writeText(searchUrl);
        button.textContent = "Copied!";
        button.title = "Search link copied to clipboard.";
      }
    } catch (error) {
      if (nativeShare) {
        if (error.name === "AbortError") {
          button.textContent = "Share";
          button.title = "";
        } else {
          button.textContent = "Share failed — retry";
          button.title = "Could not open sharing. Click to try again.";
        }
      } else {
        button.textContent = "Copy failed — retry";
        button.title = "Could not copy the search link. Click to try again.";
      }
    } finally {
      button.disabled = false;
    }
  });
  return button;
}
