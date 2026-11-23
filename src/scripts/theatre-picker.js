import { createRunGuard, getJson } from "./utils.js";
import { createFilterDropdown } from "./filter-dropdown.js";
import { setStatus } from "./ui.js";

/** The Theatres dropdown and the nearby-theatre lookup that fills it. */
export function createTheatrePicker({ container, meta, status, onChange, onLoadError }) {
  const load = createRunGuard();
  const filter = createFilterDropdown(container, {
    allLabel: (count) => `All ${count} nearby theatres`,
    noun: "theatres",
    onChange: () => {
      setStatus(status, "");
      onChange();
    },
    placeholder: "Enter a location first",
  });

  /** Empty the list, cancelling any lookup in flight. */
  function clear() {
    load.cancel();
    filter.setOptions([]);
    filter.setBusy(false);
    setStatus(meta, "");
    setStatus(status, "");
  }

  /**
   * Load theatres near the area. Resolves true when listed, false on failure, or null when
   * a newer lookup replaced this one.
   */
  async function loadNear(areaParams) {
    const isCurrent = load.start();
    filter.setBusy(true);
    setStatus(meta, "Loading…", "loading");
    setStatus(status, "");
    try {
      const data = await getJson(`/api/theatres?${areaParams}`);
      if (!isCurrent()) {
        return null;
      }
      filter.setOptions(data.theatres.map((theatre) => theatre.name));
      filter.setBusy(false);
      setStatus(meta, `${data.theatres.length} nearby`);
      return true;
    } catch (error) {
      if (!isCurrent()) {
        return null;
      }
      filter.setOptions([]);
      filter.setBusy(false);
      setStatus(meta, "");
      onLoadError(error, status);
      return false;
    }
  }

  /** Show the dropdown as waiting while ZIP, radius or date edits settle. */
  function waitForReload() {
    setStatus(meta, "");
    filter.setBusy(true);
  }

  /** Add the theatre choice to request params, as an include or exclude list. */
  function appendParams(params) {
    const { include, exclude } = filter.params();
    include.forEach((name) => params.append("includeTheatre", name));
    exclude.forEach((name) => params.append("excludeTheatre", name));
    return params;
  }

  function restore(params) {
    const include = params.getAll("includeTheatre");
    if (params.get("theatre")) {
      // Older links name a single theatre.
      include.push(params.get("theatre").trim());
    }
    filter.select({ exclude: params.getAll("excludeTheatre"), include });
  }

  function validate() {
    if (filter.isEmpty()) {
      setStatus(status, "Pick at least one theatre.", "error");
      return false;
    }
    return true;
  }

  return {
    appendParams,
    clear,
    isBusy: filter.isBusy,
    loadNear,
    restore,
    validate,
    waitForReload,
  };
}
