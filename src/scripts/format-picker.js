import { createRunGuard, getJson } from "./utils.js";
import { createFilterDropdown } from "./filter-dropdown.js";
import { setStatus } from "./ui.js";

/** The Format dropdown, filled with the formats the picked movie shows in nearby. */
export function createFormatPicker({ container, meta, status, onLoadError }) {
  const load = createRunGuard();
  // The movie the options were last loaded for; its format choice survives a reload.
  let loadedMovie = "";
  const filter = createFilterDropdown(container, {
    allLabel: () => "Any format",
    noun: "formats",
    onChange: () => setStatus(status, ""),
    placeholder: "Pick a movie first",
  });

  function reset() {
    load.cancel();
    filter.setOptions([]);
    setStatus(meta, "");
    setStatus(status, "");
  }

  /**
   * Load the formats a movie shows in. Picking another movie resets and cancels this.
   * Resolves true when listed, false on failure, or null when a newer load replaced this one.
   */
  async function loadFor(criteriaParams, movieTitle) {
    const signal = load.start();

    setStatus(meta, "Loading…", "loading");
    setStatus(status, "");
    criteriaParams.set("movie", movieTitle);
    try {
      const data = await getJson(`/api/formats?${criteriaParams}`, signal);

      if (signal.aborted) {
        return null;
      }

      if (movieTitle !== loadedMovie) {
        filter.select({
          include: [],
        });
        loadedMovie = movieTitle;
      }

      filter.setOptions(data.formats);
      setStatus(meta, "");
      return true;
    } catch (error) {
      if (signal.aborted) {
        return null;
      }

      filter.setOptions([]);
      setStatus(meta, "");
      onLoadError(error, status);
      return false;
    }
  }

  /** Re-check formats named in a link once the real options have loaded. */
  function restore(params) {
    const formats = params.get("format");

    if (formats) {
      filter.select({
        include: formats.split(",").filter((format) => format && format !== "any"),
      });
    }
  }

  function validate() {
    if (filter.isEmpty()) {
      setStatus(status, "Pick at least one format.", "error");
      return false;
    }

    return true;
  }

  return {
    loadFor: loadFor,
    param() {
      if (filter.isAll()) {
        return "any";
      }

      return filter.checked().join(",");
    },
    reset: reset,
    restore: restore,
    validate: validate,
  };
}
