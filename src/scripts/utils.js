function localDateString(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");

  return `${year}-${month}-${day}`;
}

export function todayString() {
  return localDateString(new Date());
}

export function addDays(dateString, days) {
  const [year, month, day] = dateString.split("-").map(Number);
  const date = new Date(year, month - 1, day);

  date.setDate(date.getDate() + days);
  return localDateString(date);
}

export function formatNiceDate(dateString) {
  const date = new Date(`${dateString}T00:00:00`);

  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    weekday: "short",
  });
}

/** Track the latest run of an async task, so stale results can be ignored. */
/** One run at a time: starting or cancelling a run aborts the previous run's signal. */
export function createRunGuard() {
  let controller = new AbortController();

  return {
    cancel() {
      controller.abort();
    },
    start() {
      controller.abort();
      controller = new AbortController();
      return controller.signal;
    },
  };
}

export function debounce(fn, ms) {
  let timer = 0;

  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export async function getJson(url, signal) {
  const response = await fetch(url, {
    signal: signal,
  }).catch(() => {
    throw new Error("Couldn't reach the search service. Check your connection and try again.");
  });
  const data = await response.json().catch(() => {
    if (response.ok) {
      throw new Error("The server returned an unreadable response. Please try again.");
    }

    throw new Error("The search service is temporarily unavailable. Please try again.");
  });

  if (!response.ok) {
    const error = new Error(data.error);

    error.code = data.code;
    throw error;
  }

  return data;
}
