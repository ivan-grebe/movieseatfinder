import { formatNiceDate } from "./utils.js";
import { setAnimatedStatus, setSummary } from "./ui.js";
import { logTicketClick } from "./tracking.js";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const ICON_FILM = [
  ["rect", { x: "3", y: "4", width: "18", height: "16", rx: "2" }],
  ["path", { d: "M7 4v16M17 4v16M3 9h4M3 14h4M17 9h4M17 14h4" }],
];
const ICON_CALENDAR = [
  ["rect", { x: "3", y: "4.5", width: "18", height: "16", rx: "2" }],
  ["path", { d: "M3 9h18M8 2.5v4M16 2.5v4" }],
];
function renderRealSeatMap(seatMap) {
  const image = document.createElement("img");
  image.className = "real-seat-map-image";
  image.alt = `Live Fandango seat map: ${seatMap.availableSeatCount} available of ${seatMap.totalSeatCount} total seats`;
  image.loading = "lazy";
  image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(seatMap.visualSvg);
  return image;
}

function createIcon(definition) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  Object.entries({
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    "stroke-width": "2",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
  }).forEach(([name, value]) => svg.setAttribute(name, value));
  definition.forEach(([tagName, attributes]) => {
    const child = document.createElementNS(SVG_NAMESPACE, tagName);
    Object.entries(attributes).forEach(([name, value]) => child.setAttribute(name, value));
    svg.appendChild(child);
  });
  return svg;
}

function makeTag(text, iconDefinition) {
  const tag = document.createElement("span");
  tag.className = "tag";
  const icon = document.createElement("span");
  icon.className = "tag-icon";
  icon.appendChild(createIcon(iconDefinition));
  tag.appendChild(icon);
  tag.appendChild(document.createTextNode(text));
  return tag;
}

export function createResultsView({ results, summary, resultsToolbar, pagination, getPage, onPageChange }) {
  let lastPaginationData = null;

  function beginReorder() {
    results.style.minHeight = `${Math.ceil(results.getBoundingClientRect().height)}px`;
    results.classList.add("is-reordering");
  }

  function endReorder() {
    results.classList.remove("is-reordering");
    results.style.removeProperty("min-height");
  }

  function renderPagination(data) {
    lastPaginationData = data;
    pagination.classList.remove("is-loading", "has-error");
    pagination.removeAttribute("aria-busy");
    const hasPrevious = data.hasPreviousPage;
    const hasNext = data.hasNextPage;
    if (!hasPrevious && !hasNext) {
      pagination.hidden = true;
      pagination.replaceChildren();
      return;
    }

    pagination.hidden = false;
    pagination.replaceChildren();
    const previous = document.createElement("button");
    previous.type = "button";
    previous.className = "btn-small";
    previous.textContent = "Previous";
    previous.setAttribute("aria-label", "Previous page of results");
    previous.disabled = !hasPrevious;
    previous.addEventListener("click", () => onPageChange(getPage() - 1));

    const label = document.createElement("span");
    label.className = "pagination-label";
    label.setAttribute("role", "status");
    label.setAttribute("aria-live", "polite");
    label.textContent = `Page ${data.page}`;

    const next = document.createElement("button");
    next.type = "button";
    next.className = "btn-small";
    next.textContent = "Next";
    next.setAttribute("aria-label", "Next page of results");
    next.disabled = !hasNext;
    next.addEventListener("click", () => onPageChange(getPage() + 1));
    pagination.append(previous, label, next);
  }

  function setPageLoading() {
    pagination.hidden = false;
    pagination.classList.add("is-loading");
    pagination.classList.remove("has-error");
    pagination.setAttribute("aria-busy", "true");
    pagination.querySelectorAll("button").forEach(button => {
      button.disabled = true;
    });
    const label = pagination.querySelector(".pagination-label");
    setAnimatedStatus(label, "Loading");
  }

  function endPageLoading(errorMessage = "") {
    if (lastPaginationData) renderPagination(lastPaginationData);
    if (!errorMessage) return;
    pagination.classList.add("has-error");
    const label = pagination.querySelector(".pagination-label");
    label.textContent = errorMessage;
  }

  function render(data, { skipEntrance = false } = {}) {
    const matches = data.matches;
    results.replaceChildren();
    const showingStart = matches.length ? (data.page - 1) * data.pageSize + 1 : 0;
    const showingEnd = showingStart + matches.length - 1;
    const pageText = matches.length
      ? `Showing ${showingStart}-${showingEnd} matching showtime${matches.length === 1 ? "" : "s"}`
      : "No matching showtimes";
    const summaryText = `${pageText} - checked ${data.checkedSeatMaps} seat map${data.checkedSeatMaps === 1 ? "" : "s"} from ${data.checkedShowtimes} candidate showtime${data.checkedShowtimes === 1 ? "" : "s"}.`;
    setSummary(summary, summaryText, !matches.length);
    resultsToolbar.hidden = !matches.length;
    renderPagination(data);

    if (!matches.length) {
      const hint = document.createElement("div");
      hint.className = "empty-state";
      const hintText = document.createElement("p");
      hintText.textContent = "Try widening the time range, seat area, or dates.";
      hint.appendChild(hintText);
      results.appendChild(hint);
      return;
    }

    matches.forEach((match, index) => {
      const item = document.createElement("article");
      item.className = "result";
      if (skipEntrance) item.classList.add("no-enter-animation");
      item.setAttribute("aria-label", `${match.movieTitle} at ${match.theatre.name}, ${formatNiceDate(match.date)} ${match.displayTime}`);
      if (!skipEntrance) item.style.animationDelay = `${Math.min(index, 5) * 80}ms`;
      const body = document.createElement("div");
      body.className = "result-body";

      if (match.poster) {
        const poster = document.createElement("img");
        poster.className = "result-poster";
        poster.src = match.poster;
        poster.alt = `${match.movieTitle} poster`;
        poster.loading = "lazy";
        poster.addEventListener("error", () => poster.remove());
        body.appendChild(poster);
      }

      const details = document.createElement("div");
      details.className = "result-details";
      const top = document.createElement("div");
      top.className = "result-top";
      const title = document.createElement("h2");
      title.className = "result-title";
      title.textContent = match.theatre.name;
      const distance = document.createElement("span");
      distance.className = "result-distance";
      distance.textContent = `${match.theatre.distanceMiles.toFixed(1)} mi`;
      top.append(title, distance);
      details.appendChild(top);

      if (match.theatre.address) {
        const address = document.createElement("p");
        address.className = "result-addr";
        address.textContent = match.theatre.address;
        details.appendChild(address);
      }

      const movie = document.createElement("p");
      movie.className = "result-movie";
      movie.textContent = match.movieTitle;
      details.appendChild(movie);
      const submetaParts = [match.rating, match.runtime, match.genres.join(", ")].filter(Boolean);
      if (submetaParts.length) {
        const submeta = document.createElement("p");
        submeta.className = "result-submeta";
        submeta.textContent = submetaParts.join("  ·  ");
        details.appendChild(submeta);
      }

      const meta = document.createElement("div");
      meta.className = "result-meta";
      meta.appendChild(makeTag(match.format, ICON_FILM));
      meta.appendChild(makeTag(`${formatNiceDate(match.date)} · ${match.displayTime}`, ICON_CALENDAR));
      const open = document.createElement("span");
      open.className = "result-open";
      open.textContent = `${match.seatMap.availableSeatCount} of ${match.seatMap.totalSeatCount} seats open`;
      meta.appendChild(open);
      details.appendChild(meta);
      body.appendChild(details);
      item.appendChild(body);

      if (match.amenities) {
        const amenities = document.createElement("p");
        amenities.className = "result-amenities";
        amenities.textContent = match.amenities;
        item.appendChild(amenities);
      }
      item.appendChild(renderRealSeatMap(match.seatMap));
      if (match.ticketUrl) {
        const link = document.createElement("a");
        link.className = "buy-btn";
        link.href = match.ticketUrl;
        link.textContent = "Get tickets";
        link.target = "_blank";
        link.rel = "noreferrer";
        link.addEventListener("click", logTicketClick);
        item.appendChild(link);
      }
      results.appendChild(item);
    });
  }

  return { beginReorder, endReorder, endPageLoading, render, setPageLoading };
}
