function localDateString(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function todayString(now = new Date()) {
  return localDateString(now);
}

export function addDays(dateString, days) {
  const [year, month, day] = dateString.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  date.setDate(date.getDate() + days);
  return localDateString(date);
}

export function formatNiceDate(dateString) {
  const date = new Date(dateString + "T00:00:00");
  if (Number.isNaN(date.getTime())) return dateString;
  return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

export async function getJson(url) {
  let response;
  try {
    response = await fetch(url);
  } catch {
    throw new Error("Couldn't reach the search service. Check your connection and try again.");
  }
  const body = await response.text();
  let data;
  try {
    data = body ? JSON.parse(body) : {};
  } catch {
    throw new Error(
      response.ok
        ? "The server returned an unreadable response. Please try again."
        : "The search service is temporarily unavailable. Please try again."
    );
  }
  if (!response.ok) throw new Error(data.error || "Request failed.");
  return data;
}
