import { initializeAmbientMotion } from "./ambient-motion.js";
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
  movieGroup, movieMeta, movieStatus, movieInput, movieMenu, formatOptions, formatMeta, formatStatus,
  formatGuide, formatGuideButton, formatGuideContent,
  startTimeInput, endTimeInput,
  adjacentSeatsInput, excludeAccessibleInput, preferencesGroup, seatPreferenceHelp, seatPreferenceGrid,
  editSeatGridButton, selectCenterGridButton, clearGridButton, cancelSeatGridButton, doneSeatGridButton,
  gridStatus, searchButton, summary, resultsToolbar, sortInput, sortStatus,
  results, pagination,
} = elements;

const PAGE_SIZE = 20;
const MAX_DATE_RANGE_DAYS = 14;
let theatres = [];
let movies = [];
let selectedTheatre = null;
let selectedTheatreName = "";
let selectedMovie = null;
let selectedMovieTitle = "";
let movieCombo;
let preciseLocation = null;
let currentPage = 1;
let reorderScrollY = null;
let locationReady = false;

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
const seatGrid = createSeatGrid(seatPreferenceGrid, gridStatus, selectCenterGridButton, clearGridButton, {
  help: seatPreferenceHelp,
  editButton: editSeatGridButton,
  cancelButton: cancelSeatGridButton,
  doneButton: doneSeatGridButton,
});
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

function searchInputsReady() {
  return locationReady
    && !theatreInput.hasAttribute("aria-busy")
    && !movieInput.hasAttribute("aria-busy");
}

function setLocationReady(ready) {
  locationReady = ready;
  [movieGroup, preferencesGroup].forEach(group => {
    group.classList.toggle("is-location-locked", !ready);
    group.toggleAttribute("inert", !ready);
    if (ready) group.removeAttribute("aria-disabled");
    else group.setAttribute("aria-disabled", "true");
  });
  if (!searchButton.hasAttribute("aria-busy")) searchButton.disabled = !searchInputsReady();
}

function setSearchButtonBusy(busy, label) {
  setButtonBusy(searchButton, busy, label);
  if (!busy) searchButton.disabled = !searchInputsReady();
}

function normalizedTitle(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
}

function theatreWithName(name) {
  const normalized = normalizedTitle(name);
  return theatres.find(theatre => normalizedTitle(theatre.name) === normalized) || null;
}

function movieWithTitle(title) {
  const normalized = normalizedTitle(title);
  return movies.find(movie => normalizedTitle(movie.title) === normalized) || null;
}

function setComboLoading(input, menu, disabled, busy = disabled) {
  input.disabled = disabled;
  if (busy) input.setAttribute("aria-busy", "true");
  else input.removeAttribute("aria-busy");
  if (disabled) closeCombo(input, menu);
  if (!searchButton.hasAttribute("aria-busy")) searchButton.disabled = !searchInputsReady();
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

function clearTheatreSelection({ rememberName = false } = {}) {
  selectedTheatre = null;
  if (!rememberName) selectedTheatreName = "";
}

function clearMovieSelection({ rememberTitle = false } = {}) {
  selectedMovie = null;
  if (!rememberTitle) selectedMovieTitle = "";
  resetFormats();
}

function selectTheatre(theatre) {
  selectedTheatre = theatre;
  selectedTheatreName = theatre.name;
  theatreInput.value = theatre.name;
  theatreInput.setCustomValidity("");
  setStatus(theatreStatus, "");
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

function resolveTypedTheatre() {
  const typedTheatre = theatreInput.value.trim();
  if (!typedTheatre) {
    clearTheatreSelection();
    theatreInput.setCustomValidity("");
    setStatus(theatreStatus, "");
    return true;
  }
  const exactTheatre = theatreWithName(typedTheatre);
  if (exactTheatre) {
    selectTheatre(exactTheatre);
    return true;
  }
  clearTheatreSelection();
  theatreInput.setCustomValidity("Select an exact theatre from the list before searching.");
  setStatus(theatreStatus, "Choose one of the nearby theatres shown in the list.", "error");
  return false;
}

function resolveTypedMovie() {
  const typedMovie = movieInput.value.trim();
  const exactMovie = movieWithTitle(typedMovie);
  if (exactMovie) {
    selectMovie(exactMovie);
    return true;
  }
  clearMovieSelection();
  movieInput.setCustomValidity("Select an exact movie from the list before searching.");
  setStatus(movieStatus, "Choose one of the movies shown in the list.", "error");
  return false;
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
    theatre: selectedTheatre?.name || "",
    startDate: startDateInput.value,
    endDate: endDateInput.value,
  }));
}

function showLoaderError(error, status) {
  if (error.code === "location") {
    setLocationReady(false);
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
    setComboLoading(theatreInput, theatreMenu, true, false);
    setComboLoading(movieInput, movieMenu, true, false);
    setStatus(theatreMeta, "");
    setStatus(theatreStatus, "");
    return false;
  }

  setComboLoading(theatreInput, theatreMenu, true);
  setComboLoading(movieInput, movieMenu, true);
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
    setLocationReady(true);
    setComboLoading(theatreInput, theatreMenu, false);
    zipInput.setCustomValidity("");
    if (!preciseLocation) setStatus(locationStatus, "");
    setStatus(theatreMeta, `${theatres.length} nearby`);
    const typedTheatre = theatreInput.value.trim();
    selectedTheatre = theatreWithName(selectedTheatreName || typedTheatre);
    if (selectedTheatre) selectTheatre(selectedTheatre);
    else if (typedTheatre) resolveTypedTheatre();
    else setStatus(theatreStatus, "");
    closeCombo(theatreInput, theatreMenu);
    return true;
  } catch (error) {
    if (!isCurrent()) return null;
    theatres = [];
    setComboLoading(theatreInput, theatreMenu, true, false);
    setComboLoading(movieInput, movieMenu, true, false);
    setStatus(theatreMeta, "");
    showLoaderError(error, theatreStatus);
    return false;
  }
}

async function loadMovies() {
  const isCurrent = movieLoad.start();
  if (!hasSearchBasics() || (theatreInput.value.trim() && !selectedTheatre)) {
    movies = [];
    setComboLoading(movieInput, movieMenu, true, false);
    setStatus(movieMeta, "");
    clearMovieSelection();
    setStatus(movieStatus, "");
    return;
  }

  setComboLoading(movieInput, movieMenu, true);
  setStatus(movieMeta, "Loading…", "loading");
  setStatus(movieStatus, "");
  movies = [];
  clearMovieSelection({ rememberTitle: true });
  try {
    const data = await getJson(`/api/movies?${baseParams()}`);
    if (!isCurrent()) return;
    movies = data.movies;
    setComboLoading(movieInput, movieMenu, false);
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
      if (!resolveTypedMovie() && document.activeElement === movieInput) movieCombo.refresh();
    } else {
      setStatus(movieStatus, "");
      closeCombo(movieInput, movieMenu);
    }
  } catch (error) {
    if (!isCurrent()) return;
    setComboLoading(movieInput, movieMenu, true, false);
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
  if (theatreInput.hasAttribute("aria-busy") || movieInput.hasAttribute("aria-busy")) return false;
  if (!selectedTheatre && !resolveTypedTheatre()) {
    reportRequiredField(theatreInput, "Select an exact theatre from the list before searching.");
    return false;
  }
  if (!selectedMovie && !resolveTypedMovie()) {
    reportRequiredField(movieInput, "Select an exact movie from the list before searching.");
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
    if (isCurrent()) setSearchButtonBusy(true, stage);
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
      setSearchButtonBusy(false);
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
    if (isCurrent()) setSearchButtonBusy(true, stage);
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
      setSearchButtonBusy(false);
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
  if (params.has("theatre")) selectedTheatreName = params.get("theatre").trim();
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
    if (!hasSearchLocation()) setLocationReady(false);
    theatreLoad.cancel();
    movieLoad.cancel();
    formatLoad.cancel();
    theatres = [];
    movies = [];
    clearTheatreSelection();
    setComboLoading(theatreInput, theatreMenu, true, false);
    setComboLoading(movieInput, movieMenu, true, false);
    setStatus(theatreMeta, "");
    setStatus(movieMeta, "");
    clearMovieSelection();
    [theatreStatus, movieStatus, formatStatus].forEach(status => setStatus(status, ""));
    return;
  }
  clearTheatreSelection({ rememberName: true });
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
  if (theatreInput.value.trim() && !selectedTheatre) return;
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
      setLocationReady(false);
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
  clearTheatreSelection({ rememberName: true });
  clearMovieSelection({ rememberTitle: true });
  setComboLoading(theatreInput, theatreMenu, true);
  setComboLoading(movieInput, movieMenu, true);
  autoRefresh();
}

function bindEvents() {
  formatGuideButton.addEventListener("click", () => {
    const expanded = formatGuideButton.getAttribute("aria-expanded") !== "true";
    formatGuideButton.setAttribute("aria-expanded", String(expanded));
    formatGuideContent.setAttribute("aria-hidden", String(!expanded));
    formatGuide.classList.toggle("is-open", expanded);
  });
  setupCombo(theatreInput, theatreMenu, () => theatres, theatre => theatre.name, async theatre => {
    selectTheatre(theatre);
    movies = [];
    clearMovieSelection();
    setComboLoading(movieInput, movieMenu, true, false);
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
    setLocationReady(false);
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
  theatreInput.addEventListener("change", async () => {
    if (!resolveTypedTheatre()) return;
    movies = [];
    clearMovieSelection();
    setComboLoading(movieInput, movieMenu, true);
    await loadMovies();
  });
  theatreInput.addEventListener("input", () => {
    theatreInput.setCustomValidity("");
    clearTheatreSelection();
    movieLoad.cancel();
    movies = [];
    clearMovieSelection();
    setStatus(movieMeta, "");
    setStatus(movieStatus, "");
    setComboLoading(movieInput, movieMenu, true, false);
    if (!theatreInput.value.trim()) loadMovies();
  });
  movieInput.addEventListener("change", () => {
    if (movieInput.value.trim() && !selectedMovie && resolveTypedMovie()) loadFormats();
  });
  movieInput.addEventListener("input", () => {
    movieInput.setCustomValidity("");
    clearMovieSelection();
    const exactMovie = movieWithTitle(movieInput.value.trim());
    if (exactMovie) {
      selectMovie(exactMovie);
      loadFormats();
    }
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
    const theatresLoaded = await loadTheatres();
    if (theatresLoaded) await loadMovies();
    if (theatresLoaded && shouldSearchFromUrl && selectedMovie) {
      await loadFormats();
      currentPage = 1;
      runNewSearch();
    }
  }
}

initializeAmbientMotion();
initialize();
