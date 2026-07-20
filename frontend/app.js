import { addDays, debounce, formatNiceDate, getJson, todayString } from "./utils.js";

const zipInput = document.getElementById("zipInput");
const locationStatus = document.getElementById("locationStatus");
const radiusInput = document.getElementById("radiusInput");
const radiusStatus = document.getElementById("radiusStatus");
const startDateInput = document.getElementById("startDateInput");
const endDateInput = document.getElementById("endDateInput");
const theatreStatus = document.getElementById("theatreStatus");
const theatreInput = document.getElementById("theatreInput");
const theatreMenu = document.getElementById("theatreMenu");
const movieStatus = document.getElementById("movieStatus");
const movieInput = document.getElementById("movieInput");
const movieMenu = document.getElementById("movieMenu");
const formatSelect = document.getElementById("formatSelect");
const formatStatus = document.getElementById("formatStatus");
const startTimeInput = document.getElementById("startTimeInput");
const endTimeInput = document.getElementById("endTimeInput");
const adjacentSeatsInput = document.getElementById("adjacentSeatsInput");
const excludeAccessibleInput = document.getElementById("excludeAccessibleInput");
const seatPreferenceGrid = document.getElementById("seatPreferenceGrid");
const selectCenterGridButton = document.getElementById("selectCenterGridButton");
const clearGridButton = document.getElementById("clearGridButton");
const gridStatus = document.getElementById("gridStatus");
const searchButton = document.getElementById("searchButton");
const summary = document.getElementById("summary");
const results = document.getElementById("results");
const pagination = document.getElementById("pagination");

let theatres = [];
let movies = [];
let preciseLocation = null;
let currentPage = 1;
const pageSize = 20;
const maxDateRangeDays = 14;
const selectedGridCells = new Set();
const gridCellElements = new Map();
let isPaintingGrid = false;
let gridPaintMode = true;
let gridDragStart = null;
let gridSelectionBeforeDrag = new Set();
let gridDragMoved = false;

function setStatus(element, text, state) {
  element.className = (element.id === "gridStatus" ? "status grid-status" : "status") + (state ? " is-" + state : "");
  element.textContent = "";
  if (!text) return;
  if (state === "loading") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    element.appendChild(spinner);
  }
  element.appendChild(document.createTextNode(text));
}

function setSummary(text, muted) {
  summary.className = "summary" + (muted ? " is-muted" : "");
  summary.textContent = "";
  if (muted === "loading") {
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    summary.appendChild(spinner);
  }
  summary.appendChild(document.createTextNode(text));
}

function setButtonBusy(button, busy, busyLabel) {
  if (busy) {
    button.dataset.label = button.dataset.label || button.textContent;
    button.disabled = true;
    button.textContent = "";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    button.appendChild(spinner);
    button.appendChild(document.createTextNode(busyLabel || button.dataset.label));
  } else {
    button.disabled = false;
    if (button.dataset.label) button.textContent = button.dataset.label;
  }
}

function setFormatOptions(formats) {
  formatSelect.innerHTML = "";
  const anyOption = document.createElement("option");
  anyOption.value = "any";
  anyOption.textContent = "Any available format";
  formatSelect.appendChild(anyOption);

  formats.forEach(format => {
    const option = document.createElement("option");
    option.value = format;
    option.textContent = format;
    formatSelect.appendChild(option);
  });
}

function hasValidZip() {
  return /^\d{5}$/.test(zipInput.value.trim());
}

function hasSearchLocation() {
  return hasValidZip() || preciseLocation !== null;
}

function reportRequiredField(input, message) {
  input.setCustomValidity(message);
  input.reportValidity();
}

function hasValidRadius() {
  const radius = Number(radiusInput.value);
  return radiusInput.value.trim() !== "" && Number.isFinite(radius) && radius >= 1 && radius <= 100;
}

function enforceRadius(report = false) {
  const valid = hasValidRadius();
  radiusInput.setCustomValidity(valid ? "" : "Enter a radius between 1 and 100 miles.");
  setStatus(radiusStatus, valid ? "" : "Choose a radius between 1 and 100 miles to load nearby movies.", valid ? "" : "error");
  if (!valid && report) radiusInput.reportValidity();
  return valid;
}

function locationParams(params) {
  if (preciseLocation) {
    params.set("lat", preciseLocation.latitude);
    params.set("lon", preciseLocation.longitude);
  }
  return params;
}

function cellKey(row, col) {
  return row + ":" + col;
}

function updateGridStatus() {
  const count = selectedGridCells.size;
  if (count) {
    setStatus(gridStatus, count + " seat area" + (count === 1 ? "" : "s") + " highlighted.", "success");
  } else {
    setStatus(gridStatus, "No area highlighted — matching seats anywhere.", "");
  }
}

function setGridCell(row, col, selected) {
  const key = cellKey(row, col);
  const cell = gridCellElements.get(key);
  if (selected) {
    selectedGridCells.add(key);
    if (cell) cell.classList.add("selected");
  } else {
    selectedGridCells.delete(key);
    if (cell) cell.classList.remove("selected");
  }
}

function clearGrid() {
  [...selectedGridCells].forEach(key => {
    const [row, col] = key.split(":").map(Number);
    setGridCell(row, col, false);
  });
  updateGridStatus();
}

function selectGridBox(rowStart, rowEnd, colStart, colEnd) {
  clearGrid();
  for (let row = rowStart; row <= rowEnd; row += 1) {
    for (let col = colStart; col <= colEnd; col += 1) {
      setGridCell(row, col, true);
    }
  }
  updateGridStatus();
}

function buildSeatGrid() {
  seatPreferenceGrid.innerHTML = "";
  gridCellElements.clear();
  for (let row = 0; row < 15; row += 1) {
    for (let col = 0; col < 15; col += 1) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "seat-cell";
      button.dataset.cell = cellKey(row, col);
      button.dataset.row = row;
      button.dataset.col = col;
      button.title = "Row band " + (row + 1) + ", column band " + (col + 1);
      button.setAttribute("aria-label", button.title);
      button.addEventListener("click", event => {
        if (event.detail !== 0) return;
        if (isPaintingGrid) return;
        setGridCell(row, col, !selectedGridCells.has(cellKey(row, col)));
        updateGridStatus();
      });
      gridCellElements.set(button.dataset.cell, button);
      seatPreferenceGrid.appendChild(button);
    }
  }
  updateGridStatus();
}

function gridCellFromEvent(event) {
  const element = document.elementFromPoint(event.clientX, event.clientY);
  return element && element.closest ? element.closest(".seat-cell") : null;
}

function restoreGridSelection(snapshot) {
  for (let row = 0; row < 15; row += 1) {
    for (let col = 0; col < 15; col += 1) {
      setGridCell(row, col, snapshot.has(cellKey(row, col)));
    }
  }
}

function applyGridRectangle(cell) {
  if (!cell || !seatPreferenceGrid.contains(cell)) return;
  const current = {
    row: Number(cell.dataset.row),
    col: Number(cell.dataset.col),
  };
  const rowStart = Math.min(gridDragStart.row, current.row);
  const rowEnd = Math.max(gridDragStart.row, current.row);
  const colStart = Math.min(gridDragStart.col, current.col);
  const colEnd = Math.max(gridDragStart.col, current.col);

  restoreGridSelection(gridSelectionBeforeDrag);
  for (let row = rowStart; row <= rowEnd; row += 1) {
    for (let col = colStart; col <= colEnd; col += 1) {
      setGridCell(row, col, gridPaintMode);
    }
  }
  updateGridStatus();
}

function renderCombo(menu, items, input, getLabel, onPick) {
  menu.innerHTML = "";
  if (!items.length) {
    menu.hidden = true;
    return;
  }

  items.forEach(item => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "combo-option";
    button.textContent = getLabel(item);
    button.addEventListener("mousedown", event => {
      event.preventDefault();
      input.value = getLabel(item);
      menu.hidden = true;
      onPick(item);
    });
    menu.appendChild(button);
  });
  menu.hidden = false;
}

function setupCombo(input, menu, source, getLabel, onPick) {
  function update() {
    const query = input.value.trim().toLowerCase();
    const items = source().filter(item => getLabel(item).toLowerCase().includes(query));
    renderCombo(menu, items, input, getLabel, onPick);
  }

  input.addEventListener("focus", update);
  input.addEventListener("input", update);
  input.addEventListener("blur", () => {
    setTimeout(() => {
      menu.hidden = true;
    }, 120);
  });
}

function baseParams() {
  return locationParams(new URLSearchParams({
    zip: zipInput.value.trim(),
    radius: radiusInput.value,
    theatre: theatreInput.value.trim(),
    startDate: startDateInput.value,
    endDate: endDateInput.value
  }));
}

async function loadTheatres() {
  if (!hasSearchLocation() || !enforceRadius()) {
    theatres = [];
    setStatus(theatreStatus, "", "");
    return;
  }

  setStatus(theatreStatus, "Loading theatres…", "loading");

  try {
    const params = locationParams(new URLSearchParams({
      zip: zipInput.value.trim(),
      radius: radiusInput.value
    }));
    const data = await getJson("/api/theatres?" + params.toString());
    theatres = data.theatres || [];
    setStatus(theatreStatus, theatres.length + " theatres found near " + data.place + ".", "success");
    renderCombo(theatreMenu, theatres, theatreInput, theatre => theatre.name, () => {});
    theatreMenu.hidden = true;
  } catch (error) {
    theatres = [];
    setStatus(theatreStatus, error.message, "error");
  }
}

async function loadMovies() {
  if (!hasSearchLocation() || !enforceRadius()) {
    movies = [];
    setFormatOptions([]);
    setStatus(movieStatus, "", "");
    return;
  }

  setStatus(movieStatus, "Loading movies for selected dates…", "loading");
  const currentMovie = movieInput.value;
  movies = [];
  setFormatOptions([]);

  try {
    const data = await getJson("/api/movies?" + baseParams().toString());
    movies = data.movies || [];
    movieInput.value = currentMovie;
    setStatus(movieStatus, movies.length + " movies showing " + formatNiceDate(startDateInput.value) + " – " + formatNiceDate(endDateInput.value) + ".", "success");
    renderCombo(movieMenu, movies, movieInput, movie => movie.title, () => loadFormats());
    movieMenu.hidden = true;
  } catch (error) {
    setStatus(movieStatus, error.message, "error");
  }
}

async function loadFormats() {
  const movieTitle = movieInput.value.trim();
  setFormatOptions([]);
  setStatus(formatStatus, "", "");

  if (!movieTitle || !hasSearchLocation() || !enforceRadius()) return;

  try {
    setStatus(formatStatus, "Loading formats…", "loading");
    const params = baseParams();
    params.set("movie", movieTitle);
    const data = await getJson("/api/formats?" + params.toString());
    const formats = data.formats || [];
    setFormatOptions(formats);
    setStatus(formatStatus, formats.length + " format" + (formats.length === 1 ? "" : "s") + " for this movie.", "success");
  } catch (error) {
    setFormatOptions([]);
    setStatus(formatStatus, error.message, "error");
  }
}

async function search() {
  currentPage = 1;
  await runSearch();
}

async function runSearch() {
  const movieTitle = movieInput.value.trim();

  if (!hasSearchLocation()) {
    reportRequiredField(zipInput, "Enter a ZIP code or allow location access first.");
    return;
  }
  if (!enforceRadius(true)) {
    return;
  }

  if (!movieTitle) {
    reportRequiredField(movieInput, "Choose a movie first.");
    return;
  }

  setSummary("", false);
  setButtonBusy(searchButton, true, "Searching real showtimes and seat maps…");

  try {
    const params = baseParams();
    params.set("movie", movieTitle);
    params.set("format", formatSelect.value);
    params.set("startTime", startTimeInput.value);
    params.set("endTime", endTimeInput.value);
    params.set("adjacentSeats", adjacentSeatsInput.value);
    params.set("page", currentPage);
    params.set("pageSize", pageSize);
    if (excludeAccessibleInput.checked) {
      params.set("excludeAccessible", "1");
    }
    if (selectedGridCells.size) {
      params.set("seatGrid", [...selectedGridCells].join(","));
    }
    const data = await getJson("/api/search?" + params.toString());
    renderResults(data);
  } catch (error) {
    setSummary(error.message, true);
  } finally {
    setButtonBusy(searchButton, false);
  }
}

function createLegendItem(label, className) {
  const item = document.createElement("span");
  item.className = "legend-item";
  const swatch = document.createElement("span");
  swatch.className = "legend-swatch" + (className ? " " + className : "");
  item.appendChild(swatch);
  item.appendChild(document.createTextNode(label));
  return item;
}

function renderRealSeatMap(seatMap) {
  const layout = seatMap && seatMap.layout;
  if (!layout || !layout.seats || !layout.seats.length) {
    return null;
  }
  const hasBackground = Boolean(layout.backgroundSvg);

  const wrapper = document.createElement("div");
  wrapper.className = "real-seat-map";

  const title = document.createElement("div");
  title.className = "real-seat-map-title";
  const titleLabel = document.createElement("span");
  titleLabel.textContent = "Live Fandango seat map";
  const titleCount = document.createElement("span");
  titleCount.textContent = seatMap.availableSeatCount + " available / " + seatMap.totalSeatCount + " total";
  title.appendChild(titleLabel);
  title.appendChild(titleCount);
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
  stage.style.aspectRatio = width + " / " + height;
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
    const isAccessible = seat.type === "wheelchair" || seat.type === "companion";
    const isAvailable = seat.status === "A" && !isAccessible;
    node.className = "real-seat " + (isAvailable ? "available" : "unavailable");
    if (seat.matched) node.classList.add("matched");
    node.title = [
      seat.id || "Seat",
      isAvailable ? "available" : "unavailable"
    ].filter(Boolean).join(" - ");
    node.style.left = ((Number(seat.x) || 0) / width * 100) + "%";
    node.style.top = ((Number(seat.y) || 0) / height * 100) + "%";
    node.style.width = (Math.max(Number(seat.width) || 1, 1) / width * 100) + "%";
    node.style.height = (Math.max(Number(seat.height) || 1, 1) / height * 100) + "%";
    stage.appendChild(node);
  });

  wrapper.appendChild(stage);

  const legend = document.createElement("div");
  legend.className = "seat-map-legend";
  legend.appendChild(createLegendItem("Available", ""));
  legend.appendChild(createLegendItem("Unavailable", "unavailable"));
  legend.appendChild(createLegendItem("Matches your filter", "matched"));
  wrapper.appendChild(legend);

  return wrapper;
}

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
  previous.disabled = !hasPrevious;
  previous.addEventListener("click", () => {
    if (currentPage <= 1) return;
    currentPage -= 1;
    runSearch();
  });

  const label = document.createElement("span");
  label.className = "pagination-label";
  label.textContent = "Page " + (data.page || currentPage);

  const next = document.createElement("button");
  next.type = "button";
  next.className = "btn-small";
  next.textContent = "Next";
  next.disabled = !hasNext;
  next.addEventListener("click", () => {
    currentPage += 1;
    runSearch();
  });

  pagination.appendChild(previous);
  pagination.appendChild(label);
  pagination.appendChild(next);
}

const ICON_FILM = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 4v16M17 4v16M3 9h4M3 14h4M17 9h4M17 14h4"/></svg>';
const ICON_CALENDAR = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4.5" width="18" height="16" rx="2"/><path d="M3 9h18M8 2.5v4M16 2.5v4"/></svg>';

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

function renderResults(data) {
  const matches = data.matches || [];
  results.innerHTML = "";

  const showingStart = matches.length ? ((data.page || 1) - 1) * (data.pageSize || pageSize) + 1 : 0;
  const showingEnd = showingStart + matches.length - 1;
  const pageText = matches.length ? "Showing " + showingStart + "-" + showingEnd + " matching showtime" + (matches.length === 1 ? "" : "s") : "No matching showtimes";
  const summaryText = pageText +
    " - checked " + data.checkedSeatMaps + " seat map" + (data.checkedSeatMaps === 1 ? "" : "s") +
    " from " + data.checkedShowtimes + " candidate showtime" + (data.checkedShowtimes === 1 ? "" : "s") + ".";
  setSummary(summaryText, matches.length ? false : true);
  renderPagination(data);

  if (!matches.length) {
    return;
  }

  matches.forEach((match, index) => {
    const item = document.createElement("article");
    item.className = "result";
    item.style.animationDelay = Math.min(index, 5) * 80 + "ms";

    const body = document.createElement("div");
    body.className = "result-body";

    if (match.poster) {
      const poster = document.createElement("img");
      poster.className = "result-poster";
      poster.src = match.poster;
      poster.alt = match.movieTitle + " poster";
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
    top.appendChild(title);
    const distance = document.createElement("span");
    distance.className = "result-distance";
    distance.textContent = match.theatre.distanceMiles.toFixed(1) + " mi";
    top.appendChild(distance);
    details.appendChild(top);

    if (match.theatre.address) {
      const addr = document.createElement("p");
      addr.className = "result-addr";
      addr.textContent = match.theatre.address;
      details.appendChild(addr);
    }

    const movie = document.createElement("p");
    movie.className = "result-movie";
    movie.textContent = match.movieTitle;
    details.appendChild(movie);

    const submetaParts = [];
    if (match.rating) submetaParts.push(match.rating);
    if (match.runtime) submetaParts.push(match.runtime);
    if (match.genres && match.genres.length) submetaParts.push(match.genres.join(", "));
    if (submetaParts.length) {
      const submeta = document.createElement("p");
      submeta.className = "result-submeta";
      submeta.textContent = submetaParts.join("  ·  ");
      details.appendChild(submeta);
    }

    const meta = document.createElement("div");
    meta.className = "result-meta";
    if (match.format) {
      meta.appendChild(makeTag(match.format, ICON_FILM));
    }
    meta.appendChild(makeTag(formatNiceDate(match.date) + " · " + match.displayTime, ICON_CALENDAR));
    const open = document.createElement("span");
    open.className = "result-open";
    open.textContent = match.seatMap.availableSeatCount + " of " + match.seatMap.totalSeatCount + " seats open";
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

    const realSeatMap = renderRealSeatMap(match.seatMap);
    if (realSeatMap) {
      item.appendChild(realSeatMap);
    }

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

function applyQueryParams() {
  const params = new URLSearchParams(window.location.search);
  if (params.has("zip")) zipInput.value = params.get("zip");
  if (params.has("radius")) radiusInput.value = params.get("radius");
  if (params.has("theatre")) theatreInput.value = params.get("theatre");
  if (params.has("movie")) movieInput.value = params.get("movie");
  if (params.has("startDate")) startDateInput.value = params.get("startDate");
  if (params.has("endDate")) endDateInput.value = params.get("endDate");
  if (params.has("startTime")) startTimeInput.value = params.get("startTime");
  if (params.has("endTime")) endTimeInput.value = params.get("endTime");
  if (params.has("adjacentSeats")) adjacentSeatsInput.value = params.get("adjacentSeats");
  if (params.get("excludeAccessible") === "1") excludeAccessibleInput.checked = true;
  if (params.has("seatGrid")) {
    params.get("seatGrid").split(",").forEach(value => {
      const [row, col] = value.split(":").map(Number);
      if (Number.isInteger(row) && Number.isInteger(col)) {
        setGridCell(row, col, true);
      }
    });
    updateGridStatus();
  }
  if (params.has("format")) {
    setFormatOptions([params.get("format")]);
    formatSelect.value = params.get("format");
  }
  return params.has("movie");
}

function syncEndDateBounds() {
  endDateInput.min = startDateInput.value;
  endDateInput.max = addDays(startDateInput.value, maxDateRangeDays);
  if (endDateInput.value && endDateInput.value < startDateInput.value) {
    endDateInput.value = startDateInput.value;
  }
  if (endDateInput.value && endDateInput.value > endDateInput.max) {
    endDateInput.value = endDateInput.max;
  }
}

async function refreshTheatresAndMovies() {
  if (!hasSearchLocation() || !hasValidRadius()) {
    theatres = [];
    movies = [];
    setFormatOptions([]);
    setStatus(theatreStatus, "", "");
    setStatus(movieStatus, "", "");
    setStatus(formatStatus, "", "");
    return;
  }

  await loadTheatres();
  await loadMovies();
  if (movieInput.value.trim()) await loadFormats();
}

const autoRefresh = debounce(refreshTheatresAndMovies, 650);

startDateInput.value = todayString();
endDateInput.value = addDays(todayString(), 7);
startDateInput.min = todayString();
syncEndDateBounds();
buildSeatGrid();

setupCombo(theatreInput, theatreMenu, () => theatres, theatre => theatre.name, () => loadMovies());
setupCombo(movieInput, movieMenu, () => movies, movie => movie.title, () => loadFormats());

seatPreferenceGrid.addEventListener("pointerdown", event => {
  const cell = event.target.closest(".seat-cell");
  if (!cell) return;
  event.preventDefault();
  isPaintingGrid = true;
  gridPaintMode = !(event.altKey || event.ctrlKey || event.metaKey || event.shiftKey);
  gridDragStart = {
    row: Number(cell.dataset.row),
    col: Number(cell.dataset.col),
  };
  gridSelectionBeforeDrag = new Set(selectedGridCells);
  gridDragMoved = false;
  seatPreferenceGrid.setPointerCapture(event.pointerId);
  applyGridRectangle(cell);
});
seatPreferenceGrid.addEventListener("pointermove", event => {
  if (!isPaintingGrid) return;
  event.preventDefault();
  const cell = gridCellFromEvent(event);
  if (!cell) return;
  if (Number(cell.dataset.row) !== gridDragStart.row || Number(cell.dataset.col) !== gridDragStart.col) {
    gridDragMoved = true;
  }
  applyGridRectangle(cell);
});
seatPreferenceGrid.addEventListener("pointerup", event => {
  if (!isPaintingGrid) return;
  const cell = gridCellFromEvent(event);
  if (!gridDragMoved && gridDragStart) {
    restoreGridSelection(gridSelectionBeforeDrag);
    const key = cellKey(gridDragStart.row, gridDragStart.col);
    setGridCell(gridDragStart.row, gridDragStart.col, gridPaintMode ? !gridSelectionBeforeDrag.has(key) : false);
    updateGridStatus();
  } else if (cell) {
    applyGridRectangle(cell);
  }
  isPaintingGrid = false;
  gridDragStart = null;
  gridSelectionBeforeDrag = new Set();
  gridDragMoved = false;
  if (seatPreferenceGrid.hasPointerCapture(event.pointerId)) {
    seatPreferenceGrid.releasePointerCapture(event.pointerId);
  }
});
seatPreferenceGrid.addEventListener("pointercancel", () => {
  restoreGridSelection(gridSelectionBeforeDrag);
  updateGridStatus();
  isPaintingGrid = false;
  gridDragStart = null;
  gridSelectionBeforeDrag = new Set();
  gridDragMoved = false;
});

searchButton.addEventListener("click", search);
selectCenterGridButton.addEventListener("click", () => selectGridBox(5, 9, 5, 9));
clearGridButton.addEventListener("click", clearGrid);

function requestLocationOnLoad() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    position => {
      preciseLocation = position.coords;
      locationStatus.textContent = "Using your precise location for this search. It is not saved.";
      refreshTheatresAndMovies();
    },
    () => {
      preciseLocation = null;
      locationStatus.textContent = "Location was not shared. Enter a ZIP code to search.";
    },
    { enableHighAccuracy: true, maximumAge: 300000, timeout: 10000 }
  );
}

zipInput.addEventListener("input", () => {
  zipInput.setCustomValidity("");
  if (zipInput.value.trim()) {
    preciseLocation = null;
    locationStatus.textContent = "Searching from your ZIP code.";
  }
  autoRefresh();
});
radiusInput.addEventListener("input", () => {
  enforceRadius();
  autoRefresh();
});
startDateInput.addEventListener("change", () => {
  syncEndDateBounds();
  autoRefresh();
});
endDateInput.addEventListener("change", autoRefresh);
movieInput.addEventListener("change", () => {
  movieInput.setCustomValidity("");
  if (movieInput.value.trim()) loadFormats();
});
movieInput.addEventListener("input", () => movieInput.setCustomValidity(""));

const shouldSearchFromUrl = applyQueryParams();
if (hasSearchLocation() && enforceRadius()) {
  Promise.all([loadTheatres(), loadMovies()]).then(async () => {
    if (shouldSearchFromUrl) {
      await loadFormats();
      const requestedFormat = new URLSearchParams(window.location.search).get("format");
      if (requestedFormat && Array.from(formatSelect.options).some(option => option.value === requestedFormat)) {
        formatSelect.value = requestedFormat;
      }
      search();
    }
  });
}
if (!hasValidZip()) requestLocationOnLoad();
