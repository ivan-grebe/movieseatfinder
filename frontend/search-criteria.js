import { formatNiceDate } from "./utils.js";

const CENTER_CELLS = new Set(
  Array.from({ length: 5 }, (_, rowOffset) =>
    Array.from({ length: 5 }, (_, columnOffset) => `${rowOffset + 5}:${columnOffset + 5}`),
  ).flat(),
);

function formatTime(time) {
  const [hour, minute] = time.split(":").map(Number);
  if (!Number.isInteger(hour) || !Number.isInteger(minute)) return time;
  const suffix = hour >= 12 ? "PM" : "AM";
  const displayHour = hour % 12 || 12;
  return `${displayHour}:${String(minute).padStart(2, "0")} ${suffix}`;
}

function describeDates(startDate, endDate) {
  if (!startDate) return "Any date";
  if (!endDate || startDate === endDate) return formatNiceDate(startDate);
  return `${formatNiceDate(startDate)} to ${formatNiceDate(endDate)}`;
}

function describeTimes(startTime, endTime) {
  if ((!startTime || startTime === "00:00") && (!endTime || endTime === "23:59")) return "All day";
  if (!startTime && !endTime) return "Any time";
  if (!startTime) return `Before ${formatTime(endTime)}`;
  if (!endTime) return `After ${formatTime(startTime)}`;
  return `${formatTime(startTime)} to ${formatTime(endTime)}`;
}

function describeSeatArea(value) {
  const cells = value ? value.split(",").filter(Boolean) : [];
  if (!cells.length) return "Anywhere in auditorium";
  if (cells.length === CENTER_CELLS.size && cells.every(cell => CENTER_CELLS.has(cell))) return "Center area";
  return "Custom seat area";
}

export function buildSearchCriteria(params) {
  const details = [];
  const location = params.has("lat") ? "Current location" : `ZIP ${params.get("zip")}`;
  const radius = params.get("radius");
  details.push(radius ? `${location}, ${radius} mi radius` : location);

  const theatre = params.get("theatre");
  if (theatre) details.push(theatre);

  details.push(describeDates(params.get("startDate"), params.get("endDate")));
  details.push(describeTimes(params.get("startTime"), params.get("endTime")));

  const formats = params.get("format");
  details.push(!formats || formats === "any" ? "Any format" : formats.split(",").join(", "));

  const seatCount = Math.max(Number(params.get("adjacentSeats")) || 1, 1);
  details.push(seatCount === 1 ? "1 seat" : `${seatCount} seats together`);
  details.push(describeSeatArea(params.get("seatGrid")));
  if (params.get("excludeAccessible") === "1") details.push("Accessible seats excluded");

  return {
    title: params.get("movie") || "Movie search",
    details,
  };
}
