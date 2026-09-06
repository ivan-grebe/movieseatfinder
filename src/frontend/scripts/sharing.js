import { formatNiceDate } from "./utils.js";

export function getSearchShareData(searchUrl) {
  const params = new URL(searchUrl).searchParams;
  const movie = params.get("movie");
  const details = [];
  const seats = Number(params.get("adjacentSeats"));
  if (seats > 1) {
    details.push(`${seats} seats together`);
  } else if (seats === 1) {
    details.push("1 seat");
  }
  const start = params.get("startDate");
  const end = params.get("endDate");
  if (start) {
    let dates = formatNiceDate(start);
    if (end && end !== start) {
      dates += ` – ${formatNiceDate(end)}`;
    }
    details.push(dates);
  }
  const formats = params.get("format");
  if (formats) {
    details.push(formats.split(",").join(", "));
  }
  const theatre = params.get("theatre");
  if (theatre) {
    details.push(theatre);
  }
  return {
    text: [`Find showtimes for ${movie}`, details.join(" · ")].filter(Boolean).join("\n"),
    title: `${movie} — Movie Seat Finder`,
    url: searchUrl,
  };
}

export function createShareButton(searchUrl) {
  // An iPad can identify itself as a Mac; touch distinguishes it from a desktop Mac.
  const mobile =
    /Android|iPhone|iPad|iPod/u.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const data = getSearchShareData(searchUrl);
  const nativeShare = mobile && typeof navigator.share === "function" && navigator.canShare?.(data);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "buy-btn share-btn";
  let defaultLabel = "Copy search link";
  if (nativeShare) {
    defaultLabel = "Share";
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
        await navigator.clipboard.writeText(searchUrl);
        showFeedback("Copied!", "Search link copied to clipboard.");
        resetTimer = globalThis.setTimeout(resetFeedback, 2000);
      }
    } catch (error) {
      if (nativeShare) {
        if (error.name !== "AbortError") {
          showFeedback("Share failed — retry", "Could not open sharing. Click to try again.");
        }
      } else {
        showFeedback("Copy failed — retry", "Could not copy the search link. Click to try again.");
      }
    } finally {
      button.disabled = false;
    }
  });
  return button;
}
