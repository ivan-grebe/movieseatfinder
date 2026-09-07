import { formatNiceDate } from "./utils.js";

export function getSearchShareData(searchUrl, match) {
  const params = new URL(searchUrl).searchParams;
  const movie = match.movieTitle;
  const details = [];
  const seats = Number(params.get("adjacentSeats"));
  if (seats > 1) {
    details.push(`${seats} seats together`);
  } else if (seats === 1) {
    details.push("1 seat");
  }
  details.push(formatNiceDate(match.date), match.displayTime, match.format, match.theatre.name);
  return {
    text: [movie, details.filter(Boolean).join(" · ")].join("\n"),
    title: `${movie} — Movie Seat Finder`,
    url: searchUrl,
  };
}

export function createShareButton(searchUrl, match) {
  // An iPad can identify itself as a Mac; touch distinguishes it from a desktop Mac.
  const mobile =
    /Android|iPhone|iPad|iPod/u.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const sharedUrl = new URL(searchUrl);
  sharedUrl.searchParams.set("shared", "1");
  sharedUrl.searchParams.set("showtime", match.showtimeHashCode);
  const data = getSearchShareData(sharedUrl.href, match);
  const nativeShare = mobile && typeof navigator.share === "function" && navigator.canShare?.(data);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "buy-btn share-btn";
  let defaultLabel = "Copy showtime link";
  if (nativeShare) {
    defaultLabel = "Share showtime";
  }
  const label = document.createElement("span");
  label.className = "share-label";
  label.textContent = defaultLabel;
  label.setAttribute("aria-hidden", "true");
  const feedback = document.createElement("span");
  feedback.className = "share-feedback";
  feedback.setAttribute("role", "status");
  feedback.setAttribute("aria-live", "polite");
  feedback.setAttribute("aria-hidden", "true");
  button.append(label, feedback);
  button.setAttribute("aria-label", defaultLabel);
  let resetTimer = 0;

  function resetFeedback() {
    button.classList.remove("has-feedback");
    button.setAttribute("aria-label", defaultLabel);
    feedback.setAttribute("aria-hidden", "true");
    button.title = "";
  }

  function showFeedback(text, title) {
    feedback.textContent = text;
    feedback.setAttribute("aria-hidden", "false");
    button.classList.add("has-feedback");
    button.setAttribute("aria-label", text);
    button.title = title;
  }

  button.addEventListener("click", async () => {
    globalThis.clearTimeout(resetTimer);
    resetFeedback();
    button.disabled = true;
    try {
      if (nativeShare) {
        await navigator.share(data);
      } else {
        await navigator.clipboard.writeText(data.url);
        showFeedback("Copied!", "Showtime link copied to clipboard.");
        resetTimer = globalThis.setTimeout(resetFeedback, 2000);
      }
    } catch (error) {
      if (nativeShare) {
        if (error.name !== "AbortError") {
          showFeedback("Share failed — retry", "Could not open sharing. Click to try again.");
        }
      } else {
        showFeedback(
          "Copy failed — retry",
          "Could not copy the showtime link. Click to try again.",
        );
      }
    } finally {
      button.disabled = false;
    }
  });
  return button;
}
