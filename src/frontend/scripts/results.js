import { setAnimatedStatus, setSummary } from "./ui.js";
import { formatNiceDate } from "./utils.js";
import { logTicketClick } from "./tracking.js";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const ICON_FILM = [
  ["rect", { height: "16", rx: "2", width: "18", x: "3", y: "4" }],
  ["path", { d: "M7 4v16M17 4v16M3 9h4M3 14h4M17 9h4M17 14h4" }],
];
const ICON_CALENDAR = [
  ["rect", { height: "16", rx: "2", width: "18", x: "3", y: "4.5" }],
  ["path", { d: "M3 9h18M8 2.5v4M16 2.5v4" }],
];
const ACCESSIBLE_SEAT_TYPES = new Set(["wheelchair", "companion"]);

function createLegendItem(label, className) {
  const item = document.createElement("span");
  item.className = "legend-item";
  const swatch = document.createElement("span");
  let swatchClass = "legend-swatch";
  if (className) {
    swatchClass += ` ${className}`;
  }
  swatch.className = swatchClass;
  item.append(swatch, document.createTextNode(label));
  return item;
}

function renderRealSeatMap(seatMap, accessibleSeatsExcluded) {
  const layout = seatMap.layout;
  const hasBackground = Boolean(layout.backgroundSvg);

  const wrapper = document.createElement("div");
  wrapper.className = "real-seat-map";
  const title = document.createElement("div");
  title.className = "real-seat-map-title";
  const titleLabel = document.createElement("span");
  titleLabel.textContent = "Live Fandango seat map";
  const titleCount = document.createElement("span");
  titleCount.textContent = `${seatMap.availableSeatCount} available / ${seatMap.totalSeatCount} total`;
  title.append(titleLabel, titleCount);
  wrapper.append(title);

  if (!hasBackground) {
    const screen = document.createElement("div");
    screen.className = "real-screen";
    screen.title = "Screen";
    screen.textContent = "SCREEN";
    wrapper.append(screen);
  }

  const stage = document.createElement("div");
  stage.className = "real-seat-map-stage";
  if (hasBackground) {
    stage.classList.add("has-background");
  }
  const { width, height } = layout;
  stage.style.aspectRatio = `${width} / ${height}`;
  stage.style.minHeight = "150px";
  if (hasBackground) {
    const background = document.createElement("img");
    background.className = "real-seat-map-background";
    background.alt = "";
    background.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(layout.backgroundSvg)}`;
    stage.append(background);
  }

  layout.seats.forEach((seat) => {
    const node = document.createElement("span");
    const isAvailable = seat.status === "A";
    let accessibilityType = "";
    if (ACCESSIBLE_SEAT_TYPES.has(seat.type)) {
      accessibilityType = seat.type;
    }
    const isExcluded = Boolean(isAvailable && accessibilityType && accessibleSeatsExcluded);
    let availabilityClass = "unavailable";
    if (isAvailable && !isExcluded) {
      availabilityClass = "available";
    }
    node.className = `real-seat ${availabilityClass}`;
    if (accessibilityType) {
      node.classList.add("accessible");
    }
    if (seat.matched) {
      node.classList.add("matched");
    }
    let availabilityLabel = "unavailable";
    if (isAvailable) {
      availabilityLabel = "available";
    }
    let exclusionLabel = "";
    if (isExcluded) {
      exclusionLabel = "excluded by filter";
    }
    node.title = [seat.id || "Seat", availabilityLabel, accessibilityType, exclusionLabel]
      .filter(Boolean)
      .join(" - ");
    node.style.left = `${((Number(seat.x) || 0) / width) * 100}%`;
    node.style.top = `${((Number(seat.y) || 0) / height) * 100}%`;
    node.style.width = `${(Math.max(Number(seat.width) || 1, 1) / width) * 100}%`;
    node.style.height = `${(Math.max(Number(seat.height) || 1, 1) / height) * 100}%`;
    stage.append(node);
  });
  wrapper.append(stage);

  const legend = document.createElement("div");
  legend.className = "seat-map-legend";
  legend.append(createLegendItem("Available", ""));
  if (!accessibleSeatsExcluded) {
    legend.append(createLegendItem("Accessible", "accessible"));
  }
  let unavailableLabel = "Unavailable";
  if (accessibleSeatsExcluded) {
    unavailableLabel = "Unavailable / excluded";
  }
  legend.append(
    createLegendItem(unavailableLabel, "unavailable"),
    createLegendItem("Matches", "matched"),
  );
  wrapper.append(legend);
  return wrapper;
}

function createIcon(definition) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  Object.entries({
    fill: "none",
    stroke: "currentColor",
    "stroke-linecap": "round",
    "stroke-linejoin": "round",
    "stroke-width": "2",
    viewBox: "0 0 24 24",
  }).forEach(([name, value]) => svg.setAttribute(name, value));
  definition.forEach(([tagName, attributes]) => {
    const child = document.createElementNS(SVG_NAMESPACE, tagName);
    Object.entries(attributes).forEach(([name, value]) => child.setAttribute(name, value));
    svg.append(child);
  });
  return svg;
}

function makeTag(text, iconDefinition) {
  const tag = document.createElement("span");
  tag.className = "tag";
  const icon = document.createElement("span");
  icon.className = "tag-icon";
  icon.append(createIcon(iconDefinition));
  tag.append(icon);
  tag.append(document.createTextNode(text));
  return tag;
}

function pluralSuffix(count) {
  if (count === 1) {
    return "";
  }
  return "s";
}

export function createResultsView({
  results,
  summary,
  resultsToolbar,
  pagination,
  getPage,
  onPageChange,
}) {
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
    pagination.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });
    const label = pagination.querySelector(".pagination-label");
    setAnimatedStatus(label, "Loading");
  }

  function endPageLoading(errorMessage = "") {
    if (lastPaginationData) {
      renderPagination(lastPaginationData);
    }
    if (!errorMessage) {
      return;
    }
    pagination.classList.add("has-error");
    const label = pagination.querySelector(".pagination-label");
    label.textContent = errorMessage;
  }

  function render(data, { skipEntrance = false } = {}) {
    const matches = data.matches;
    results.replaceChildren();
    let showingStart = 0;
    if (matches.length > 0) {
      showingStart = (data.page - 1) * data.pageSize + 1;
    }
    const showingEnd = showingStart + matches.length - 1;
    let pageText = "No matching showtimes";
    if (matches.length > 0) {
      pageText = `Showing ${showingStart}-${showingEnd} matching showtime${pluralSuffix(matches.length)}`;
    }
    const summaryText = `${pageText} - checked ${data.checkedSeatMaps} seat map${pluralSuffix(data.checkedSeatMaps)} from ${data.checkedShowtimes} candidate showtime${pluralSuffix(data.checkedShowtimes)}.`;
    setSummary(summary, summaryText, matches.length === 0);
    resultsToolbar.hidden = matches.length === 0;
    renderPagination(data);

    if (matches.length === 0) {
      const hint = document.createElement("div");
      hint.className = "empty-state";
      const hintText = document.createElement("p");
      hintText.textContent = "Try widening the time range, seat area, or dates.";
      hint.append(hintText);
      results.append(hint);
      return;
    }

    matches.forEach((match, index) => {
      const item = document.createElement("article");
      item.className = "result";
      if (skipEntrance) {
        item.classList.add("no-enter-animation");
      }
      item.setAttribute(
        "aria-label",
        `${match.movieTitle} at ${match.theatre.name}, ${formatNiceDate(match.date)} ${match.displayTime}`,
      );
      if (!skipEntrance) {
        item.style.animationDelay = `${Math.min(index, 5) * 80}ms`;
      }
      const body = document.createElement("div");
      body.className = "result-body";

      if (match.poster) {
        const poster = document.createElement("img");
        poster.className = "result-poster";
        poster.src = match.poster;
        poster.alt = `${match.movieTitle} poster`;
        poster.loading = "lazy";
        poster.addEventListener("error", () => poster.remove());
        body.append(poster);
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
      details.append(top);

      if (match.theatre.address) {
        const address = document.createElement("p");
        address.className = "result-addr";
        address.textContent = match.theatre.address;
        details.append(address);
      }

      const movie = document.createElement("p");
      movie.className = "result-movie";
      movie.textContent = match.movieTitle;
      details.append(movie);
      const submetaParts = [match.rating, match.runtime, match.genres.join(", ")].filter(Boolean);
      if (submetaParts.length > 0) {
        const submeta = document.createElement("p");
        submeta.className = "result-submeta";
        submeta.textContent = submetaParts.join("  ·  ");
        details.append(submeta);
      }

      const meta = document.createElement("div");
      meta.className = "result-meta";
      meta.append(makeTag(match.format, ICON_FILM));
      meta.append(makeTag(`${formatNiceDate(match.date)} · ${match.displayTime}`, ICON_CALENDAR));
      const open = document.createElement("span");
      open.className = "result-open";
      open.textContent = `${match.seatMap.availableSeatCount} of ${match.seatMap.totalSeatCount} seats open`;
      meta.append(open);
      details.append(meta);
      body.append(details);
      item.append(body);

      if (match.amenities) {
        const amenities = document.createElement("p");
        amenities.className = "result-amenities";
        amenities.textContent = match.amenities;
        item.append(amenities);
      }
      item.append(renderRealSeatMap(match.seatMap, data.accessibleSeatsExcluded));
      if (match.ticketUrl) {
        const link = document.createElement("a");
        link.className = "buy-btn";
        link.href = match.ticketUrl;
        link.textContent = "Get tickets";
        link.target = "_blank";
        link.rel = "noreferrer";
        link.addEventListener("click", logTicketClick);
        item.append(link);
      }
      results.append(item);
    });
  }

  return { beginReorder, endPageLoading, endReorder, render, setPageLoading };
}