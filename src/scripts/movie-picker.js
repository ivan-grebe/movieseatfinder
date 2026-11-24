import { closeCombo, setupCombo } from "./combo.js";
import { createRunGuard, getJson } from "./utils.js";
import { setStatus } from "./ui.js";

function normalizedTitle(value) {
  return value
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/gu, " ")
    .trim();
}

/**
 * The Movie combobox: loads the movies showing nearby and tracks which one is picked.
 * A remembered title (from a link, or before a reload) is re-picked once movies load.
 */
export function createMoviePicker({
  input,
  menu,
  meta,
  status,
  onBusyChange,
  onLoadError,
  onPick,
  onSelectionChange,
}) {
  const load = createRunGuard();
  let movies = [];
  let selected = null;
  let rememberedTitle = "";

  function movieWithTitle(title) {
    const normalized = normalizedTitle(title);

    return movies.find((movie) => normalizedTitle(movie.title) === normalized);
  }

  /** Disable the input while there is nothing to pick, or mark it busy while loading. */
  function setLoading(disabled, busy = disabled) {
    input.disabled = disabled;
    if (busy) {
      input.setAttribute("aria-busy", "true");
    } else {
      input.removeAttribute("aria-busy");
    }

    if (disabled) {
      closeCombo(input, menu);
    }

    onBusyChange();
  }

  function showCount() {
    setStatus(meta, `${movies.length} showing`);
  }

  function clearSelection({ rememberTitle = false } = {}) {
    selected = null;
    if (!rememberTitle) {
      rememberedTitle = "";
    }

    onSelectionChange();
  }

  function select(movie) {
    selected = movie;
    rememberedTitle = movie.title;
    input.value = movie.title;
    input.setCustomValidity("");
    onSelectionChange();
    showCount();
    setStatus(status, "");
  }

  function resolveTyped() {
    const exactMovie = movieWithTitle(input.value);

    if (exactMovie) {
      select(exactMovie);
      return true;
    }

    clearSelection();
    input.setCustomValidity("Select an exact movie from the list before searching.");
    setStatus(status, "Choose one of the movies shown in the list.", "error");
    return false;
  }

  /** Drop the list and selection because the area or theatres no longer allow a lookup. */
  function disable({ rememberTitle = false } = {}) {
    load.cancel();
    movies = [];
    setLoading(true, false);
    setStatus(meta, "");
    setStatus(status, "");
    clearSelection({
      rememberTitle: rememberTitle,
    });
  }

  /** Show the input as waiting for a lookup, keeping the picked title to re-pick after. */
  function waitForReload() {
    load.cancel();
    setStatus(meta, "");
    clearSelection({
      rememberTitle: true,
    });
    setLoading(true);
  }

  async function loadFor(criteriaParams) {
    const signal = load.start();

    setLoading(true);
    setStatus(meta, "Loading…", "loading");
    setStatus(status, "");
    movies = [];
    clearSelection({
      rememberTitle: true,
    });
    try {
      const data = await getJson(`/api/movies?${criteriaParams}`, signal);

      if (signal.aborted) {
        return;
      }

      movies = data.movies;
      setLoading(false);
      showCount();
      const remembered = movieWithTitle(rememberedTitle);

      if (remembered) {
        select(remembered);
      } else if (input.value.trim()) {
        if (!resolveTyped() && document.activeElement === input) {
          combo.refresh();
        }
      }
    } catch (error) {
      if (signal.aborted) {
        return;
      }

      setLoading(true, false);
      setStatus(meta, "");
      onLoadError(error, status);
    }
  }

  /** Pick this title once movies load (from a link or a shared showtime). */
  function remember(title) {
    rememberedTitle = title;
    input.value = title;
  }

  /** Make sure a movie is picked before searching, reporting it on the input if not. */
  function validate() {
    if (selected || resolveTyped()) {
      return true;
    }

    input.reportValidity();
    return false;
  }

  const combo = setupCombo(
    input,
    menu,
    () => movies,
    (movie) => movie.title,
    (movie) => {
      select(movie);
      onPick();
    },
  );

  input.addEventListener("change", () => {
    if (input.value.trim() && !selected && resolveTyped()) {
      onPick();
    }
  });
  input.addEventListener("input", () => {
    input.setCustomValidity("");
    clearSelection();
    const exactMovie = movieWithTitle(input.value);

    if (exactMovie) {
      select(exactMovie);
      onPick();
    }
  });

  return {
    clearSelection: clearSelection,
    disable: disable,
    isBusy: () => input.hasAttribute("aria-busy"),
    loadFor: loadFor,
    remember: remember,
    selected: () => selected,
    setLoading: setLoading,
    validate: validate,
    waitForReload: waitForReload,
  };
}
