import { closeCombo, setupCombo } from "./combo.js";
import { elements } from "./dom.js";
import { createFormatPicker } from "./format-picker.js";
import { createResultsView } from "./results.js";
import { createSeatGrid } from "./seat-grid.js";
import { setAnimatedStatus, setButtonBusy, setStatus, setSummary, startLoadingStages } from "./ui.js";
import { addDays, debounce, getJson, todayString } from "./utils.js";

const {
  searchForm, zipInput, useLocationButton, locationStatus, radiusInput, radiusStatus,
  startDateInput, endDateInput, theatreMeta, theatreStatus, theatreInput, theatreMenu,
  movieMeta, movieStatus, movieInput, movieMenu, formatOptions, formatMeta, formatStatus,
  startTimeInput, endTimeInput,
  adjacentSeatsInput, excludeAccessibleInput, seatPreferenceGrid, selectCenterGridButton,
  clearGridButton, gridStatus, searchButton, summary, resultsToolbar, sortInput, sortStatus,
  results, pagination,
} = elements;

const PAGE_SIZE = 20;
const MAX_DATE_RANGE_DAYS = 14;
let theatres = [];
let movies = [];
let selectedMovie = null;
let selectedMovieTitle = "";
let movieCombo;
let preciseLocation = null;
let currentPage = 1;
let reorderScrollY = null;

// Tracks the latest run of an async loader so stale responses can be dropped.
function createRunGuard() {
  let current = 0;
  return {
    start() {
      const id = ++current;
      return () => id === current;
    },
    cancel() {
      current += 1;
    },
  };
}

const theatreLoad = createRunGuard();
const movieLoad = createRunGuard();
const formatLoad = createRunGuard();
const searchLoad = createRunGuard();

const formatPicker = createFormatPicker(formatOptions);
const seatGrid = createSeatGrid(seatPreferenceGrid, gridStatus, selectCenterGridButton, clearGridButton);
const resultsView = createResultsView({
  results,
  summary,
  resultsToolbar,
  pagination,
  getPage: () => currentPage,
  onPageChange: page => runPageChange(page),
});

function hasValidZip() {
  return /^\d{5}$/.test(zipInput.value.trim());
}

function normalizedTitle(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function movieWithTitle(title) {
  const normalized = normalizedTitle(title);
  return movies.find(movie => normalizedTitle(movie.title) === normalized) || null;
}

function setMovieCountMeta() {
  setStatus(movieMeta, `${movies.length} showing`);
}

function resetFormats() {
  formatLoad.cancel();
  formatPicker.setOptions([]);
  setStatus(formatMeta, "");
  setStatus(formatStatus, "");
}

function clearMovieSelection({ rememberTitle = false } = {}) {
  selectedMovie = null;
  if (!rememberTitle) selectedMovieTitle = "";
  resetFormats();
}

function selectMovie(movie) {
  selectedMovie = movie;
  selectedMovieTitle = movie.title;
  movieInput.value = movie.title;
  movieInput.setCustomValidity("");
  resetFormats();
  setMovieCountMeta();
  setStatus(movieStatus, "");
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
  setStatus(radiusStatus, valid ? "" : "Use a radius from 1 to 100 miles.", valid ? "" : "error");
  if (!valid && report) radiusInput.reportValidity();
  return valid;
}

function hasSearchBasics() {
  return hasSearchLocation() && enforceRadius();
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

function showLoaderError(error, status) {
  if (error.code === "location") {
    zipInput.setCustomValidity(error.message);
    setStatus(locationStatus, error.message, "error");
    setStatus(status, "");
    return;
  }
  setStatus(status, error.message, "error");
}

async function loadTheatres() {
  const isCurrent = theatreLoad.start();
  if (!hasSearchBasics()) {
    theatres = [];
    setStatus(theatreMeta, "");
    setStatus(theatreStatus, "");
    return false;
  }

  setStatus(theatreMeta, "Loading…", "loading");
  setStatus(theatreStatus, "");
  try {
    const params = locationParams(new URLSearchParams({
      zip: zipInput.value.trim(),
      radius: radiusInput.value,
    }));
    const data = await getJson(`/api/theatres?${params}`);
    if (!isCurrent()) return null;
    theatres = data.theatres;
    zipInput.setCustomValidity("");
    if (!preciseLocation) setStatus(locationStatus, "");
    setStatus(theatreMeta, `${theatres.length} nearby`);
    setStatus(theatreStatus, "");
    closeCombo(theatreInput, theatreMenu);
    return true;
  } catch (error) {
    if (!isCurrent()) return null;
    theatres = [];
    setStatus(theatreMeta, "");
    showLoaderError(error, theatreStatus);
    return false;
  }
}

async function loadMovies() {
  const isCurrent = movieLoad.start();
  if (!hasSearchBasics()) {
    movies = [];
    setStatus(movieMeta, "");
    clearMovieSelection();
    setStatus(movieStatus, "");
    return;
  }

  setStatus(movieMeta, "Loading…", "loading");
  setStatus(movieStatus, "");
  movies = [];
  clearMovieSelection({ rememberTitle: true });
  try {
    const data = await getJson(`/api/movies?${baseParams()}`);
    if (!isCurrent()) return;
    movies = data.movies;
    setMovieCountMeta();
    selectedMovie = movieWithTitle(selectedMovieTitle);
    const typedMovie = movieInput.value.trim();
    if (selectedMovie) {
      selectedMovieTitle = selectedMovie.title;
      movieInput.value = selectedMovie.title;
      movieInput.setCustomValidity("");
      setStatus(movieStatus, "");
      closeCombo(movieInput, movieMenu);
    } else if (typedMovie) {
      selectedMovieTitle = "";
      setStatus(movieStatus, `"${typedMovie}" isn't selected - choose a movie from the list.`, "error");
      if (document.activeElement === movieInput) movieCombo.refresh();
    } else {
      setStatus(movieStatus, "");
      closeCombo(movieInput, movieMenu);
    }
  } catch (error) {
    if (!isCurrent()) return;
    setStatus(movieMeta, "");
    showLoaderError(error, movieStatus);
  }
}

async function loadFormats() {
  const movieTitle = selectedMovie?.title;
  const isCurrent = formatLoad.start();
  setStatus(formatMeta, "");
  setStatus(formatStatus, "");
  if (!movieTitle || !hasSearchBasics()) {
    formatPicker.setOptions([]);
    return;
  }

  try {
    setStatus(formatMeta, "Loading…", "loading");
    const params = baseParams();
    params.set("movie", movieTitle);
    const data = await getJson(`/api/formats?${params}`);
    if (!isCurrent() || selectedMovie?.title !== movieTitle) return;
    const formats = data.formats;
    formatPicker.setOptions(formats);
    setStatus(formatMeta, "");
    setStatus(formatStatus, "");
  } catch (error) {
    if (!isCurrent() || selectedMovie?.title !== movieTitle) return;
    formatPicker.setOptions([]);
    setStatus(formatMeta, "");
    showLoaderError(error, formatStatus);
  }
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

function validateSearchInputs() {
  syncEndDateBounds();
  if (!hasSearchLocation()) {
    reportRequiredField(zipInput, "Enter a ZIP code or allow location access first.");
    return false;
  }
  if (!enforceRadius(true)) return false;
  if (!selectedMovie) {
    reportRequiredField(movieInput, "Select a movie from the list before searching.");
    return false;
  }
  return true;
}

function fetchSearchResults() {
  const params = baseParams();
  params.set("movie", selectedMovie.title);
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
  return getJson(`/api/search?${params}`);
}

async function runNewSearch() {
  finishReorder({ restoreScroll: false });
  if (!validateSearchInputs()) return;
  const isCurrent = searchLoad.start();
  sortInput.disabled = true;
  setSummary(summary, "", false);
  // A fresh search can supersede an in-flight page change; clear any
  // pagination loading state so it cannot outlive that request.
  resultsView.endPageLoading();
  const stopLoadingStages = startLoadingStages(stage => {
    if (isCurrent()) setButtonBusy(searchButton, true, stage);
  });
  try {
    const data = await fetchSearchResults();
    if (!isCurrent()) return;
    resultsView.render(data);
  } catch (error) {
    if (!isCurrent()) return;
    setSummary(summary, error.message, true);
  } finally {
    stopLoadingStages();
    if (isCurrent()) {
      setButtonBusy(searchButton, false);
      sortInput.disabled = false;
    }
  }
}

async function runPageChange(page) {
  finishReorder({ restoreScroll: false });
  if (!validateSearchInputs()) return;
  const previousPage = currentPage;
  currentPage = page;
  const isCurrent = searchLoad.start();
  sortInput.disabled = true;
  resultsView.setPageLoading();
  const stopLoadingStages = startLoadingStages(stage => {
    if (isCurrent()) setButtonBusy(searchButton, true, stage);
  });
  try {
    const data = await fetchSearchResults();
    if (!isCurrent()) return;
    resultsView.render(data);
  } catch {
    if (!isCurrent()) return;
    currentPage = previousPage;
    resultsView.endPageLoading(`Couldn't load page ${page}`);
  } finally {
    stopLoadingStages();
    if (isCurrent()) {
      setButtonBusy(searchButton, false);
      sortInput.disabled = false;
    }
  }
}

async function runReorder() {
  if (!validateSearchInputs()) return;
  const isCurrent = searchLoad.start();
  reorderScrollY = window.scrollY;
  resultsView.beginReorder();
  sortInput.disabled = true;
  setAnimatedStatus(sortStatus, "Reordering");
  let errorMessage = "";
  try {
    const data = await fetchSearchResults();
    if (!isCurrent()) return;
    resultsView.render(data, { skipEntrance: true });
  } catch {
    if (!isCurrent()) return;
    errorMessage = "Try again";
  } finally {
    if (isCurrent()) {
      finishReorder();
      sortStatus.textContent = errorMessage;
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
  if (params.has("movie")) selectedMovieTitle = params.get("movie").trim();
  // An unknown sort value would otherwise leave the select showing no option.
  if (sortInput.selectedIndex === -1) sortInput.value = "earliest";
  if (params.has("excludeAccessible")) {
    excludeAccessibleInput.checked = params.get("excludeAccessible") === "1";
  }
  if (params.has("seatGrid")) seatGrid.select(params.get("seatGrid").split(","));
  if (params.has("format")) {
    const formats = params.get("format").split(",").filter(Boolean);
    formatPicker.setOptions(formats.filter(format => format !== "any"));
    formatPicker.select(formats);
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
    theatreLoad.cancel();
    movieLoad.cancel();
    formatLoad.cancel();
    theatres = [];
    movies = [];
    setStatus(theatreMeta, "");
    setStatus(movieMeta, "");
    clearMovieSelection();
    [theatreStatus, movieStatus, formatStatus].forEach(status => setStatus(status, ""));
    return;
  }
  clearMovieSelection({ rememberTitle: true });
  const theatresLoaded = await loadTheatres();
  if (theatresLoaded === null) return;
  if (!theatresLoaded) {
    movieLoad.cancel();
    movies = [];
    setStatus(movieMeta, "");
    setStatus(movieStatus, "");
    return;
  }
  await loadMovies();
  if (selectedMovie) await loadFormats();
}

function requestLocation() {
  if (!navigator.geolocation) {
    setStatus(locationStatus, "Location unavailable — use ZIP.", "error");
    return;
  }
  setStatus(locationStatus, "Requesting your location…", "loading");
  useLocationButton.disabled = true;
  navigator.geolocation.getCurrentPosition(
    position => {
      preciseLocation = position.coords;
      zipInput.value = "";
      zipInput.setCustomValidity("");
      setStatus(locationStatus, "Using location · not saved", "");
      useLocationButton.disabled = false;
      refreshTheatresAndMovies();
    },
    () => {
      preciseLocation = null;
      setStatus(locationStatus, "Location blocked — use ZIP.", "error");
      useLocationButton.disabled = false;
    },
    { enableHighAccuracy: true, maximumAge: 300000, timeout: 10000 },
  );
}

const autoRefresh = debounce(refreshTheatresAndMovies, 650);

function queueCriteriaRefresh() {
  setStatus(theatreMeta, "");
  setStatus(movieMeta, "");
  clearMovieSelection({ rememberTitle: true });
  autoRefresh();
}

function bindEvents() {
  setupCombo(theatreInput, theatreMenu, () => theatres, theatre => theatre.name, async () => {
    await loadMovies();
    if (selectedMovie) await loadFormats();
  });
  movieCombo = setupCombo(movieInput, movieMenu, () => movies, movie => movie.title, movie => {
    selectMovie(movie);
    loadFormats();
  });
  searchForm.addEventListener("submit", event => {
    event.preventDefault();
    currentPage = 1;
    runNewSearch();
  });
  sortInput.addEventListener("change", () => {
    currentPage = 1;
    runReorder();
  });
  useLocationButton.addEventListener("click", requestLocation);
  zipInput.addEventListener("input", () => {
    zipInput.setCustomValidity("");
    const zip = zipInput.value.trim();
    if (zip) {
      preciseLocation = null;
      if (/^\d{0,5}$/.test(zip)) {
        setStatus(locationStatus, "");
      } else {
        setStatus(locationStatus, "Use a 5-digit US ZIP code.", "error");
      }
    } else if (!preciseLocation) setStatus(locationStatus, "");
    queueCriteriaRefresh();
  });
  radiusInput.addEventListener("input", () => {
    enforceRadius();
    queueCriteriaRefresh();
  });
  [startDateInput, endDateInput].forEach(input => input.addEventListener("change", () => {
    syncEndDateBounds();
    queueCriteriaRefresh();
  }));
  movieInput.addEventListener("change", () => {
    if (movieInput.value.trim() && !selectedMovie) {
      movieInput.setCustomValidity("Select a movie from the list before searching.");
      setStatus(movieStatus, "Choose one of the movies shown in the list.", "error");
    }
  });
  movieInput.addEventListener("input", () => {
    movieInput.setCustomValidity("");
    clearMovieSelection();
  });
}

async function initialize() {
  const today = todayString();
  startDateInput.value = today;
  endDateInput.value = addDays(today, 7);
  formatPicker.setOptions([]);
  bindEvents();

  const shouldSearchFromUrl = applyQueryParams();
  syncEndDateBounds();
  if (hasSearchBasics()) {
    await Promise.all([loadTheatres(), loadMovies()]);
    if (shouldSearchFromUrl && selectedMovie) {
      await loadFormats();
      currentPage = 1;
      runNewSearch();
    }
  }
}

initialize();
