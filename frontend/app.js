import { closeCombo, setupCombo } from "./combo.js";
import { elements } from "./dom.js";
import { createFormatPicker } from "./format-picker.js";
import { createResultsView } from "./results.js";
import { createSeatGrid } from "./seat-grid.js";
import { setAnimatedStatus, setButtonBusy, setStatus, setSummary, startLoadingStages } from "./ui.js";
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

// Mirrors the backend's two-way substring match, so the warning only fires
// when a search for the typed title genuinely cannot match anything.
function typedMovieIsShowing(typed) {
  const query = normalizedTitle(typed);
  if (!query) return true;
  return movies.some(movie => {
    const title = normalizedTitle(movie.title);
    return title.includes(query) || query.includes(title);
  });
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

async function loadTheatres() {
  const isCurrent = theatreLoad.start();
  if (!hasSearchBasics()) {
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
    if (!isCurrent()) return;
    theatres = data.theatres;
    setStatus(theatreStatus, `${theatres.length} theatres found near ${data.place}.`, "success");
    closeCombo(theatreInput, theatreMenu);
  } catch (error) {
    if (!isCurrent()) return;
    theatres = [];
    setStatus(theatreStatus, error.message, "error");
  }
}

async function loadMovies() {
  const isCurrent = movieLoad.start();
  if (!hasSearchBasics()) {
    movies = [];
    formatPicker.setOptions([]);
    setStatus(movieStatus, "");
    return;
  }

  setStatus(movieStatus, "Loading movies for selected dates…", "loading");
  movies = [];
  try {
    const data = await getJson(`/api/movies?${baseParams()}`);
    if (!isCurrent()) return;
    movies = data.movies;
    const typedMovie = movieInput.value.trim();
    if (typedMovie && !typedMovieIsShowing(typedMovie)) {
      setStatus(movieStatus, `"${typedMovie}" isn't showing for these dates and theatres - pick a different movie.`, "error");
    } else {
      setStatus(movieStatus, `${movies.length} movies showing ${formatNiceDate(startDateInput.value)} – ${formatNiceDate(endDateInput.value)}.`, "success");
    }
    closeCombo(movieInput, movieMenu);
  } catch (error) {
    if (!isCurrent()) return;
    setStatus(movieStatus, error.message, "error");
  }
}

async function loadFormats() {
  const movieTitle = movieInput.value.trim();
  const isCurrent = formatLoad.start();
  setStatus(formatStatus, "");
  if (!movieTitle || !hasSearchBasics()) {
    formatPicker.setOptions([]);
    return;
  }

  try {
    setStatus(formatStatus, "Loading formats…", "loading");
    const params = baseParams();
    params.set("movie", movieTitle);
    const data = await getJson(`/api/formats?${params}`);
    if (!isCurrent() || movieInput.value.trim() !== movieTitle) return;
    const formats = data.formats;
    formatPicker.setOptions(formats);
    setStatus(formatStatus, `${formats.length} format${formats.length === 1 ? "" : "s"} for this movie.`, "success");
  } catch (error) {
    if (!isCurrent() || movieInput.value.trim() !== movieTitle) return;
    formatPicker.setOptions([]);
    setStatus(formatStatus, error.message, "error");
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
  if (!movieInput.value.trim()) {
    reportRequiredField(movieInput, "Choose a movie first.");
    return false;
  }
  return true;
}

function fetchSearchResults() {
  const params = baseParams();
  params.set("movie", movieInput.value.trim());
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
        setStatus(locationStatus, "Searching from your ZIP code.");
      } else {
        setStatus(locationStatus, "US ZIP codes are 5 digits, like 90210.", "error");
      }
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
  formatPicker.setOptions([]);
  bindEvents();

  const shouldSearchFromUrl = applyQueryParams();
  syncEndDateBounds();
  if (hasSearchBasics()) {
    await Promise.all([loadTheatres(), loadMovies()]);
    if (shouldSearchFromUrl) {
      await loadFormats();
      currentPage = 1;
      runNewSearch();
    }
  } else {
    setStatus(locationStatus, "Enter a ZIP code or use your location.");
  }
}

initialize();
