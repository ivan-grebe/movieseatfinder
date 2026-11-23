// The ZIP and radius people search from rarely change, so remember them in this browser.
// Storage can be unavailable (private windows, blocked site data); then nothing is saved.
const STORAGE_KEY = "movieseatfinder.location";

function read() {
  try {
    return JSON.parse(globalThis.localStorage.getItem(STORAGE_KEY)) || {};
  } catch {
    return {};
  }
}

/** The saved ZIP and radius, keeping only values the form would accept. */
export function loadSavedLocation() {
  const { radius, zip } = read();
  const saved = {};

  if (typeof zip === "string" && /^\d{5}$/u.test(zip)) {
    saved.zip = zip;
  }

  if (Number.isFinite(radius) && radius >= 1 && radius <= 100) {
    saved.radius = radius;
  }

  return saved;
}

export function saveLocation(changes) {
  try {
    globalThis.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        ...read(),
        ...changes,
      }),
    );
  } catch {
    // Nothing to do: the form just starts empty next time.
  }
}
