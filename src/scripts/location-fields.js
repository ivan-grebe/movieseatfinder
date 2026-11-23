import { addDays, todayString } from "./utils.js";
import { loadSavedLocation, saveLocation } from "./saved-location.js";
import { setStatus } from "./ui.js";

const MAX_DATE_RANGE_DAYS = 14;

/**
 * The ZIP, browser location, radius and date fields. Reports edits through callbacks so
 * the page can reload theatres and movies for the new area.
 */
export function createLocationFields({
  elements: {
    endDateInput,
    locationStatus,
    radiusInput,
    radiusStatus,
    startDateInput,
    useLocationButton,
    zipInput,
  },
  onCriteriaChange,
  onLocated,
  onLocationInvalidated,
}) {
  let preciseLocation = null;
  // Fields the user edited since theatres last loaded; saved once they load for them.
  const unsavedEdits = new Set();

  function hasValidZip() {
    return /^\d{5}$/u.test(zipInput.value.trim());
  }

  function hasSearchLocation() {
    return hasValidZip() || preciseLocation !== null;
  }

  function hasValidRadius() {
    const radius = Number(radiusInput.value);

    return (
      radiusInput.value.trim() !== "" && Number.isFinite(radius) && radius >= 1 && radius <= 100
    );
  }

  function enforceRadius(report = false) {
    if (hasValidRadius()) {
      radiusInput.setCustomValidity("");
      setStatus(radiusStatus, "");
      return true;
    }

    radiusInput.setCustomValidity("Enter a radius between 1 and 100 miles.");
    setStatus(radiusStatus, "Use a radius from 1 to 100 miles.", "error");
    if (report) {
      radiusInput.reportValidity();
    }

    return false;
  }

  function hasSearchBasics() {
    return hasSearchLocation() && enforceRadius();
  }

  /** Report a missing location or bad radius the way the browser reports invalid fields. */
  function validate() {
    if (!hasSearchLocation()) {
      zipInput.setCustomValidity("Enter a ZIP code or allow location access first.");
      zipInput.reportValidity();
      return false;
    }

    return enforceRadius(true);
  }

  function syncEndDateBounds() {
    const today = todayString();

    startDateInput.min = today;
    if (startDateInput.value && startDateInput.value < today) {
      startDateInput.value = today;
    }

    endDateInput.min = startDateInput.value;
    endDateInput.max = addDays(startDateInput.value, MAX_DATE_RANGE_DAYS);
    if (endDateInput.value && endDateInput.value < startDateInput.value) {
      endDateInput.value = startDateInput.value;
    }

    if (endDateInput.value && endDateInput.value > endDateInput.max) {
      endDateInput.value = endDateInput.max;
    }
  }

  function withCoordinates(params) {
    if (preciseLocation) {
      params.set("lat", preciseLocation.latitude);
      params.set("lon", preciseLocation.longitude);
    }

    return params;
  }

  /** Where to look for theatres. */
  function areaParams() {
    return withCoordinates(
      new URLSearchParams({
        radius: radiusInput.value,
        zip: zipInput.value.trim(),
      }),
    );
  }

  /** Where and when to look for showtimes. */
  function criteriaParams() {
    return withCoordinates(
      new URLSearchParams({
        endDate: endDateInput.value,
        radius: radiusInput.value,
        startDate: startDateInput.value,
        zip: zipInput.value.trim(),
      }),
    );
  }

  function showError(message) {
    zipInput.setCustomValidity(message);
    setStatus(locationStatus, message, "error");
  }

  /**
   * Clear location errors once theatres load for the current area, and remember the ZIP and
   * radius the user typed now that they are known to work.
   */
  function markResolved() {
    zipInput.setCustomValidity("");
    if (!preciseLocation) {
      setStatus(locationStatus, "");
    }

    const changes = {};

    if (unsavedEdits.has("zip") && hasValidZip()) {
      changes.zip = zipInput.value.trim();
    }

    if (unsavedEdits.has("radius") && hasValidRadius()) {
      changes.radius = Number(radiusInput.value);
    }

    unsavedEdits.clear();
    if (Object.keys(changes).length > 0) {
      saveLocation(changes);
    }
  }

  /** Fill the fields from saved values, then from the URL, so a link's own values win. */
  function restore(params) {
    const { radius, zip } = loadSavedLocation();

    zipInput.value = zip || zipInput.value;
    radiusInput.value = radius || radiusInput.value;
    Object.entries(inputsByParam).forEach(([name, input]) => {
      if (params.has(name)) {
        input.value = params.get(name);
      }
    });
    if (params.has("lat") && params.has("lon")) {
      const latitude = Number(params.get("lat"));
      const longitude = Number(params.get("lon"));

      if (
        Number.isFinite(latitude) &&
        Math.abs(latitude) <= 90 &&
        Number.isFinite(longitude) &&
        Math.abs(longitude) <= 180
      ) {
        preciseLocation = {
          latitude: latitude,
          longitude: longitude,
        };
        setStatus(locationStatus, "Using location");
      }
    }
  }

  function setDates(startDate, endDate) {
    startDateInput.value = startDate;
    endDateInput.value = endDate;
  }

  function requestLocation() {
    if (!navigator.geolocation) {
      setStatus(locationStatus, "Location unavailable — use ZIP.", "error");
      return;
    }

    setStatus(locationStatus, "Requesting your location…", "loading");
    useLocationButton.disabled = true;
    navigator.geolocation.getCurrentPosition(
      (position) => {
        preciseLocation = position.coords;
        zipInput.value = "";
        zipInput.setCustomValidity("");
        setStatus(locationStatus, "Using location", "");
        useLocationButton.disabled = false;
        onLocated();
      },
      () => {
        preciseLocation = null;
        setStatus(locationStatus, "Location blocked — use ZIP.", "error");
        useLocationButton.disabled = false;
      },
      {
        enableHighAccuracy: true,
        maximumAge: 300_000,
        timeout: 10_000,
      },
    );
  }

  const inputsByParam = {
    endDate: endDateInput,
    radius: radiusInput,
    startDate: startDateInput,
    zip: zipInput,
  };

  const today = todayString();

  setDates(today, addDays(today, 7));

  useLocationButton.addEventListener("click", requestLocation);
  zipInput.addEventListener("input", () => {
    onLocationInvalidated();
    zipInput.setCustomValidity("");
    const zip = zipInput.value.trim();

    if (zip) {
      preciseLocation = null;
      if (/^\d{0,5}$/u.test(zip)) {
        setStatus(locationStatus, "");
      } else {
        setStatus(locationStatus, "Use a 5-digit US ZIP code.", "error");
      }
    } else if (!preciseLocation) {
      setStatus(locationStatus, "");
    }

    unsavedEdits.add("zip");
    onCriteriaChange();
  });
  radiusInput.addEventListener("input", () => {
    enforceRadius();
    unsavedEdits.add("radius");
    onCriteriaChange();
  });
  [startDateInput, endDateInput].forEach((input) =>
    input.addEventListener("change", () => {
      syncEndDateBounds();
      onCriteriaChange();
    }),
  );

  return {
    areaParams: areaParams,
    criteriaParams: criteriaParams,
    hasSearchBasics: hasSearchBasics,
    hasSearchLocation: hasSearchLocation,
    hasValidRadius: hasValidRadius,
    markResolved: markResolved,
    restore: restore,
    setDates: setDates,
    showError: showError,
    syncEndDateBounds: syncEndDateBounds,
    validate: validate,
  };
}
