import {
  StreamState,
  narrowedResults,
  pageOf,
  resortedResults,
  streamSearch,
} from "./result-stream.js";
import { createLoadingIndicator, setSummary, startLoadingStages } from "./ui.js";
import { createResultsView } from "./results.js";
import { getJson } from "./utils.js";

const PAGE_SIZE = 20;
const LOADING_MORE = "Checking more showtimes…";

function rememberSearch(searchUrl, replaceHistory = false) {
  const url = new URL(searchUrl);

  if (globalThis.location.href === url.href) {
    return;
  }

  if (replaceHistory) {
    globalThis.history.replaceState(null, "", url.href);
  } else {
    globalThis.history.pushState(null, "", url.href);
  }
}

function searchUrlFor(params, page, sort, zipCode) {
  const query = new URLSearchParams(params);

  query.set("page", page);
  query.set("sort", sort);
  query.delete("lat");
  query.delete("lon");
  query.set("zip", zipCode);
  const url = new URL("/", globalThis.location.origin);

  url.search = query.toString();
  return url.href;
}

/**
 * Runs searches and keeps the results area in step: one activity at a time (a new search,
 * a page change, a re-sort, or loading a shared showtime), each cancelling the last.
 * Results stream in, so later pages, re-sorts and narrowed searches can often be served
 * from what has already loaded.
 */
export function createSearchFlow({
  pagination,
  results,
  resultsToolbar,
  searchParams,
  setSearchBusy,
  sortInput,
  sortStatus,
  summary,
  validate,
}) {
  let currentPage = 1;
  let activity = {
    kind: "idle",
  };
  // The current search's results: streaming, or derived locally from a finished stream.
  let loaded = null;
  const resultsView = createResultsView({
    getPage: () => currentPage,
    onPageChange: (page) => runPageChange(page),
    pagination: pagination,
    results: results,
    resultsToolbar: resultsToolbar,
    summary: summary,
  });

  function showSpinner(message) {
    sortStatus.replaceChildren(...createLoadingIndicator(message));
  }

  function finishActivity({ pageError = "", restoreScroll = false, sortMessage = "" } = {}) {
    const finished = activity;

    if (finished.kind === "idle") {
      return;
    }

    activity = {
      kind: "idle",
    };
    if (finished.kind === "searching") {
      finished.stopLoadingStages();
      setSearchBusy(false);
    } else if (finished.kind === "paging") {
      resultsView.endPageLoading(pageError);
    } else if (finished.kind === "reordering") {
      resultsView.endReorder();
      if (restoreScroll) {
        globalThis.requestAnimationFrame(() => window.scrollTo(window.scrollX, finished.scrollY));
      }
    }

    sortInput.disabled = false;
    sortStatus.textContent = sortMessage;
  }

  /** Start an activity, returning a check for whether it is still the current one. */
  function startActivity(next) {
    finishActivity();
    activity = next;
    sortInput.disabled = true;
    sortStatus.replaceChildren();
    if (next.kind === "searching") {
      resultsView.endPageLoading();
      next.stopLoadingStages = startLoadingStages((stage) => setSearchBusy(true, stage));
    } else if (next.kind === "paging") {
      resultsView.setPageLoading();
      showSpinner("Loading page");
    } else if (next.kind === "reordering") {
      resultsView.beginReorder();
      showSpinner("Reordering results");
    }

    return () => activity === next;
  }

  /** A page of the current search: from loaded results when they cover it, else the server. */
  async function loadPage(page) {
    const results = loaded;

    await results.until((current) => current.matches.length > page * PAGE_SIZE);
    let data = null;

    if (results.state === StreamState.Complete || results.matches.length > page * PAGE_SIZE) {
      data = pageOf(results, page, PAGE_SIZE);
    } else {
      // The stream stopped at its seat-map cap before reaching this page.
      const query = new URLSearchParams(results.params);

      query.set("page", page);
      query.set("pageSize", PAGE_SIZE);
      query.set("sort", results.sort);
      data = await getJson(`/api/search?${query}`);
    }

    return {
      data: data,
      searchUrl: searchUrlFor(results.params, data.page, results.sort, data.zipCode),
    };
  }

  /** Note that more showtimes are still being checked, until the stream finishes. */
  function showLoadingMore(results) {
    if (results.state !== StreamState.Streaming) {
      return;
    }

    sortStatus.textContent = LOADING_MORE;
    results
      .until(() => false)
      .catch(() => undefined)
      .finally(() => {
        if (activity.kind === "idle" && sortStatus.textContent === LOADING_MORE) {
          sortStatus.textContent = "";
        }
      });
  }

  /** Use results narrowed from the last search when they answer it, else stream a new one. */
  function startResults(params, sort) {
    const narrowed = loaded && narrowedResults(loaded, params);

    loaded?.abort();
    if (narrowed) {
      if (narrowed.sort === sort) {
        loaded = narrowed;
      } else {
        loaded = resortedResults(narrowed, sort);
      }
    } else {
      loaded = streamSearch(params, sort);
    }
  }

  async function runNewSearch({ replaceHistory = false, scrollToResults = false } = {}) {
    if (!validate()) {
      return;
    }

    startResults(searchParams(), sortInput.value);
    const isCurrent = startActivity({
      kind: "searching",
    });

    setSummary(summary, "", false);
    try {
      const { data, searchUrl } = await loadPage(currentPage);

      if (!isCurrent()) {
        return;
      }

      rememberSearch(searchUrl, replaceHistory);
      resultsView.render(data, {
        searchUrl: searchUrl,
      });
    } catch (error) {
      if (!isCurrent()) {
        return;
      }

      resultsView.renderError(error.message);
    }

    finishActivity();
    showLoadingMore(loaded);
    if (scrollToResults) {
      const section = results.closest("section");

      section.focus({
        preventScroll: true,
      });
      section.scrollIntoView({
        behavior: "instant",
        block: "start",
      });
    }
  }

  /** Replace the results on screen with another page of the current search. */
  async function showPage(page, nextActivity, { onFailure, onShown }) {
    const isCurrent = startActivity(nextActivity);

    try {
      const { data, searchUrl } = await loadPage(page);

      if (!isCurrent()) {
        return;
      }

      rememberSearch(searchUrl);
      resultsView.render(data, {
        isUpdate: true,
        searchUrl: searchUrl,
      });
      onShown();
      showLoadingMore(loaded);
    } catch {
      if (isCurrent()) {
        onFailure();
      }
    }
  }

  function runPageChange(page) {
    const previousPage = currentPage;

    currentPage = page;
    return showPage(
      page,
      {
        kind: "paging",
      },
      {
        onFailure: () => {
          currentPage = previousPage;
          finishActivity({
            pageError: `Couldn't load page ${page}`,
          });
        },
        onShown: () => {
          const firstResult = results.firstElementChild;

          firstResult.tabIndex = -1;
          firstResult.focus({
            preventScroll: true,
          });
          firstResult.scrollIntoView({
            behavior: "instant",
            block: "start",
          });
          finishActivity();
        },
      },
    );
  }

  /** Show the current search from page one in a new sort order. */
  function runReorder() {
    currentPage = 1;
    const sort = sortInput.value;
    const resorted = resortedResults(loaded, sort);

    loaded.abort();
    loaded = resorted ?? streamSearch(loaded.params, sort);
    return showPage(
      1,
      {
        kind: "reordering",
        scrollY: window.scrollY,
      },
      {
        onFailure: () =>
          finishActivity({
            restoreScroll: true,
            sortMessage: "Try again",
          }),
        onShown: () =>
          finishActivity({
            restoreScroll: true,
          }),
      },
    );
  }

  /** Show one shared showtime; `applyShared` copies its details into the form. */
  async function loadSharedShowtime(token, applyShared) {
    const isCurrent = startActivity({
      kind: "loadingShared",
    });

    try {
      const data = await getJson(`/api/shared-showtime/${token}`);

      if (!isCurrent()) {
        return;
      }

      const match = data.matches[0];

      applyShared(match, data.preferences);
      const url = new URL("/", globalThis.location.origin);

      url.search = new URLSearchParams(data.preferences).toString();
      resultsView.render(data, {
        searchUrl: url.href,
        shared: true,
      });
      resultsView.focusShowtime(match.showtimeHashCode);
    } catch (error) {
      if (isCurrent()) {
        resultsView.renderError(error.message);
      }
    }

    if (isCurrent()) {
      finishActivity();
      document.body.classList.remove("shared-loading");
    }
  }

  /** Stop whatever the results area is doing, as when leaving the shared view. */
  function stop() {
    finishActivity();
    if (results.childElementCount === 0) {
      setSummary(summary, "", true);
    }
  }

  /** Start a new search from page one. */
  function searchFromStart() {
    currentPage = 1;
    return runNewSearch();
  }

  function setPage(page) {
    if (Number.isSafeInteger(page) && page > 0) {
      currentPage = page;
    }
  }

  return {
    loadSharedShowtime: loadSharedShowtime,
    runNewSearch: runNewSearch,
    runReorder: runReorder,
    searchFromStart: searchFromStart,
    setPage: setPage,
    stop: stop,
  };
}
