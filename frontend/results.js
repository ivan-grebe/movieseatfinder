import { formatNiceDate } from "./utils.js";
import { setSummary } from "./ui.js";

const ICON_FILM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 14h4M17 9h4M17 14h4"/></svg>';
const ICON_CALENDAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9h18M8 2.5v4M16 2.5v4"/></svg>';

function createLegendItem(label, className) {
  const item = document.createElement("span");
  item.className = "legend-item";
  const swatch = document.createElement("span");
  swatch.className = "legend-swatch" + (className ? ` ${className}` : "");
  item.append(swatch, document.createTextNode(label));
  return item;
}

function renderRealSeatMap(seatMap) {
  const layout = seatMap?.layout;
  if (!layout?.seats?.length) return null;
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
  wrapper.appendChild(title);

  if (!hasBackground) {
    const screen = document.createElement("div");
    screen.className = "real-screen";
    screen.title = "Screen";
    screen.textContent = "SCREEN";
    wrapper.appendChild(screen);
  }

  const stage = document.createElement("div");
  stage.className = "real-seat-map-stage";
  if (hasBackground) stage.classList.add("has-background");
  const width = Math.max(Number(layout.width) || 1, 1);
  const height = Math.max(Number(layout.height) || 1, 1);
  stage.style.aspectRatio = `${width} / ${height}`;
  stage.style.minHeight = "150px";
  if (hasBackground) {
    const background = document.createElement("img");
    background.className = "real-seat-map-background";
    background.alt = "";
    background.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(layout.backgroundSvg);
    stage.appendChild(background);
  }

  layout.seats.forEach(seat => {
    const node = document.createElement("span");
    const isAvailable = seat.status === "A";
    node.className = `real-seat ${isAvailable ? "available" : "unavailable"}`;
    if (seat.matched) node.classList.add("matched");
    node.title = [seat.id || "Seat", isAvailable ? "available" : "unavailable"].join(" - ");
    node.style.left = `${((Number(seat.x) || 0) / width) * 100}%`;
    node.style.top = `${((Number(seat.y) || 0) / height) * 100}%`;
    node.style.width = `${(Math.max(Number(seat.width) || 1, 1) / width) * 100}%`;
    node.style.height = `${(Math.max(Number(seat.height) || 1, 1) / height) * 100}%`;
    stage.appendChild(node);
  });
  wrapper.appendChild(stage);

  const legend = document.createElement("div");
  legend.className = "seat-map-legend";
  legend.append(
    createLegendItem("Available", ""),
    createLegendItem("Unavailable", "unavailable"),
    createLegendItem("Matches your filter", "matched"),
  );
  wrapper.appendChild(legend);
  return wrapper;
}

function makeTag(text, iconSvg) {
  const tag = document.createElement("span");
  tag.className = "tag";
  if (iconSvg) {
    const icon = document.createElement("span");
    icon.className = "tag-icon";
    icon.innerHTML = iconSvg;
    tag.appendChild(icon);
  }
  tag.appendChild(document.createTextNode(text));
  return tag;
}

export function createResultsView({ results, summary, pagination, pageSize, getPage, onPageChange }) {
  function renderPagination(data) {
    const hasPrevious = Boolean(data.hasPreviousPage);
    const hasNext = Boolean(data.hasNextPage);
    if (!hasPrevious && !hasNext) {
      pagination.hidden = true;
      pagination.innerHTML = "";
      return;
    }

    pagination.hidden = false;
    pagination.innerHTML = "";
    const previous = document.createElement("button");
    previous.type = "button";
    previous.className = "btn-small";
    previous.textContent = "Previous";
    previous.setAttribute("aria-label", "Previous page of results");
    previous.disabled = !hasPrevious;
    previous.addEventListener("click", () => {
      if (getPage() > 1) onPageChange(getPage() - 1);
    });

    const label = document.createElement("span");
    label.className = "pagination-label";
    label.textContent = `Page ${data.page || getPage()}`;

    const next = document.createElement("button");
    next.type = "button";
    next.className = "btn-small";
    next.textContent = "Next";
    next.setAttribute("aria-label", "Next page of results");
    next.disabled = !hasNext;
    next.addEventListener("click", () => onPageChange(getPage() + 1));
    pagination.append(previous, label, next);
  }

  function render(data) {
    const matches = data.matches || [];
    results.innerHTML = "";
    const showingStart = matches.length ? ((data.page || 1) - 1) * (data.pageSize || pageSize) + 1 : 0;
    const showingEnd = showingStart + matches.length - 1;
    const pageText = matches.length
      ? `Showing ${showingStart}-${showingEnd} matching showtime${matches.length === 1 ? "" : "s"}`
      : "No matching showtimes";
    const summaryText = `${pageText} - checked ${data.checkedSeatMaps} seat map${data.checkedSeatMaps === 1 ? "" : "s"} from ${data.checkedShowtimes} candidate showtime${data.checkedShowtimes === 1 ? "" : "s"}.`;
    setSummary(summary, summaryText, !matches.length);
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
      item.setAttribute("aria-label", `${match.movieTitle} at ${match.theatre.name}, ${formatNiceDate(match.date)} ${match.displayTime}`);
      item.style.animationDelay = `${Math.min(index, 5) * 80}ms`;
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
      const title = document.createElement("h3");
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
      const submetaParts = [match.rating, match.runtime, match.genres?.join(", ")].filter(Boolean);
      if (submetaParts.length) {
        const submeta = document.createElement("p");
        submeta.className = "result-submeta";
        submeta.textContent = submetaParts.join("  ·  ");
        details.appendChild(submeta);
      }

      const meta = document.createElement("div");
      meta.className = "result-meta";
      if (match.format) meta.appendChild(makeTag(match.format, ICON_FILM));
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
      const seatMap = renderRealSeatMap(match.seatMap);
      if (seatMap) item.appendChild(seatMap);
      if (match.ticketUrl) {
        const link = document.createElement("a");
        link.className = "buy-btn";
        link.href = match.ticketUrl;
        link.textContent = "Get tickets";
        link.target = "_blank";
        link.rel = "noreferrer";
        item.appendChild(link);
      }
      results.appendChild(item);
    });
  }

  return { render };
}
