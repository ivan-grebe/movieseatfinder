import { closeCombo, setupCombo } from "./combo.js";
import { elements } from "./dom.js?v=20260801-stable-sorting";
import { createFormatPicker } from "./format-picker.js";
import { createResultsView } from "./results.js?v=20260801-loading-feedback-2";
import { createSeatGrid } from "./seat-grid.js";
import { setAnimatedStatus, setButtonBusy, setStatus, setSummary, startLoadingStages } from "./ui.js?v=20260801-loading-feedback-2";
import { addDays, debounce, formatNiceDate, getJson, todayString } from "./utils.js";

const {
  searchForm, zipInput, useLocationButton, locationStatus, radiusInput, radiusStatus,
  startDateInput, endDateInput, theatreStatus, theatreInput, theatreMenu, movieStatus,
  movieInput, movieMenu, formatOptions, formatStatus, startTimeInput, endTimeInput,
  adjacentSeatsInput, excludeAccessibleInput, seatPreferenceGrid, selectCenterGridButton,
  clearGridButton, gridStatus, searchButton, summary, resultsToolbar, sortInput, sortStatus,
  results, pagination,
} = elements;

const PAGE_SIZE = 20;
const MAX_DATE_RANGE_DAYS = 14;
let theatres = [];
let movies = [];
let preciseLocation = null;
let currentPage = 1;
let theatreLoadSequence = 0;
let movieLoadSequence = 0;
let formatLoadSequence = 0;
let searchLoadSequence = 0;
let reorderScrollY = null;

const formatPicker = createFormatPicker(formatOptions);
const seatGrid = createSeatGrid(seatPreferenceGrid, gridStatus, selectCenterGridButton, clearGridButton);
const resultsView = createResultsView({
  results,
  summary,
  resultsToolbar,
  pagination,
  pageSize: PAGE_SIZE,
  getPage: () => currentPage,
  onPageChange: page => {
    const previousPage = currentPage;
    currentPage = page;
    runSearch({ pageChange: true, previousPage });
  },
});

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

function baseParams() {
  return locationParams(new URLSearchParams({
    zip: zipInput.value.trim(),
    radius: radiusInput.value,
    theatre: theatreInput.value.trim(),
    startDate: startDateInput.value,
    endDate: endDateInput.value,
  }));
}

async function loadTheatres() {
  const loadSequence = ++theatreLoadSequence;
  if (!hasSearchLocation() || !enforceRadius()) {
    theatres = [];
    setStatus(theatreStatus, "");
    return;
  }

  setStatus(theatreStatus, "Loading theatres…", "loading");
  try {
    const params = locationParams(new URLSearchParams({
      zip: zipInput.value.trim(),
      radius: radiusInput.value,
    }));
    const data = await getJson(`/api/theatres?${params}`);
    if (loadSequence !== theatreLoadSequence) return;
    theatres = data.theatres || [];
    setStatus(theatreStatus, `${theatres.length} theatres found near ${data.place}.`, "success");
    closeCombo(theatreInput, theatreMenu);
  } catch (error) {
    if (loadSequence !== theatreLoadSequence) return;
    theatres = [];
    setStatus(theatreStatus, error.message, "error");
  }
}

async function loadMovies() {
  const loadSequence = ++movieLoadSequence;
  if (!hasSearchLocation() || !enforceRadius()) {
    movies = [];
    formatPicker.setOptions([]);
    setStatus(movieStatus, "");
    return;
  }

  setStatus(movieStatus, "Loading movies for selected dates…", "loading");
  const currentMovie = movieInput.value;
  movies = [];
  try {
    const data = await getJson(`/api/movies?${baseParams()}`);
    if (loadSequence !== movieLoadSequence) return;
    movies = data.movies || [];
    movieInput.value = currentMovie;
    setStatus(movieStatus, `${movies.length} movies showing ${formatNiceDate(startDateInput.value)} – ${formatNiceDate(endDateInput.value)}.`, "success");
    closeCombo(movieInput, movieMenu);
  } catch (error) {
    if (loadSequence !== movieLoadSequence) return;
    setStatus(movieStatus, error.message, "error");
  }
}

async function loadFormats() {
  const movieTitle = movieInput.value.trim();
  const loadSequence = ++formatLoadSequence;
  setStatus(formatStatus, "");
  if (!movieTitle || !hasSearchLocation() || !enforceRadius()) {
    formatPicker.setOptions([]);
    return;
  }

  try {
    setStatus(formatStatus, "Loading formats…", "loading");
    const params = baseParams();
    params.set("movie", movieTitle);
    const data = await getJson(`/api/formats?${params}`);
    if (loadSequence !== formatLoadSequence || movieInput.value.trim() !== movieTitle) return;
    const formats = data.formats || [];
    formatPicker.setOptions(formats);
    setStatus(formatStatus, `${formats.length} format${formats.length === 1 ? "" : "s"} for this movie.`, "success");
  } catch (error) {
    if (loadSequence !== formatLoadSequence || movieInput.value.trim() !== movieTitle) return;
    formatPicker.setOptions([]);
    setStatus(formatStatus, error.message, "error");
  }
}

async function search() {
  currentPage = 1;
  await runSearch();
}

function finishReorder({ restoreScroll = true } = {}) {
  if (reorderScrollY === null) return;
  const scrollY = reorderScrollY;
  reorderScrollY = null;
  resultsView.endReorder();
  sortInput.disabled = false;
  sortStatus.textContent = "";
  if (restoreScroll) window.requestAnimationFrame(() => window.scrollTo(window.scrollX, scrollY));
}

async function runSearch({ reorder = false, pageChange = false, previousPage = currentPage } = {}) {
  if (!reorder) finishReorder({ restoreScroll: false });
  syncEndDateBounds();
  const movieTitle = movieInput.value.trim();
  if (!hasSearchLocation()) {
    if (pageChange) currentPage = previousPage;
    reportRequiredField(zipInput, "Enter a ZIP code or allow location access first.");
    return;
  }
  if (!enforceRadius(true)) {
    if (pageChange) currentPage = previousPage;
    return;
  }
  if (!movieTitle) {
    if (pageChange) currentPage = previousPage;
    reportRequiredField(movieInput, "Choose a movie first.");
    return;
  }

  const loadSequence = ++searchLoadSequence;
  let stopLoadingStages = () => {};
  if (reorder) {
    reorderScrollY = window.scrollY;
    resultsView.beginReorder();
    sortInput.disabled = true;
    setAnimatedStatus(sortStatus, "Reordering");
  } else {
    sortInput.disabled = true;
    if (!pageChange) setSummary(summary, "", false);
    if (pageChange) resultsView.setPageLoading();
    stopLoadingStages = startLoadingStages(stage => {
      if (loadSequence !== searchLoadSequence) return;
      setButtonBusy(searchButton, true, stage);
    });
  }
  let reorderError = "";
  try {
    const params = baseParams();
    params.set("movie", movieTitle);
    params.set("format", formatPicker.value());
    params.set("startTime", startTimeInput.value);
    params.set("endTime", endTimeInput.value);
    params.set("adjacentSeats", adjacentSeatsInput.value);
    params.set("page", currentPage);
    params.set("pageSize", PAGE_SIZE);
    params.set("excludeAccessible", excludeAccessibleInput.checked ? "1" : "0");
    params.set("sort", sortInput.value);
    const selectedCells = seatGrid.values();
    if (selectedCells.length) params.set("seatGrid", selectedCells.join(","));
    const data = await getJson(`/api/search?${params}`);
    if (loadSequence !== searchLoadSequence) return;
    resultsView.render(data, { skipEntrance: reorder });
  } catch (error) {
    if (loadSequence !== searchLoadSequence) return;
    if (reorder) reorderError = "Try again";
    else if (pageChange) {
      const failedPage = currentPage;
      currentPage = previousPage;
      resultsView.endPageLoading(`Couldn't load page ${failedPage}`);
    } else setSummary(summary, error.message, true);
  } finally {
    stopLoadingStages();
    if (loadSequence === searchLoadSequence) {
      if (reorder) {
        finishReorder();
        sortStatus.textContent = reorderError;
      } else {
        setButtonBusy(searchButton, false);
        sortInput.disabled = false;
      }
    }
  }
}

function applyQueryParams() {
  const params = new URLSearchParams(window.location.search);
  const inputParams = {
    zip: zipInput,
    radius: radiusInput,
    theatre: theatreInput,
    movie: movieInput,
    startDate: startDateInput,
    endDate: endDateInput,
    startTime: startTimeInput,
    endTime: endTimeInput,
    adjacentSeats: adjacentSeatsInput,
    sort: sortInput,
  };
  Object.entries(inputParams).forEach(([name, input]) => {
    if (params.has(name)) input.value = params.get(name);
  });
  if (params.has("excludeAccessible")) {
    excludeAccessibleInput.checked = params.get("excludeAccessible") === "1";
  }
  if (params.has("seatGrid")) seatGrid.select(params.get("seatGrid").split(","));
  if (params.has("format")) {
    formatPicker.select(params.get("format").split(","));
    formatPicker.setOptions(formatPicker.values().filter(format => format !== "any"));
  }
  return params.has("movie");
}

function syncEndDateBounds() {
  const today = todayString();
  startDateInput.min = today;
  if (startDateInput.value && startDateInput.value < today) startDateInput.value = today;
  endDateInput.min = startDateInput.value;
  endDateInput.max = addDays(startDateInput.value, MAX_DATE_RANGE_DAYS);
  if (endDateInput.value && endDateInput.value < startDateInput.value) endDateInput.value = startDateInput.value;
  if (endDateInput.value && endDateInput.value > endDateInput.max) endDateInput.value = endDateInput.max;
}

async function refreshTheatresAndMovies() {
  if (!hasSearchLocation() || !hasValidRadius()) {
    theatreLoadSequence += 1;
    movieLoadSequence += 1;
    formatLoadSequence += 1;
    theatres = [];
    movies = [];
    formatPicker.setOptions([]);
    [theatreStatus, movieStatus, formatStatus].forEach(status => setStatus(status, ""));
    return;
  }
  await loadTheatres();
  await loadMovies();
  if (movieInput.value.trim()) await loadFormats();
}

function requestLocation() {
  if (!navigator.geolocation) {
    setStatus(locationStatus, "Location isn't available in this browser. Enter a ZIP code to search.", "error");
    return;
  }
  setStatus(locationStatus, "Requesting your location…", "loading");
  useLocationButton.disabled = true;
  navigator.geolocation.getCurrentPosition(
    position => {
      preciseLocation = position.coords;
      zipInput.value = "";
      zipInput.setCustomValidity("");
      setStatus(locationStatus, "Using your precise location for this search. It is not saved.", "success");
      useLocationButton.disabled = false;
      refreshTheatresAndMovies();
    },
    () => {
      preciseLocation = null;
      setStatus(locationStatus, "Location access was blocked. Enter a ZIP code to search.", "error");
      useLocationButton.disabled = false;
    },
    { enableHighAccuracy: true, maximumAge: 300000, timeout: 10000 },
  );
}

const autoRefresh = debounce(refreshTheatresAndMovies, 650);

function bindEvents() {
  setupCombo(theatreInput, theatreMenu, () => theatres, theatre => theatre.name, async () => {
    await loadMovies();
    if (movieInput.value.trim()) await loadFormats();
  });
  setupCombo(movieInput, movieMenu, () => movies, movie => movie.title, loadFormats);
  searchForm.addEventListener("submit", event => {
    event.preventDefault();
    search();
  });
  sortInput.addEventListener("change", () => {
    currentPage = 1;
    runSearch({ reorder: true });
  });
  useLocationButton.addEventListener("click", requestLocation);
  zipInput.addEventListener("input", () => {
    zipInput.setCustomValidity("");
    if (zipInput.value.trim()) {
      preciseLocation = null;
      setStatus(locationStatus, "Searching from your ZIP code.");
    } else if (!preciseLocation) {
      setStatus(locationStatus, "Enter a ZIP code or use your location.");
    }
    autoRefresh();
  });
  radiusInput.addEventListener("input", () => {
    enforceRadius();
    autoRefresh();
  });
  [startDateInput, endDateInput].forEach(input => input.addEventListener("change", () => {
    syncEndDateBounds();
    autoRefresh();
  }));
  movieInput.addEventListener("change", () => {
    movieInput.setCustomValidity("");
    if (movieInput.value.trim()) loadFormats();
  });
  movieInput.addEventListener("input", () => movieInput.setCustomValidity(""));
}

async function initialize() {
  const today = todayString();
  startDateInput.value = today;
  endDateInput.value = addDays(today, 7);
  startDateInput.min = today;
  syncEndDateBounds();
  formatPicker.setOptions([]);
  bindEvents();

  const shouldSearchFromUrl = applyQueryParams();
  syncEndDateBounds();
  if (hasSearchLocation() && enforceRadius()) {
    await Promise.all([loadTheatres(), loadMovies()]);
    if (shouldSearchFromUrl) {
      await loadFormats();
      search();
    }
  } else {
    setStatus(locationStatus, "Enter a ZIP code or use your location.");
  }
}

initialize();
