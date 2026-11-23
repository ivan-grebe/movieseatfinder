import { setButtonBusy, setStatus } from "./ui.js";
import { createFormatPicker } from "./format-picker.js";
import { createLocationFields } from "./location-fields.js";
import { createMoviePicker } from "./movie-picker.js";
import { createSearchFlow } from "./search-flow.js";
import { createSeatPreferences } from "./seat-preferences.js";
import { createTheatrePicker } from "./theatre-picker.js";
import { debounce } from "./utils.js";
import { elements } from "./dom.js";
import { initializeHalReveal } from "./hal-reveal.js";
import { logSharedLinkVisit } from "./tracking.js";
import { setupRegionNotice } from "./region-notice.js";

/*
 * Wires the search form's sections together: each step unlocks the next, and changing an
 * earlier step reloads what depends on it.
 */

const {
  backToSearchButton,
  formatGuide,
  formatGuideButton,
  formatGuideContent,
  movieGroup,
  preferencesGroup,
  searchButton,
  searchForm,
  sortInput,
} = elements;

const sharedToken = /^\/s\/([^/]+)$/u.exec(globalThis.location.pathname)?.[1] || "";
const sharedSearch = Boolean(sharedToken);
let locationReady = false;

const locationFields = createLocationFields({
  elements,
  onCriteriaChange: queueCriteriaRefresh,
  onLocated: () => {
    setLocationReady(false);
    refreshTheatresAndMovies();
  },
  onLocationInvalidated: () => setLocationReady(false),
});
const theatres = createTheatrePicker({
  container: elements.theatreFilterContainer,
  meta: elements.theatreMeta,
  onBusyChange: syncSearchButton,
  onChange: queueTheatreFilterRefresh,
  onLoadError: showLoaderError,
  status: elements.theatreStatus,
});
const movies = createMoviePicker({
  input: elements.movieInput,
  menu: elements.movieMenu,
  meta: elements.movieMeta,
  onBusyChange: syncSearchButton,
  onLoadError: showLoaderError,
  onPick: loadFormats,
  onSelectionChange: () => {
    syncPreferencesReady();
    syncSearchButton();
    formats.reset();
  },
  status: elements.movieStatus,
});
const formats = createFormatPicker({
  container: elements.formatFilterContainer,
  meta: elements.formatMeta,
  onLoadError: showLoaderError,
  status: elements.formatStatus,
});
const preferences = createSeatPreferences(elements);
const search = createSearchFlow({
  ...elements,
  searchParams,
  setSearchBusy: (busy, label) => {
    setButtonBusy(searchButton, busy, label);
    syncSearchButton();
  },
  validate: validateSearchInputs,
});

function setGroupReady(group, ready) {
  group.classList.toggle("is-locked", !ready);
  group.toggleAttribute("inert", !ready);
  if (ready) {
    group.removeAttribute("aria-disabled");
  } else {
    group.setAttribute("aria-disabled", "true");
  }
}

function syncPreferencesReady() {
  setGroupReady(preferencesGroup, locationReady && movies.selected() !== null);
}

/** Enable the search button once inputs are ready, unless a search is running. */
function syncSearchButton() {
  if (searchButton.hasAttribute("aria-busy")) {
    return;
  }
  searchButton.disabled = !(
    locationReady &&
    movies.selected() !== null &&
    !theatres.isBusy() &&
    !movies.isBusy()
  );
}

function setLocationReady(ready) {
  locationReady = ready;
  setGroupReady(movieGroup, ready);
  syncPreferencesReady();
  syncSearchButton();
}

/** Show a lookup error on its field, or on the ZIP when the location itself is the problem. */
function showLoaderError(error, status) {
  if (error.code === "location") {
    setLocationReady(false);
    locationFields.showError(error.message);
    setStatus(status, "");
    return;
  }
  setStatus(status, error.message, "error");
}

/** Where and when to search, narrowed to the chosen theatres. */
function criteriaParams() {
  return theatres.appendParams(locationFields.criteriaParams());
}

async function loadTheatres() {
  if (!locationFields.hasSearchBasics()) {
    theatres.clear();
    movies.setLoading(true, false);
    return false;
  }
  movies.setLoading(true);
  const loaded = await theatres.loadNear(locationFields.areaParams());
  if (loaded) {
    setLocationReady(true);
    locationFields.markResolved();
  } else if (loaded === false) {
    movies.setLoading(true, false);
  }
  return loaded;
}

async function loadMovies() {
  if (locationFields.hasSearchBasics()) {
    await movies.loadFor(criteriaParams());
  } else {
    movies.disable();
  }
}

async function loadFormats() {
  const movie = movies.selected();
  if (!movie || !locationFields.hasSearchBasics()) {
    formats.reset();
    return;
  }
  await formats.loadFor(criteriaParams(), movie.title);
}

async function loadMoviesAndFormats() {
  await loadMovies();
  await loadFormats();
}

async function refreshTheatresAndMovies() {
  if (!locationFields.hasSearchLocation() || !locationFields.hasValidRadius()) {
    if (!locationFields.hasSearchLocation()) {
      setLocationReady(false);
    }
    theatres.clear();
    movies.disable();
    return;
  }
  movies.clearSelection({ rememberTitle: true });
  const theatresLoaded = await loadTheatres();
  if (theatresLoaded === null) {
    return;
  }
  if (!theatresLoaded) {
    movies.disable({ rememberTitle: true });
    return;
  }
  await loadMoviesAndFormats();
}

const autoRefresh = debounce(refreshTheatresAndMovies, 650);

/** Reload theatres and movies once ZIP, radius or date edits settle. */
function queueCriteriaRefresh() {
  theatres.waitForReload();
  movies.waitForReload();
  autoRefresh();
}

const theatreFilterRefresh = debounce(loadMoviesAndFormats, 400);

/** Reload the movies (and formats) nearby once the theatre choice settles. */
function queueTheatreFilterRefresh() {
  movies.waitForReload();
  theatreFilterRefresh();
}

function validateSearchInputs() {
  locationFields.syncEndDateBounds();
  return (
    preferences.validate() &&
    locationFields.validate() &&
    !theatres.isBusy() &&
    !movies.isBusy() &&
    theatres.validate() &&
    movies.validate() &&
    formats.validate()
  );
}

function searchParams() {
  const params = criteriaParams();
  params.set("movie", movies.selected().title);
  params.set("format", formats.param());
  return preferences.appendParams(params);
}

/** Load theatres, movies and formats for the form's current values. */
async function loadSearchOptions() {
  locationFields.syncEndDateBounds();
  if (!locationFields.hasSearchBasics()) {
    return false;
  }
  if (!(await loadTheatres())) {
    return false;
  }
  await loadMovies();
  if (!movies.selected()) {
    return false;
  }
  await loadFormats();
  formats.restore(new URLSearchParams(globalThis.location.search));
  return true;
}

/** Fill the form from a search link. Returns whether the link names a movie to search. */
function applyQueryParams() {
  const params = new URLSearchParams(globalThis.location.search);
  locationFields.restore(params);
  theatres.restore(params);
  preferences.restore(params);
  if (params.has("movie")) {
    movies.remember(params.get("movie"));
  }
  if (params.has("sort")) {
    sortInput.value = params.get("sort");
  }
  if (sortInput.selectedIndex === -1) {
    sortInput.value = "earliest";
  }
  search.setPage(Number(params.get("page")));
  return params.has("movie");
}

function applySharedShowtime(match, sharedPreferences) {
  movies.remember(match.movieTitle);
  locationFields.setDates(match.date, match.date);
  preferences.restore(new URLSearchParams(sharedPreferences));
}

function bindEvents() {
  setupRegionNotice(elements.regionNotice, elements.dismissRegionNoticeButton);
  backToSearchButton.addEventListener("click", async () => {
    search.stop();
    document.body.classList.remove("shared-search", "shared-loading");
    globalThis.history.replaceState(null, "", "/");
    const section = searchForm.closest("section");
    section.tabIndex = -1;
    section.focus({ preventScroll: true });
    globalThis.scrollTo({ behavior: "instant", top: 0 });
    await loadSearchOptions();
  });
  globalThis.addEventListener("popstate", () => globalThis.location.reload());
  window.addEventListener("pageshow", () => setLocationReady(locationReady));
  formatGuideButton.addEventListener("click", () => {
    const expanded = formatGuideButton.getAttribute("aria-expanded") !== "true";
    formatGuideButton.setAttribute("aria-expanded", String(expanded));
    formatGuideContent.setAttribute("aria-hidden", String(!expanded));
    formatGuide.classList.toggle("is-open", expanded);
  });
  searchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    search.searchFromStart();
  });
  sortInput.addEventListener("change", () => search.runReorder());
}

async function initialize() {
  setLocationReady(false);
  bindEvents();
  let shouldSearchFromUrl = false;
  if (!sharedSearch) {
    shouldSearchFromUrl = applyQueryParams();
  }
  if (sharedSearch) {
    logSharedLinkVisit();
    await search.loadSharedShowtime(sharedToken, applySharedShowtime);
    return;
  }
  const optionsLoaded = await loadSearchOptions();
  if (optionsLoaded && shouldSearchFromUrl) {
    await search.runNewSearch({ replaceHistory: true, scrollToResults: true });
  }
}

// oxlint-disable-next-line unicorn/prefer-top-level-await -- Initialization should not block module evaluation.
initialize();
initializeHalReveal();
