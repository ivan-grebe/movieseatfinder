import { createSeatGrid } from "./seat-grid.js";
import { setStatus } from "./ui.js";

/** The time window, party size, accessible-seat toggle and seat-area grid. */
export function createSeatPreferences({
  adjacentSeatsInput,
  cancelSeatGridButton,
  clearGridButton,
  doneSeatGridButton,
  editSeatGridButton,
  endTimeInput,
  excludeAccessibleInput,
  gridStatus,
  seatPreferenceGrid,
  seatPreferenceHelp,
  selectCenterGridButton,
  startTimeInput,
  timeStatus,
}) {
  const seatGrid = createSeatGrid(
    seatPreferenceGrid,
    gridStatus,
    selectCenterGridButton,
    clearGridButton,
    {
      cancelButton: cancelSeatGridButton,
      doneButton: doneSeatGridButton,
      editButton: editSeatGridButton,
      help: seatPreferenceHelp,
    },
  );

  function validateTimeWindow() {
    const invalid = startTimeInput.value > endTimeInput.value;
    const message = invalid ? "Latest time must be on or after earliest time." : "";
    endTimeInput.setCustomValidity(message);
    endTimeInput.setAttribute("aria-invalid", String(invalid));
    setStatus(timeStatus, message, invalid ? "error" : "");
    return !invalid;
  }

  function validate() {
    if (validateTimeWindow()) {
      return true;
    }
    endTimeInput.reportValidity();
    return false;
  }

  function appendParams(params) {
    params.set("startTime", startTimeInput.value);
    params.set("endTime", endTimeInput.value);
    params.set("adjacentSeats", adjacentSeatsInput.value);
    params.set("excludeAccessible", excludeAccessibleInput.checked ? "1" : "0");
    const selectedCells = seatGrid.values();
    if (selectedCells.length > 0) {
      params.set("seatGrid", selectedCells.join(","));
    }
    return params;
  }

  /** Fill the preferences from a link or a shared showtime's saved preferences. */
  function restore(params) {
    ["adjacentSeats", "startTime", "endTime"].forEach((name) => {
      if (params.has(name)) {
        inputsByParam[name].value = params.get(name);
      }
    });
    if (params.has("excludeAccessible")) {
      excludeAccessibleInput.checked = params.get("excludeAccessible") === "1";
    }
    if (params.has("seatGrid")) {
      seatGrid.select(params.get("seatGrid").split(",").filter(Boolean));
    }
  }

  const inputsByParam = {
    adjacentSeats: adjacentSeatsInput,
    endTime: endTimeInput,
    startTime: startTimeInput,
  };

  [startTimeInput, endTimeInput].forEach((input) =>
    input.addEventListener("input", validateTimeWindow),
  );

  return { appendParams, restore, validate };
}
