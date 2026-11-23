import { createLoadingIndicator, setSummary, startLoadingStages } from "./ui.js";
import { createResultsView } from "./results.js";
import { getJson } from "./utils.js";

const PAGE_SIZE = 20;

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

/**
 * Runs searches and keeps the results area in step: one activity at a time (a new search,
 * a page change, a re-sort, or loading a shared showtime), each cancelling the last.
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
  let activity = { kind: "idle" };
  const resultsView = createResultsView({
    getPage: () => currentPage,
    onPageChange: (page) => runPageChange(page),
    pagination,
    results,
    resultsToolbar,
    summary,
  });

  function showSpinner(message) {
    sortStatus.replaceChildren(...createLoadingIndicator(message));
  }

  function finishActivity({ pageError = "", restoreScroll = false, sortMessage = "" } = {}) {
    const finished = activity;
    if (finished.kind === "idle") {
      return;
    }
    activity = { kind: "idle" };
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

  async function fetchResults() {
    const params = searchParams();
    params.set("page", currentPage);
    params.set("pageSize", PAGE_SIZE);
    params.set("sort", sortInput.value);
    const data = await getJson(`/api/search?${params}`);
    params.set("page", data.page);
    params.delete("pageSize");
    params.delete("lat");
    params.delete("lon");
    params.set("zip", data.zipCode);
    const url = new URL("/", globalThis.location.origin);
    url.search = params.toString();
    return { data, searchUrl: url.href };
  }

  async function runNewSearch({ replaceHistory = false, scrollToResults = false } = {}) {
    if (!validate()) {
      return;
    }
    const isCurrent = startActivity({ kind: "searching" });
    setSummary(summary, "", false);
    try {
      const { data, searchUrl } = await fetchResults();
      if (!isCurrent()) {
        return;
      }
      rememberSearch(searchUrl, replaceHistory);
      currentPage = data.page;
      resultsView.render(data, { searchUrl });
    } catch (error) {
      if (!isCurrent()) {
        return;
      }
      resultsView.renderError(error.message);
    }
    finishActivity();
    document.body.classList.remove("shared-loading");
    if (scrollToResults) {
      const section = results.closest("section");
      section.focus({ preventScroll: true });
      section.scrollIntoView({ behavior: "instant", block: "start" });
    }
  }

  async function runPageChange(page) {
    if (!validate()) {
      return;
    }
    const previousPage = currentPage;
    const isCurrent = startActivity({ kind: "paging" });
    currentPage = page;
    try {
      const { data, searchUrl } = await fetchResults();
      if (!isCurrent()) {
        return;
      }
      rememberSearch(searchUrl);
      resultsView.render(data, { isUpdate: true, searchUrl });
      const firstResult = results.firstElementChild;
      firstResult.tabIndex = -1;
      firstResult.focus({ preventScroll: true });
      firstResult.scrollIntoView({ behavior: "instant", block: "start" });
      finishActivity();
    } catch {
      if (!isCurrent()) {
        return;
      }
      currentPage = previousPage;
      finishActivity({ pageError: `Couldn't load page ${page}` });
    }
  }

  /** Re-run the current search from page one in a new sort order. */
  async function runReorder() {
    currentPage = 1;
    if (!validate()) {
      return;
    }
    const isCurrent = startActivity({ kind: "reordering", scrollY: window.scrollY });
    try {
      const { data, searchUrl } = await fetchResults();
      if (!isCurrent()) {
        return;
      }
      rememberSearch(searchUrl);
      resultsView.render(data, { isUpdate: true, searchUrl });
      finishActivity({ restoreScroll: true });
    } catch {
      if (!isCurrent()) {
        return;
      }
      finishActivity({ restoreScroll: true, sortMessage: "Try again" });
    }
  }

  /** Show one shared showtime; `applyShared` copies its details into the form. */
  async function loadSharedShowtime(token, applyShared) {
    results.replaceChildren();
    setSummary(summary, "Loading shared showtime…", true);
    const isCurrent = startActivity({ kind: "loadingShared" });
    try {
      const data = await getJson(`/api/shared-showtime/${token}`);
      if (!isCurrent()) {
        return;
      }
      const match = data.matches[0];
      applyShared(match, data.preferences);
      const url = new URL("/", globalThis.location.origin);
      url.search = new URLSearchParams(data.preferences).toString();
      resultsView.render(data, { searchUrl: url.href, shared: true });
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
    resultsView.endPageLoading();
    if (results.childElementCount === 0) {
      setSummary(summary, "", true);
    }
  }

  /** Start a new search from page one. */
  function searchFromStart(options) {
    currentPage = 1;
    return runNewSearch(options);
  }

  function setPage(page) {
    if (Number.isSafeInteger(page) && page > 0) {
      currentPage = page;
    }
  }

  return { loadSharedShowtime, runNewSearch, runReorder, searchFromStart, setPage, stop };
}
