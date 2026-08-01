import assert from "node:assert/strict";
import test from "node:test";

import { addDays, getJson, todayString } from "../frontend/utils.js";
import { groupMatchesByTheatre } from "../frontend/results.js";
import { logTicketClick } from "../frontend/tracking.js";

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => body,
  };
}

async function withFetch(fakeFetch, run) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fakeFetch;
  try {
    await run();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

test("getJson returns a successful JSON response", async () => {
  await withFetch(async () => response(200, '{"movies":[]}'), async () => {
    assert.deepEqual(await getJson("/api/movies"), { movies: [] });
  });
});

test("getJson surfaces API error messages", async () => {
  await withFetch(async () => response(400, '{"error":"Enter a movie title."}'), async () => {
    await assert.rejects(getJson("/api/search"), /Enter a movie title/);
  });
});

test("getJson never exposes a JSON parser error for an HTML server failure", async () => {
  await withFetch(async () => response(500, "Internal Server Error"), async () => {
    await assert.rejects(
      getJson("/api/search"),
      /search service is temporarily unavailable/,
    );
  });
});

test("date helpers preserve calendar dates in the browser's timezone", () => {
  const originalTimezone = process.env.TZ;
  try {
    process.env.TZ = "America/New_York";
    assert.equal(todayString(new Date("2026-07-23T00:26:31Z")), "2026-07-22");

    process.env.TZ = "Pacific/Kiritimati";
    assert.equal(addDays("2026-07-22", 7), "2026-07-29");
  } finally {
    if (originalTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = originalTimezone;
  }
});

test("ticket click tracking uses a non-blocking beacon", () => {
  const originalNavigator = globalThis.navigator;
  const calls = [];
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: (url) => calls.push(url) > 0 },
  });
  try {
    logTicketClick();
    assert.deepEqual(calls, ["/api/events/ticket-click"]);
  } finally {
    if (originalNavigator === undefined) delete globalThis.navigator;
    else Object.defineProperty(globalThis, "navigator", { configurable: true, value: originalNavigator });
  }
});

test("ticket click tracking falls back when a beacon cannot be queued", async () => {
  const originalNavigator = globalThis.navigator;
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: () => false },
  });
  try {
    await withFetch(async (url, options) => {
      assert.equal(url, "/api/events/ticket-click");
      assert.deepEqual(options, { method: "POST", keepalive: true });
      return response(204, "");
    }, async () => logTicketClick());
  } finally {
    if (originalNavigator === undefined) delete globalThis.navigator;
    else Object.defineProperty(globalThis, "navigator", { configurable: true, value: originalNavigator });
  }
});

test("matching showtimes are grouped by theatre without changing their order", () => {
  const first = { theatre: { name: "Cinema One", address: "1 Main St" }, time: "18:00" };
  const second = { theatre: { name: "Cinema One", address: "1 Main St" }, time: "18:15" };
  const third = { theatre: { name: "Cinema Two", address: "2 Main St" }, time: "19:00" };
  const matches = [first, second, third];
  const groups = groupMatchesByTheatre(matches);

  assert.deepEqual(groups, [
    { theatre: first.theatre, matches: [first, second] },
    { theatre: third.theatre, matches: [third] },
  ]);
  assert.deepEqual(groups.flatMap(group => group.matches), matches);
});

test("nonconsecutive showtimes share one theatre group", () => {
  const first = { theatre: { name: "Cinema One", address: "1 Main St" }, time: "18:00" };
  const second = { theatre: { name: "Cinema Two", address: "2 Main St" }, time: "18:15" };
  const third = { theatre: { name: "Cinema One", address: "1 Main St" }, time: "19:00" };
  const matches = [first, second, third];
  const groups = groupMatchesByTheatre(matches);

  assert.deepEqual(groups, [
    { theatre: first.theatre, matches: [first, third] },
    { theatre: second.theatre, matches: [second] },
  ]);
});

test("theatres with the same name at different addresses stay separate", () => {
  const first = { theatre: { name: "Neighborhood Cinema", address: "1 Main St" } };
  const second = { theatre: { name: "Neighborhood Cinema", address: "99 Broad St" } };

  assert.equal(groupMatchesByTheatre([first, second]).length, 2);
});
