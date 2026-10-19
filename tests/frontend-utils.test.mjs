import { addDays, formatNiceDate, getJson, todayString } from "../src/frontend/scripts/utils.js";
import { compactShareUrl, getSearchShareData } from "../src/frontend/scripts/sharing.js";
import { logSharedLinkVisit, logTicketClick } from "../src/frontend/scripts/tracking.js";
import assert from "node:assert/strict";
import { inflateSync } from "node:zlib";
import test from "node:test";

test("native share content describes the specific showtime and requested seats", () => {
  const url = "https://movieseatfinder.com/?movie=Test+Movie&adjacentSeats=3";
  const match = {
    date: "2026-09-06",
    displayTime: "7 PM",
    format: "IMAX",
    movieTitle: "Test Movie",
    theatre: { name: "Shared Cinema" },
  };
  assert.deepEqual(getSearchShareData(url, match), {
    text: `Test Movie\n3 seats together · ${formatNiceDate(match.date)} · 7 PM · IMAX · Shared Cinema`,
    title: "Test Movie — Movie Seat Finder",
    url,
  });
  assert.match(
    getSearchShareData(url.replace("adjacentSeats=3", "adjacentSeats=1"), match).text,
    /1 seat · /u,
  );
});

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: () => body,
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

test("getJson preserves API error routing metadata", async () => {
  await withFetch(
    () => Promise.resolve(response(400, '{"error":"ZIP not found.","code":"location"}')),
    async () => {
      await assert.rejects(
        getJson("/api/theatres"),
        (error) =>
          error.message === "ZIP not found." && error.code === "location" && error.status === 400,
      );
    },
  );
});

test("getJson never exposes a JSON parser error for an HTML server failure", async () => {
  await withFetch(
    () => Promise.resolve(response(500, "Internal Server Error")),
    async () => {
      await assert.rejects(getJson("/api/search"), /search service is temporarily unavailable/u);
    },
  );
});

test("date helpers preserve calendar dates in the browser's timezone", () => {
  const originalTimezone = process.env.TZ;
  try {
    process.env.TZ = "America/New_York";
    assert.equal(todayString(new Date("2026-07-23T00:26:31Z")), "2026-07-22");

    process.env.TZ = "Pacific/Kiritimati";
    assert.equal(addDays("2026-07-22", 7), "2026-07-29");
  } finally {
    if (originalTimezone === undefined) {
      delete process.env.TZ;
    } else {
      process.env.TZ = originalTimezone;
    }
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
    logSharedLinkVisit();
    assert.deepEqual(calls, ["/api/events/ticket-click", "/api/events/shared-link-visit"]);
  } finally {
    if (originalNavigator === undefined) {
      delete globalThis.navigator;
    } else {
      Object.defineProperty(globalThis, "navigator", {
        configurable: true,
        value: originalNavigator,
      });
    }
  }
});

test("ticket click tracking falls back when a beacon cannot be queued", async () => {
  const originalNavigator = globalThis.navigator;
  const calls = [];
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: () => false },
  });
  try {
    await withFetch(
      (url, options) => {
        calls.push({ options, url });
        return Promise.resolve(response(204, ""));
      },
      () => {
        logTicketClick();
        logSharedLinkVisit();
      },
    );
    assert.deepEqual(calls, [
      { options: { keepalive: true, method: "POST" }, url: "/api/events/ticket-click" },
      { options: { keepalive: true, method: "POST" }, url: "/api/events/shared-link-visit" },
    ]);
  } finally {
    if (originalNavigator === undefined) {
      delete globalThis.navigator;
    } else {
      Object.defineProperty(globalThis, "navigator", {
        configurable: true,
        value: originalNavigator,
      });
    }
  }
});

test("compact share links remove precise coordinates and unknown data before compression", () => {
  const url = new URL("https://movieseatfinder.com/");
  url.search = new URLSearchParams({
    adjacentSeats: "3",
    endDate: "2026-10-25",
    excludeAccessible: "1",
    format: "IMAX",
    lat: "40.75012345",
    lon: "-73.99098765",
    movie: "Spirited Away — 千と千尋",
    privateNote: "must never be shared",
    radius: "25",
    seatGrid: Array.from({ length: 225 }, (_, i) => `${Math.floor(i / 15)}:${i % 15}`).join(","),
    startDate: "2026-10-18",
    zip: "10001",
  }).toString();
  const compact = new URL(compactShareUrl(url.href, "showtime-123"));
  const decoded = inflateSync(Buffer.from(compact.pathname.slice(3), "base64url")).toString("utf8");
  const params = new URLSearchParams(decoded);
  assert.equal(params.has("lat"), false);
  assert.equal(params.has("lon"), false);
  assert.equal(params.has("privateNote"), false);
  assert.equal(params.get("zip"), "10001");
  assert.equal(params.get("movie"), "Spirited Away — 千と千尋");
  const bitmap = Buffer.from(params.get("seatGrid"), "base64url");
  assert.equal(bitmap.length, 29);
  assert.deepEqual([...bitmap], [...new Uint8Array(28).fill(255), 1]);
  assert.equal(params.get("showtime"), "showtime-123");
  assert.equal(params.get("shared"), "1");
  assert.ok(compact.href.length < url.href.length / 2);
});

test("sharing requires a resolved ZIP instead of encoding precise coordinates", () => {
  assert.throws(
    () => compactShareUrl("https://movieseatfinder.com/?lat=40&lon=-73&movie=Test", "abc"),
    /resolved ZIP/u,
  );
});
