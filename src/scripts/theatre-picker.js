import { createRunGuard, getJson } from "./utils.js";
import { createFilterDropdown } from "./filter-dropdown.js";
import { setStatus } from "./ui.js";

/** The Theatres dropdown and the nearby-theatre lookup that fills it. */
export function createTheatrePicker({
  container,
  meta,
  status,
  onBusyChange,
  onChange,
  onLoadError,
}) {
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

  function setBusy(busy) {
    filter.setBusy(busy);
    onBusyChange();
  }

  /** Empty the list, cancelling any lookup in flight. */
  function clear() {
    load.cancel();
    filter.setOptions([]);
    setBusy(false);
    setStatus(meta, "");
    setStatus(status, "");
  }

  /**
   * Load theatres near the area. Resolves true when listed, false on failure, or null when
   * a newer lookup replaced this one.
   */
  async function loadNear(areaParams) {
    const signal = load.start();

    setBusy(true);
    setStatus(meta, "Loading…", "loading");
    setStatus(status, "");
    try {
      const data = await getJson(`/api/theatres?${areaParams}`, signal);

      if (signal.aborted) {
        return null;
      }

      filter.setOptions(data.theatres.map((theatre) => theatre.name));
      setBusy(false);
      setStatus(meta, `${data.theatres.length} nearby`);
      return true;
    } catch (error) {
      if (signal.aborted) {
        return null;
      }

      filter.setOptions([]);
      setBusy(false);
      setStatus(meta, "");
      onLoadError(error, status);
      return false;
    }
  }

  /** Show the dropdown as waiting while ZIP, radius or date edits settle. */
  function waitForReload() {
    load.cancel();
    setStatus(meta, "");
    setBusy(true);
  }

  /** Add the theatre choice to request params, as an include or exclude list. */
  function appendParams(params) {
    const { include, exclude } = filter.params();

    include.forEach((name) => params.append("includeTheatre", name));
    exclude.forEach((name) => params.append("excludeTheatre", name));
    return params;
  }

  function restore(params) {
    filter.select({
      exclude: params.getAll("excludeTheatre"),
      include: params.getAll("includeTheatre"),
    });
  }

  function validate() {
    if (filter.isEmpty()) {
      setStatus(status, "Pick at least one theatre.", "error");
      return false;
    }

    return true;
  }

  return {
    appendParams: appendParams,
    clear: clear,
    isBusy: filter.isBusy,
    loadNear: loadNear,
    restore: restore,
    validate: validate,
    waitForReload: waitForReload,
  };
}
