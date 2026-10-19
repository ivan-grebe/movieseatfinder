import { strToU8, zlibSync } from "fflate";
import { formatNiceDate } from "./utils.js";

export function compactShareUrl(searchUrl, showtime) {
  const source = new URL(searchUrl);
  const fields = [
    "zip",
    "radius",
    "movie",
    "theatre",
    "format",
    "startDate",
    "endDate",
    "startTime",
    "endTime",
    "adjacentSeats",
    "excludeAccessible",
    "seatGrid",
    "sort",
    "page",
  ];
  const params = new URLSearchParams();
  for (const field of fields) {
    if (source.searchParams.has(field)) {
      params.set(field, source.searchParams.get(field));
    }
  }
  if (!/^\d{5}$/u.test(params.get("zip") || "")) {
    throw new Error("A resolved ZIP is required to share without precise coordinates.");
  }
  params.set("shared", "1");
  params.set("showtime", showtime);
  if (params.get("seatGrid")) {
    const bitmap = new Uint8Array(29);
    for (const cell of params.get("seatGrid").split(",")) {
      if (!/^(?:[0-9]|1[0-4]):(?:[0-9]|1[0-4])$/u.test(cell)) {
        throw new Error("Invalid seat area.");
      }
      const [row, column] = cell.split(":").map(Number);
      const index = row * 15 + column;
      bitmap[Math.floor(index / 8)] |= 1 << (index % 8);
    }
    params.set(
      "seatGrid",
      btoa(String.fromCharCode(...bitmap))
        .replaceAll("+", "-")
        .replaceAll("/", "_")
        .replaceAll("=", ""),
    );
  }
  const compressed = zlibSync(strToU8(params.toString()), { level: 9 });
  const token = btoa(String.fromCharCode(...compressed))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
  return new URL(`/s/${token}`, source.origin).href;
}

function getSearchShareData(searchUrl, match) {
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
  const data = getSearchShareData(searchUrl, match);
  data.url = compactShareUrl(searchUrl, match.showtimeHashCode);
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
